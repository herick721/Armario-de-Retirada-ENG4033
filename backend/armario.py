"""Regras do armário: o que fazer quando chega algo do Arduino ou do painel.

A ponte serial só traduz linhas de texto; quem decide é este arquivo.

Cada porta destravada pelo PC vira uma "operação pendente" até o sensor
confirmar que ela abriu e fechou:

    DEPOSITO  (painel)   UNLOCK -> DOOR OPEN -> DOOR CLOSED -> pedido DEPOSITADO
    RETIRADA  (teclado)  pedido RETIRADO -> UNLOCK -> OPEN -> CLOSED -> porta LIVRE
    RECOLHA   (painel)   UNLOCK -> OPEN -> CLOSED -> pedido DEVOLVIDO, porta LIVRE

Se a porta não abrir em 15 s (o firmware tranca sozinho), a operação vence:
no depósito a reserva é desfeita; na retirada o código já foi gasto, então
fica um alerta para o lojista.

Porta que abre sem operação pendente é violação: BEEP ALARM e alerta.
"""
import threading
import time
from datetime import timedelta

from backend import codigos, db, estados

PRAZO_RETIRADA = timedelta(hours=48)
TEMPO_PARA_ABRIR = 15  # segundos; igual ao do firmware
TEMPO_PORTA_ABERTA = 60  # segundos; depois disso vira alerta

# Mensagens do LCD (16 colunas, sem acento), definidas na seção da Interface
LCD_REPOUSO = "LCD Digite o codigo|e aperte #"


class OperacaoRecusada(Exception):
    """Pedido do painel que não pode ser feito agora (vira HTTP 409)."""


class Armario:
    def __init__(self, ponte, relogio=time.monotonic):
        self.ponte = ponte
        self.relogio = relogio  # os testes trocam por um relógio falso
        self._lock = threading.Lock()  # thread da serial e rotas da API mexem aqui
        self.pendentes = {}  # porta_id -> {"tipo", "pedido_id", "desde", "abriu"}
        self._aberta_desde = {}  # porta_id -> instante em que abriu (alerta de 60 s)

    # ================= painel =================

    def depositar(self, pedido_id: str) -> dict:
        pedido = db.buscar_pedido(pedido_id)
        if pedido is None:
            raise LookupError("pedido não encontrado")
        if pedido["status"] != "PAGO":
            raise OperacaoRecusada(f"pedido está {pedido['status']}, só dá para depositar pedido PAGO")
        if pedido.get("porta_id") is not None:
            raise OperacaoRecusada(f"depósito já em andamento na porta {pedido['porta_id']}")

        porta = db.reservar_porta_livre(pedido_id)
        if porta is None:
            raise OperacaoRecusada("nenhuma porta livre")
        porta_id = porta["_id"]
        if not db.vincular_porta(pedido_id, porta_id):
            # dois cliques em "depositar" ao mesmo tempo: o outro já levou
            db.liberar_porta(porta_id)
            raise OperacaoRecusada("depósito já em andamento")
        try:
            self._destravar(porta_id, "DEPOSITO", pedido_id, f"LCD Deposito porta {porta_id}|Feche a porta")
        except ConnectionError:
            self._desfazer_deposito(porta_id, pedido_id, "Arduino desconectado")
            raise
        db.registrar_evento("DEPOSITO_INICIADO", porta_id=porta_id, pedido_id=pedido_id)
        return db.buscar_pedido(pedido_id)

    def recolher(self, pedido_id: str) -> dict:
        pedido = db.buscar_pedido(pedido_id)
        if pedido is None:
            raise LookupError("pedido não encontrado")
        if pedido["status"] != "EXPIRADO":
            raise OperacaoRecusada(f"pedido está {pedido['status']}, só dá para recolher pedido EXPIRADO")
        porta_id = pedido["porta_id"]
        if porta_id in self.pendentes:
            raise OperacaoRecusada(f"porta {porta_id} já está destravada")
        self._destravar(porta_id, "RECOLHA", pedido_id, f"LCD Recolha porta {porta_id}|Feche a porta")
        db.registrar_evento("RECOLHA_INICIADA", porta_id=porta_id, pedido_id=pedido_id)
        return pedido

    # ================= Arduino =================

    def arduino_ligou(self, linha: str) -> None:
        db.registrar_evento("ARDUINO_LIGOU", linha)
        self._enviar(LCD_REPOUSO)

    def codigo_digitado(self, codigo: str) -> None:
        # O código digitado NUNCA vai para o log: o painel não pode ver códigos.
        bloqueio = codigos.segundos_de_bloqueio()
        if bloqueio:
            db.registrar_evento("TENTATIVA_BLOQUEADA", f"teclado bloqueado por mais {bloqueio}s")
            self._enviar("BEEP ERR")
            self._enviar(f"LCD Bloqueado|Aguarde {bloqueio}s")
            return

        pedido, motivo = codigos.validar(codigo)
        if pedido is None:
            self._codigo_errado(motivo)
            return

        codigos.registrar_acerto()
        porta_id = pedido["porta_id"]
        db.registrar_evento("CODIGO_ACEITO", porta_id=porta_id, pedido_id=pedido["_id"])
        try:
            self._destravar(porta_id, "RETIRADA", pedido["_id"], f"LCD Porta {porta_id} aberta|Retire e feche")
            self._enviar("BEEP OK")
        except ConnectionError:
            # só acontece se o cabo soltar entre o CODE e o UNLOCK
            db.registrar_evento("ALERTA_RETIRADA_FALHOU", "Arduino desconectado após aceitar o código",
                                porta_id=porta_id, pedido_id=pedido["_id"])

    def porta_mudou(self, porta_id: int, aberta: bool) -> None:
        if db.buscar_porta(porta_id) is None:
            db.registrar_evento("MENSAGEM_INVALIDA", f"porta {porta_id} não existe")
            return
        db.atualizar_porta(porta_id, aberta=aberta)
        db.registrar_evento("PORTA_ABERTA" if aberta else "PORTA_FECHADA", porta_id=porta_id)

        with self._lock:
            op = self.pendentes.get(porta_id)
            if aberta:
                self._aberta_desde[porta_id] = self.relogio()
                if op:
                    op["abriu"] = True
                violacao, concluir = op is None, None
            else:
                self._aberta_desde.pop(porta_id, None)
                # fechou depois de abrir: a operação terminou
                violacao, concluir = False, (self.pendentes.pop(porta_id) if op and op["abriu"] else None)
        if violacao:
            db.registrar_evento("ALERTA_VIOLACAO", "porta abriu sem UNLOCK", porta_id=porta_id)
            self._enviar_sem_falhar("BEEP ALARM")
        if concluir:
            self._concluir(porta_id, concluir)

    # ================= tarefas periódicas =================

    def verificar_prazos(self) -> None:
        """Chamada a cada segundo: operações que venceram e portas abertas demais."""
        agora = self.relogio()
        with self._lock:
            vencidas = [
                (porta_id, op) for porta_id, op in self.pendentes.items()
                if not op["abriu"] and agora - op["desde"] > TEMPO_PARA_ABRIR
            ]
            for porta_id, _ in vencidas:
                del self.pendentes[porta_id]
            abertas_demais = [
                porta_id for porta_id, desde in self._aberta_desde.items()
                if agora - desde > TEMPO_PORTA_ABERTA
            ]
            for porta_id in abertas_demais:
                self._aberta_desde[porta_id] = float("inf")  # alerta uma vez só
        for porta_id, op in vencidas:
            if op["tipo"] == "DEPOSITO":
                self._desfazer_deposito(porta_id, op["pedido_id"], "porta não abriu em 15 s")
            else:
                db.registrar_evento(f"ALERTA_{op['tipo']}_NAO_ABERTA", "porta não abriu em 15 s",
                                    porta_id=porta_id, pedido_id=op["pedido_id"])
            self._enviar_sem_falhar(LCD_REPOUSO)
        for porta_id in abertas_demais:
            db.registrar_evento("ALERTA_PORTA_ABERTA_DEMAIS", f"aberta há mais de {TEMPO_PORTA_ABERTA}s",
                                porta_id=porta_id)

    def expirar_vencidos(self) -> int:
        """Marca como EXPIRADO todo pedido DEPOSITADO cujo prazo passou."""
        total = 0
        for pedido in db.listar_vencidos(db.agora()):
            try:
                estados.aplicar(pedido, "expirar")
                total += 1
            except estados.TransicaoInvalida:
                pass  # foi retirado no mesmo instante
        return total

    def status(self) -> dict:
        with self._lock:
            return {
                str(porta_id): {"tipo": op["tipo"], "pedido_id": op["pedido_id"], "abriu": op["abriu"]}
                for porta_id, op in self.pendentes.items()
            }

    # ================= internos =================

    def _enviar(self, comando: str) -> None:
        self.ponte.enviar(comando)

    def _enviar_sem_falhar(self, comando: str) -> None:
        try:
            self._enviar(comando)
        except ConnectionError:
            pass

    def _destravar(self, porta_id, tipo, pedido_id, lcd) -> None:
        with self._lock:
            self.pendentes[porta_id] = {"tipo": tipo, "pedido_id": pedido_id, "desde": self.relogio(), "abriu": False}
        try:
            self._enviar(f"UNLOCK {porta_id}")
        except ConnectionError:
            with self._lock:
                self.pendentes.pop(porta_id, None)
            raise
        self._enviar_sem_falhar(lcd)

    def _concluir(self, porta_id: int, op: dict) -> None:
        pedido = db.buscar_pedido(op["pedido_id"])
        try:
            if op["tipo"] == "DEPOSITO":
                estados.aplicar(pedido, "depositar", expira_em=db.agora() + PRAZO_RETIRADA)
            elif op["tipo"] == "RETIRADA":
                db.liberar_porta(porta_id)
            elif op["tipo"] == "RECOLHA":
                estados.aplicar(pedido, "recolher")
                db.liberar_porta(porta_id)
        except estados.TransicaoInvalida as erro:
            db.registrar_evento("ERRO_ESTADO", str(erro), porta_id=porta_id, pedido_id=op["pedido_id"])
        db.registrar_evento(f"{op['tipo']}_CONCLUIDO", porta_id=porta_id, pedido_id=op["pedido_id"])
        self._enviar_sem_falhar(LCD_REPOUSO)

    def _desfazer_deposito(self, porta_id: int, pedido_id: str, motivo: str) -> None:
        db.atualizar_pedido_se(pedido_id, "PAGO", {"porta_id": None})
        db.liberar_porta(porta_id)
        db.registrar_evento("DEPOSITO_CANCELADO", motivo, porta_id=porta_id, pedido_id=pedido_id)

    def _codigo_errado(self, motivo: str) -> None:
        restantes, bloqueio = codigos.registrar_erro()
        suspeito = motivo == "ja_usado"
        db.registrar_evento("ALERTA_CODIGO_REUSADO" if suspeito else "CODIGO_RECUSADO", motivo)
        self._enviar_sem_falhar("BEEP ERR")
        if bloqueio:
            db.registrar_evento("ALERTA_TECLADO_BLOQUEADO", f"{bloqueio}s após {codigos.ERROS_PARA_BLOQUEAR} erros")
            self._enviar_sem_falhar(f"LCD Bloqueado|Aguarde {bloqueio}s")
        else:
            self._enviar_sem_falhar(f"LCD Codigo invalido|Restam {restantes} tent.")
