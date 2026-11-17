"""Acesso ao MongoDB. É o ÚNICO arquivo que conversa com o banco.

Coleções (o equivalente às "tabelas" do documento do projeto):
  - pedidos: _id, produto, status, codigo_hmac, porta_id, criado_em, expira_em
  - portas:  _id, estado (LIVRE/OCUPADA), aberta (True/False, vem do sensor)
  - eventos: quando, tipo, porta_id, pedido_id, detalhe
"""
from datetime import datetime, timezone

from pymongo import DESCENDING, MongoClient

from backend import config

NUM_PORTAS = 2

_db = None


def conectar(database=None):
    """Abre a conexão. Os testes passam um banco falso (mongomock)."""
    global _db
    if database is None:
        # tz_aware: devolve as datas com fuso (UTC), igual a como foram gravadas
        cliente = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=3000, tz_aware=True)
        cliente.admin.command("ping")  # falha logo se o MongoDB não estiver rodando
        database = cliente[config.MONGO_DB]
    _db = database
    _db.pedidos.create_index("codigo_hmac")  # a retirada busca pedido pelo HMAC
    for porta_id in range(1, NUM_PORTAS + 1):
        # upsert: cria a porta só se ainda não existir
        _db.portas.update_one(
            {"_id": porta_id},
            {"$setOnInsert": {"estado": "LIVRE", "aberta": False}},
            upsert=True,
        )


def agora():
    return datetime.now(timezone.utc)


# ---------- pedidos ----------

def inserir_pedido(pedido: dict) -> None:
    _db.pedidos.insert_one(pedido)


def buscar_pedido(pedido_id: str) -> dict | None:
    return _db.pedidos.find_one({"_id": pedido_id})


def listar_pedidos() -> list[dict]:
    return list(_db.pedidos.find().sort("criado_em", DESCENDING))


def existe_codigo_ativo(codigo_hmac: str) -> bool:
    """Ativo = pedido que ainda pode ser retirado com esse código."""
    return _db.pedidos.find_one(
        {"codigo_hmac": codigo_hmac, "status": {"$in": ["PAGO", "DEPOSITADO"]}}
    ) is not None


# ---------- portas ----------

def listar_portas() -> list[dict]:
    return list(_db.portas.find().sort("_id"))


def atualizar_porta(porta_id: int, **campos) -> None:
    _db.portas.update_one({"_id": porta_id}, {"$set": campos})


# ---------- eventos ----------

def registrar_evento(tipo: str, detalhe: str = "", porta_id=None, pedido_id=None) -> None:
    _db.eventos.insert_one({
        "quando": agora(),
        "tipo": tipo,
        "porta_id": porta_id,
        "pedido_id": pedido_id,
        "detalhe": detalhe,
    })


def listar_eventos(limite: int = 50) -> list[dict]:
    return list(_db.eventos.find({}, {"_id": 0}).sort("quando", DESCENDING).limit(limite))
