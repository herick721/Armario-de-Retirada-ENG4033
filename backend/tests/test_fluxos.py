"""Fluxos B, C e D do documento do projeto, com o Arduino simulado.

Cada teste faz o papel do Arduino chamando ponte.tratar_linha("DOOR 1 OPEN")
etc., e confere os comandos que o PC mandou de volta.
"""
from datetime import timedelta

import mongomock
import pytest
from fastapi.testclient import TestClient

from backend import config, db
from backend.api import criar_app

SENHA = (config.PAINEL_USUARIO, config.PAINEL_SENHA)


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def c():
    banco = mongomock.MongoClient(tz_aware=True).db
    app = criar_app(fake=True, database=banco, iniciar_serial=False)
    with TestClient(app) as cliente:
        cliente.banco = banco
        cliente.ponte = app.state.ponte
        cliente.armario = app.state.armario
        cliente.relogio = cliente.armario.relogio = Relogio()
        cliente.enviados = []
        enviar_original = cliente.ponte.enviar

        def enviar(comando):
            enviar_original(comando)  # continua validando o protocolo
            cliente.enviados.append(comando)

        cliente.ponte.enviar = enviar
        yield cliente
        # tratar_linha engole exceções para não derrubar a serial; aqui elas reprovam o teste
        erros = list(banco.eventos.find({"tipo": "ERRO_INTERNO"}))
        assert not erros, erros


def comprar(c, produto="Fone"):
    return c.post("/api/pedidos", json={"produto": produto}).json()


def pedido(c, pedido_id):
    return c.get(f"/api/pedidos/{pedido_id}").json()


def porta(c, porta_id):
    return c.get("/api/portas", auth=SENHA).json()[porta_id - 1]


def tipos_eventos(c):
    return [e["tipo"] for e in c.get("/api/eventos", auth=SENHA).json()]


def depositar(c, pedido_id, porta_id):
    """Fluxo B completo: painel clica, porta abre e fecha."""
    r = c.post(f"/api/pedidos/{pedido_id}/depositar", auth=SENHA)
    assert r.status_code == 200, r.text
    c.ponte.tratar_linha(f"DOOR {porta_id} OPEN")
    c.ponte.tratar_linha(f"DOOR {porta_id} CLOSED")


# ---------- B: depósito ----------

def test_deposito_destrava_e_so_confirma_pelo_sensor(c):
    p = comprar(c)
    r = c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA)
    assert r.status_code == 200
    assert r.json()["status"] == "PAGO" and r.json()["porta_id"] == 1
    assert c.enviados[:2] == ["UNLOCK 1", "LCD Deposito porta 1|Feche a porta"]
    assert porta(c, 1)["estado"] == "OCUPADA"

    c.ponte.tratar_linha("DOOR 1 OPEN")
    assert pedido(c, p["id"])["status"] == "PAGO"  # ainda não fechou
    c.ponte.tratar_linha("DOOR 1 CLOSED")

    final = pedido(c, p["id"])
    assert final["status"] == "DEPOSITADO"
    assert final["expira_em"] is not None
    assert c.enviados[-1] == "LCD Digite o codigo|e aperte #"


def test_porta_1_ocupada_vai_para_porta_2(c):
    a, b, d = comprar(c, "A"), comprar(c, "B"), comprar(c, "C")
    depositar(c, a["id"], 1)
    depositar(c, b["id"], 2)
    assert pedido(c, b["id"])["porta_id"] == 2
    r = c.post(f"/api/pedidos/{d['id']}/depositar", auth=SENHA)
    assert r.status_code == 409  # sem porta livre


def test_deposito_desfeito_se_porta_nao_abre_em_15s(c):
    p = comprar(c)
    c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA)
    c.relogio.t += 16
    c.armario.verificar_prazos()
    assert pedido(c, p["id"])["porta_id"] is None
    assert porta(c, 1)["estado"] == "LIVRE"
    assert "DEPOSITO_CANCELADO" in tipos_eventos(c)
    # dá para tentar de novo
    assert c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA).status_code == 200


def test_nao_deposita_duas_vezes(c):
    p = comprar(c)
    assert c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA).status_code == 200
    assert c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA).status_code == 409
    assert porta(c, 2)["estado"] == "LIVRE"


def test_depositar_pedido_inexistente_da_404(c):
    assert c.post("/api/pedidos/naoexiste/depositar", auth=SENHA).status_code == 404


def test_deposito_sem_arduino_da_503_e_desfaz(c):
    def desconectado(comando):
        raise ConnectionError("Arduino não conectado")

    c.ponte.enviar = desconectado
    p = comprar(c)
    assert c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA).status_code == 503
    assert porta(c, 1)["estado"] == "LIVRE"
    assert pedido(c, p["id"])["porta_id"] is None


# ---------- C: retirada ----------

def test_retirada_abre_a_porta_certa_e_libera(c):
    a, b = comprar(c, "A"), comprar(c, "B")
    depositar(c, a["id"], 1)
    depositar(c, b["id"], 2)
    c.enviados.clear()

    c.ponte.tratar_linha(f"CODE {b['codigo']}")
    assert pedido(c, b["id"])["status"] == "RETIRADO"  # morto antes de a porta abrir
    assert c.enviados[:3] == ["UNLOCK 2", "LCD Porta 2 aberta|Retire e feche", "BEEP OK"]

    c.ponte.tratar_linha("DOOR 2 OPEN")
    c.ponte.tratar_linha("DOOR 2 CLOSED")
    assert porta(c, 2)["estado"] == "LIVRE"
    assert porta(c, 1)["estado"] == "OCUPADA"


def test_codigo_vale_uma_vez_so(c):
    p = comprar(c)
    depositar(c, p["id"], 1)
    c.ponte.tratar_linha(f"CODE {p['codigo']}")
    c.ponte.tratar_linha("DOOR 1 OPEN")
    c.ponte.tratar_linha("DOOR 1 CLOSED")
    c.enviados.clear()

    c.ponte.tratar_linha(f"CODE {p['codigo']}")
    assert "UNLOCK 1" not in c.enviados
    assert "BEEP ERR" in c.enviados
    assert "ALERTA_CODIGO_REUSADO" in tipos_eventos(c)


def test_codigo_antes_do_deposito_nao_abre(c):
    p = comprar(c)
    c.ponte.tratar_linha(f"CODE {p['codigo']}")
    assert not any(cmd.startswith("UNLOCK") for cmd in c.enviados)
    assert pedido(c, p["id"])["status"] == "PAGO"


def test_codigo_vencido_nao_abre(c):
    p = comprar(c)
    depositar(c, p["id"], 1)
    c.banco.pedidos.update_one({"_id": p["id"]}, {"$set": {"expira_em": db.agora() - timedelta(seconds=1)}})
    c.enviados.clear()
    c.ponte.tratar_linha(f"CODE {p['codigo']}")
    assert "UNLOCK 1" not in c.enviados
    assert pedido(c, p["id"])["status"] == "DEPOSITADO"


def test_codigo_errado_mostra_tentativas_restantes(c):
    c.ponte.tratar_linha("CODE 000000")
    assert c.enviados == ["BEEP ERR", "LCD Codigo invalido|Restam 2 tent."]


# ---------- bloqueio ----------

def errar(c, vezes):
    for _ in range(vezes):
        c.ponte.tratar_linha("CODE 000000")


def test_tres_erros_bloqueiam_e_o_quarto_e_ignorado(c):
    p = comprar(c)
    depositar(c, p["id"], 1)
    c.enviados.clear()
    errar(c, 3)
    assert c.enviados[-1] == "LCD Bloqueado|Aguarde 60s"

    c.enviados.clear()
    c.ponte.tratar_linha(f"CODE {p['codigo']}")  # até o código certo é ignorado
    assert "UNLOCK 1" not in c.enviados
    assert pedido(c, p["id"])["status"] == "DEPOSITADO"
    assert c.enviados[-1].startswith("LCD Bloqueado|Aguarde")


def test_bloqueio_dobra_ate_15_min_e_acerto_zera(c):
    duracoes = []
    for _ in range(6):
        errar(c, 3)
        duracoes.append(c.enviados[-1])
        c.banco.teclado.update_one({"_id": 1}, {"$set": {"bloqueado_ate": None}})  # "espera" passar
    assert duracoes == [f"LCD Bloqueado|Aguarde {s}s" for s in (60, 120, 240, 480, 900, 900)]

    p = comprar(c)
    depositar(c, p["id"], 1)
    c.ponte.tratar_linha(f"CODE {p['codigo']}")
    assert pedido(c, p["id"])["status"] == "RETIRADO"
    errar(c, 3)
    assert c.enviados[-1] == "LCD Bloqueado|Aguarde 60s"  # voltou ao começo


# ---------- D: expiração e recolha ----------

def test_expiracao_e_recolha(c):
    p = comprar(c)
    depositar(c, p["id"], 1)
    assert c.post(f"/api/pedidos/{p['id']}/recolher", auth=SENHA).status_code == 409  # ainda vale

    c.banco.pedidos.update_one({"_id": p["id"]}, {"$set": {"expira_em": db.agora() - timedelta(seconds=1)}})
    assert c.armario.expirar_vencidos() == 1
    assert pedido(c, p["id"])["status"] == "EXPIRADO"

    c.enviados.clear()
    assert c.post(f"/api/pedidos/{p['id']}/recolher", auth=SENHA).status_code == 200
    assert c.enviados[0] == "UNLOCK 1"
    c.ponte.tratar_linha("DOOR 1 OPEN")
    c.ponte.tratar_linha("DOOR 1 CLOSED")
    assert pedido(c, p["id"])["status"] == "DEVOLVIDO"
    assert porta(c, 1)["estado"] == "LIVRE"


# ---------- alertas ----------

def test_porta_aberta_sem_unlock_e_violacao(c):
    c.ponte.tratar_linha("DOOR 1 OPEN")
    assert "BEEP ALARM" in c.enviados
    assert "ALERTA_VIOLACAO" in tipos_eventos(c)


def test_porta_aberta_demais_gera_alerta_uma_vez(c):
    p = comprar(c)
    c.post(f"/api/pedidos/{p['id']}/depositar", auth=SENHA)
    c.ponte.tratar_linha("DOOR 1 OPEN")
    c.relogio.t += 61
    c.armario.verificar_prazos()
    c.armario.verificar_prazos()
    assert tipos_eventos(c).count("ALERTA_PORTA_ABERTA_DEMAIS") == 1


def test_porta_inexistente_e_ignorada(c):
    c.ponte.tratar_linha("DOOR 7 OPEN")
    assert tipos_eventos(c)[0] == "MENSAGEM_INVALIDA"
