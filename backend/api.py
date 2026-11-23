"""Rotas da API REST (contrato 2: Interface <-> Backend).

Semana 1:
  POST /api/pedidos          (loja, pública)  cria o pedido e devolve o código UMA vez
  GET  /api/pedidos/{id}     (loja, pública)  status do pedido, sem o código
  GET  /api/pedidos          (painel, senha)  lista de pedidos
  GET  /api/portas           (painel, senha)  estado de cada porta
  GET  /api/eventos          (painel, senha)  últimos eventos
  GET  /api/arduino          (painel, senha)  status da ponte serial
  POST /api/arduino/comando  (painel, senha)  manda um comando do protocolo ao Arduino (teste)

Documentação interativa gerada sozinha pelo FastAPI: http://localhost:8000/docs
"""
import secrets
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from backend import codigos, config, db, estados
from backend.serial_bridge import SerialBridge


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

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        # roda uma vez quando o servidor sobe...
        db.conectar(database)
        if iniciar_serial:
            ponte.iniciar()
        yield
        # ...e uma vez quando ele desliga
        ponte.parar()

    app = FastAPI(title="Armário de retirada", lifespan=ciclo_de_vida)
    app.state.ponte = ponte

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

    @app.get("/api/portas", dependencies=[Depends(exigir_lojista)])
    def listar_portas():
        return [{"id": p["_id"], "estado": p["estado"], "aberta": p["aberta"]} for p in db.listar_portas()]

    @app.get("/api/eventos", dependencies=[Depends(exigir_lojista)])
    def listar_eventos():
        return db.listar_eventos()

    # ----- Arduino (com senha) -----

    @app.get("/api/arduino", dependencies=[Depends(exigir_lojista)])
    def status_arduino():
        return ponte.status()

    @app.post("/api/arduino/comando", dependencies=[Depends(exigir_lojista)])
    def enviar_comando(entrada: Comando):
        """Rota de teste da S1. Na S2 o depósito e a retirada mandam os comandos sozinhos."""
        try:
            ponte.enviar(entrada.comando)
        except ValueError as erro:
            raise HTTPException(status_code=400, detail=str(erro))
        except ConnectionError as erro:
            raise HTTPException(status_code=503, detail=str(erro))
        db.registrar_evento("COMANDO_MANUAL", entrada.comando)
        return {"enviado": entrada.comando}

    return app
