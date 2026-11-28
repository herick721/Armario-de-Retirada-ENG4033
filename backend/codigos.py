"""Geração e proteção dos códigos de retirada.

Regras do documento do projeto (seção Segurança):
  - gerar com `secrets` (aleatório criptográfico), NUNCA com `random`;
  - guardar no banco só o HMAC(SECRET_KEY, código), nunca o código;
  - gerar de novo se colidir com um código ativo;
  - só vale para pedido DEPOSITADO e dentro do prazo (expira_em no futuro);
  - uso único: o pedido vira RETIRADO no instante em que o código é aceito;
  - 3 erros seguidos bloqueiam o teclado por 60 s; cada novo bloqueio dobra
    o tempo, até 15 min; um acerto zera tudo.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from backend import config, db, estados

ERROS_PARA_BLOQUEAR = 3
BLOQUEIO_INICIAL = 60  # segundos
BLOQUEIO_MAXIMO = 15 * 60


def calcular_hmac(codigo: str) -> str:
    """Impressão digital do código, que só quem tem a SECRET_KEY consegue calcular.

    Um hash simples (sha256 do código) não serve: só existem 1 milhão de
    códigos de 6 dígitos, então daria para testar todos em segundos.
    Com HMAC, sem a chave o banco vazado não revela nada.
    """
    return hmac.new(config.SECRET_KEY.encode(), codigo.encode(), hashlib.sha256).hexdigest()


def gerar_codigo() -> tuple[str, str]:
    """Devolve (codigo, codigo_hmac). O código em texto só existe nesta resposta."""
    while True:
        codigo = f"{secrets.randbelow(10**6):06d}"  # 000000 a 999999
        codigo_hmac = calcular_hmac(codigo)
        if not db.existe_codigo_ativo(codigo_hmac):
            return codigo, codigo_hmac


# ---------- validação ----------

def validar(codigo: str) -> tuple[dict | None, str]:
    """Tenta usar o código. Devolve (pedido, motivo).

    Se aceito, o pedido já volta RETIRADO (código morto) e motivo == "ok".
    Se recusado, pedido é None e motivo diz por quê (vai para o log, o
    código não).
    """
    if not (len(codigo) == 6 and codigo.isdigit()):
        return None, "formato"
    agora = db.agora()
    candidatos = db.buscar_pedidos_por_hmac(calcular_hmac(codigo))
    for pedido in candidatos:
        if pedido["status"] == "DEPOSITADO" and pedido.get("expira_em") and pedido["expira_em"] > agora:
            try:
                return estados.aplicar(pedido, "retirar", retirado_em=agora), "ok"
            except estados.TransicaoInvalida:
                return None, "ja_usado"  # outra retirada ganhou a corrida
    if any(p["status"] in ("RETIRADO", "EXPIRADO", "DEVOLVIDO") for p in candidatos):
        return None, "ja_usado"  # tentativa suspeita: código velho digitado de novo
    if any(p["status"] == "DEPOSITADO" for p in candidatos):
        return None, "vencido"  # prazo passou e a tarefa ainda não marcou EXPIRADO
    if any(p["status"] == "PAGO" for p in candidatos):
        return None, "nao_depositado"
    return None, "inexistente"


# ---------- bloqueio por tentativas ----------

def segundos_de_bloqueio() -> int:
    """Quanto falta para o teclado destravar (0 = liberado)."""
    ate = db.ler_teclado().get("bloqueado_ate")
    if ate is None:
        return 0
    falta = (ate - db.agora()).total_seconds()
    return max(0, int(falta + 0.999))  # arredonda para cima


def registrar_erro() -> tuple[int, int]:
    """Conta um erro. Devolve (tentativas_restantes, segundos_de_bloqueio)."""
    teclado = db.ler_teclado()
    erros = teclado["erros"] + 1
    if erros < ERROS_PARA_BLOQUEAR:
        db.salvar_teclado(erros=erros)
        return ERROS_PARA_BLOQUEAR - erros, 0
    bloqueios = teclado["bloqueios"] + 1
    duracao = min(BLOQUEIO_INICIAL * 2 ** (bloqueios - 1), BLOQUEIO_MAXIMO)
    db.salvar_teclado(erros=0, bloqueios=bloqueios, bloqueado_ate=db.agora() + timedelta(seconds=duracao))
    return 0, duracao


def registrar_acerto() -> None:
    db.salvar_teclado(erros=0, bloqueios=0, bloqueado_ate=None)
