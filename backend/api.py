"""Rotas da API REST (contrato 2: Interface <-> Backend).

  POST /api/pedidos                 (loja, pública)  cria o pedido e devolve o código UMA vez
  GET  /api/pedidos/{id}            (loja, pública)  status do pedido, sem o código
  GET  /api/pedidos                 (painel, senha)  lista de pedidos
  POST /api/pedidos/{id}/depositar  (painel, senha)  escolhe porta livre e destrava
  POST /api/pedidos/{id}/recolher   (painel, senha)  destrava a porta de um pedido EXPIRADO
  GET  /api/portas                  (painel, senha)  estado de cada porta
  GET  /api/eventos                 (painel, senha)  últimos eventos e alertas
  GET  /api/arduino                 (painel, senha)  status da ponte serial
  POST /api/arduino/comando         (painel, senha)  manda um comando do protocolo ao Arduino (teste)

Páginas (pasta interface/, servidas pelo próprio backend):
  GET /loja    loja do cliente
  GET /painel  painel do lojista (quando interface/painel.html existir)

Documentação interativa gerada sozinha pelo FastAPI: http://localhost:8000/docs
"""
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import codigos, config, db, estados
from backend.armario import Armario, OperacaoRecusada
from backend.serial_bridge import SerialBridge
from backend.tarefas import Tarefas

PASTA_INTERFACE = Path(__file__).resolve().parent.parent / "interface"


# ---------- formatos de entrada e saída (o "contrato" em código) ----------

class NovoPedido(BaseModel):
    produto: str = Field(min_length=1, max_length=60, examples=["Fone de ouvido"])


class Pedido(BaseModel):
    id: str
    produto: str
    status: str
    porta_id: int | None
    criado_em: datetime
    expira_em: datetime | None


class PedidoCriado(Pedido):
    codigo: str = Field(description="Código de retirada. Só aparece nesta resposta.")


class Comando(BaseModel):
    comando: str = Field(examples=["UNLOCK 1", "BEEP OK", "LCD Ola|Mundo"])


def para_saida(doc: dict) -> dict:
    """Converte o documento do Mongo para o formato público.

    Daqui para fora nunca sai o codigo_hmac.
    """
    return {
        "id": doc["_id"],
        "produto": doc["produto"],
        "status": doc["status"],
        "porta_id": doc.get("porta_id"),
        "criado_em": doc["criado_em"],
        "expira_em": doc.get("expira_em"),
    }


# ---------- senha do painel (HTTP Basic) ----------

_basic = HTTPBasic()


def exigir_lojista(cred: HTTPBasicCredentials = Depends(_basic)) -> None:
    # compare_digest leva o mesmo tempo acertando ou errando, então não dá
    # para descobrir a senha medindo o tempo de resposta.
    usuario_ok = secrets.compare_digest(cred.username.encode(), config.PAINEL_USUARIO.encode())
    senha_ok = secrets.compare_digest(cred.password.encode(), config.PAINEL_SENHA.encode())
    if not (usuario_ok and senha_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="usuário ou senha incorretos",
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------- aplicação ----------

def criar_app(fake: bool = True, database=None, iniciar_serial: bool = True) -> FastAPI:
    ponte = SerialBridge(fake=fake)
    armario = Armario(ponte)
    ponte.armario = armario
    tarefas = Tarefas(armario)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        # roda uma vez quando o servidor sobe...
        db.conectar(database)
        if iniciar_serial:  # os testes desligam as threads e chamam tudo na mão
            ponte.iniciar()
            tarefas.iniciar()
        yield
        # ...e uma vez quando ele desliga
        tarefas.parar()
        ponte.parar()

    app = FastAPI(title="Armário de retirada", lifespan=ciclo_de_vida)
    app.state.ponte = ponte
    app.state.armario = armario

    # ----- loja (pública) -----

    @app.post("/api/pedidos", response_model=PedidoCriado, status_code=status.HTTP_201_CREATED)
    def criar_pedido(entrada: NovoPedido):
        """Cria o pedido já pago (pagamento simulado) e devolve o código de retirada."""
        codigo, codigo_hmac = codigos.gerar_codigo()
        pedido = {
            "_id": secrets.token_hex(6),  # id aleatório: não dá para chutar pedidos vizinhos
            "produto": entrada.produto,
            "status": estados.STATUS_INICIAL,
            "codigo_hmac": codigo_hmac,
            "porta_id": None,
            "criado_em": db.agora(),
            "expira_em": None,  # definido no depósito (48 h)
        }
        db.inserir_pedido(pedido)
        db.registrar_evento("PEDIDO_CRIADO", entrada.produto, pedido_id=pedido["_id"])
        return {**para_saida(pedido), "codigo": codigo}

    @app.get("/api/pedidos/{pedido_id}", response_model=Pedido)
    def ver_pedido(pedido_id: str):
        pedido = db.buscar_pedido(pedido_id)
        if pedido is None:
            raise HTTPException(status_code=404, detail="pedido não encontrado")
        return para_saida(pedido)

    # ----- painel (com senha) -----

    @app.get("/api/pedidos", response_model=list[Pedido], dependencies=[Depends(exigir_lojista)])
    def listar_pedidos():
        return [para_saida(p) for p in db.listar_pedidos()]

    def operacao(funcao, pedido_id):
        try:
            return para_saida(funcao(pedido_id))
        except LookupError as erro:
            raise HTTPException(status_code=404, detail=str(erro))
        except OperacaoRecusada as erro:
            raise HTTPException(status_code=409, detail=str(erro))
        except ConnectionError as erro:
            raise HTTPException(status_code=503, detail=str(erro))

    @app.post("/api/pedidos/{pedido_id}/depositar", response_model=Pedido, dependencies=[Depends(exigir_lojista)])
    def depositar(pedido_id: str):
        """Reserva a primeira porta livre e destrava. O pedido só vira DEPOSITADO
        quando o sensor avisar que a porta abriu e fechou (até lá, fica PAGO com porta_id)."""
        return operacao(armario.depositar, pedido_id)

    @app.post("/api/pedidos/{pedido_id}/recolher", response_model=Pedido, dependencies=[Depends(exigir_lojista)])
    def recolher(pedido_id: str):
        """Destrava a porta de um pedido EXPIRADO. Vira DEVOLVIDO quando a porta abrir e fechar."""
        return operacao(armario.recolher, pedido_id)

    @app.get("/api/portas", dependencies=[Depends(exigir_lojista)])
    def listar_portas():
        return [
            {"id": p["_id"], "estado": p["estado"], "aberta": p["aberta"], "pedido_id": p.get("pedido_id")}
            for p in db.listar_portas()
        ]

    @app.get("/api/eventos", dependencies=[Depends(exigir_lojista)])
    def listar_eventos():
        return db.listar_eventos()

    # ----- Arduino (com senha) -----

    @app.get("/api/arduino", dependencies=[Depends(exigir_lojista)])
    def status_arduino():
        return {**ponte.status(), "operacoes_pendentes": armario.status(),
                "teclado_bloqueado_por": codigos.segundos_de_bloqueio()}

    @app.post("/api/arduino/comando", dependencies=[Depends(exigir_lojista)])
    def enviar_comando(entrada: Comando):
        """Rota de teste. O depósito e a retirada já mandam os comandos sozinhos."""
        try:
            ponte.enviar(entrada.comando)
        except ValueError as erro:
            raise HTTPException(status_code=400, detail=str(erro))
        except ConnectionError as erro:
            raise HTTPException(status_code=503, detail=str(erro))
        db.registrar_evento("COMANDO_MANUAL", entrada.comando)
        return {"enviado": entrada.comando}

    # ----- páginas (interface/) -----

    def pagina(nome):
        arquivo = PASTA_INTERFACE / nome
        if not arquivo.is_file():
            raise HTTPException(status_code=404, detail=f"interface/{nome} ainda não existe")
        return FileResponse(arquivo)

    @app.get("/", include_in_schema=False)
    def inicio():
        return RedirectResponse("/loja")

    @app.get("/loja", include_in_schema=False)
    def loja():
        return pagina("loja.html")

    @app.get("/painel", include_in_schema=False)
    def painel():
        return pagina("painel.html")

    if (PASTA_INTERFACE / "imagens").is_dir():
        app.mount("/imagens", StaticFiles(directory=PASTA_INTERFACE / "imagens"), name="imagens")

    return app
