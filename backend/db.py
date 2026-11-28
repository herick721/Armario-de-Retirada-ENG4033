"""Acesso ao MongoDB. É o ÚNICO arquivo que conversa com o banco.

Coleções (o equivalente às "tabelas" do documento do projeto):
  - pedidos: _id, produto, status, codigo_hmac, porta_id, criado_em, expira_em
  - portas:  _id, estado (LIVRE/OCUPADA), aberta (True/False, vem do sensor), pedido_id
  - eventos: quando, tipo, porta_id, pedido_id, detalhe
  - teclado: um único documento com o contador de erros e o bloqueio
"""
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

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
            {"$setOnInsert": {"estado": "LIVRE", "aberta": False, "pedido_id": None}},
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


def atualizar_pedido_se(pedido_id: str, status_esperado: str, campos: dict) -> dict | None:
    """Muda o pedido SÓ se ele ainda estiver em status_esperado (operação atômica).

    É o que garante o uso único: se duas retiradas chegarem juntas, só a
    primeira encontra o pedido DEPOSITADO; a segunda recebe None.
    """
    return _db.pedidos.find_one_and_update(
        {"_id": pedido_id, "status": status_esperado},
        {"$set": campos},
        return_document=ReturnDocument.AFTER,
    )


def vincular_porta(pedido_id: str, porta_id: int) -> bool:
    """Liga o pedido PAGO à porta reservada, só se ele ainda não tiver porta."""
    r = _db.pedidos.update_one(
        {"_id": pedido_id, "status": "PAGO", "porta_id": None},
        {"$set": {"porta_id": porta_id}},
    )
    return r.modified_count == 1


def buscar_pedidos_por_hmac(codigo_hmac: str) -> list[dict]:
    return list(_db.pedidos.find({"codigo_hmac": codigo_hmac}))


def listar_vencidos(momento) -> list[dict]:
    """Pedidos DEPOSITADOS cujo prazo de retirada já passou."""
    return list(_db.pedidos.find({"status": "DEPOSITADO", "expira_em": {"$lte": momento}}))


def existe_codigo_ativo(codigo_hmac: str) -> bool:
    """Ativo = pedido que ainda pode ser retirado com esse código."""
    return _db.pedidos.find_one(
        {"codigo_hmac": codigo_hmac, "status": {"$in": ["PAGO", "DEPOSITADO"]}}
    ) is not None


# ---------- portas ----------

def listar_portas() -> list[dict]:
    return list(_db.portas.find().sort("_id"))


def buscar_porta(porta_id: int) -> dict | None:
    return _db.portas.find_one({"_id": porta_id})


def atualizar_porta(porta_id: int, **campos) -> None:
    _db.portas.update_one({"_id": porta_id}, {"$set": campos})


def reservar_porta_livre(pedido_id: str) -> dict | None:
    """Pega a primeira porta LIVRE e já a marca OCUPADA, numa operação só.

    Atômico para dois depósitos ao mesmo tempo não escolherem a mesma porta.
    """
    return _db.portas.find_one_and_update(
        {"estado": "LIVRE"},
        {"$set": {"estado": "OCUPADA", "pedido_id": pedido_id}},
        sort=[("_id", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def liberar_porta(porta_id: int) -> None:
    atualizar_porta(porta_id, estado="LIVRE", pedido_id=None)


# ---------- teclado (bloqueio por tentativas) ----------

def ler_teclado() -> dict:
    padrao = {"_id": 1, "erros": 0, "bloqueios": 0, "bloqueado_ate": None}
    return {**padrao, **(_db.teclado.find_one({"_id": 1}) or {})}


def salvar_teclado(**campos) -> None:
    _db.teclado.update_one({"_id": 1}, {"$set": campos}, upsert=True)


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
