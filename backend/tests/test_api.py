"""Testes da API. Rodam sem MongoDB real (mongomock) e sem Arduino.

    pytest backend -v
"""
import mongomock
import pytest
from fastapi.testclient import TestClient

from backend import codigos, config, db
from backend.api import criar_app

SENHA = (config.PAINEL_USUARIO, config.PAINEL_SENHA)


@pytest.fixture
def cliente():
    banco = mongomock.MongoClient(tz_aware=True).db
    app = criar_app(fake=True, database=banco, iniciar_serial=False)
    with TestClient(app) as c:
        c.banco = banco
        yield c


def test_post_cria_pedido_e_devolve_codigo(cliente):
    r = cliente.post("/api/pedidos", json={"produto": "Fone"})
    assert r.status_code == 201
    corpo = r.json()
    assert corpo["status"] == "PAGO"
    assert len(corpo["codigo"]) == 6 and corpo["codigo"].isdigit()


def test_banco_guarda_so_o_hmac(cliente):
    corpo = cliente.post("/api/pedidos", json={"produto": "Fone"}).json()
    salvo = cliente.banco.pedidos.find_one({"_id": corpo["id"]})
    assert "codigo" not in salvo
    assert corpo["codigo"] not in str(salvo)
    assert salvo["codigo_hmac"] == codigos.calcular_hmac(corpo["codigo"])


def test_get_devolve_pedido_sem_codigo(cliente):
    criado = cliente.post("/api/pedidos", json={"produto": "Fone"}).json()
    r = cliente.get(f"/api/pedidos/{criado['id']}")
    assert r.status_code == 200
    assert r.json()["produto"] == "Fone"
    assert "codigo" not in r.json() and "codigo_hmac" not in r.json()
    assert r.json()["criado_em"].endswith("Z")  # data com fuso, igual ao POST


def test_get_pedido_inexistente_da_404(cliente):
    assert cliente.get("/api/pedidos/naoexiste").status_code == 404


def test_post_sem_produto_da_422(cliente):
    assert cliente.post("/api/pedidos", json={}).status_code == 422


def test_rotas_do_painel_exigem_senha(cliente):
    for rota in ["/api/pedidos", "/api/portas", "/api/eventos", "/api/arduino"]:
        assert cliente.get(rota).status_code == 401
        assert cliente.get(rota, auth=("lojista", "errada")).status_code == 401
        assert cliente.get(rota, auth=SENHA).status_code == 200


def test_lista_pedidos(cliente):
    cliente.post("/api/pedidos", json={"produto": "A"})
    cliente.post("/api/pedidos", json={"produto": "B"})
    assert len(cliente.get("/api/pedidos", auth=SENHA).json()) == 2


def test_comando_arduino_valido_e_invalido(cliente):
    ok = cliente.post("/api/arduino/comando", json={"comando": "UNLOCK 1"}, auth=SENHA)
    assert ok.status_code == 200
    for ruim in ["UNLOCK 9", "rm -rf", "LCD texto|grande demais para o lcd", "UNLOCK 1\nUNLOCK 2"]:
        r = cliente.post("/api/arduino/comando", json={"comando": ruim}, auth=SENHA)
        assert r.status_code == 400, ruim


def test_arduino_door_atualiza_porta(cliente):
    ponte = cliente.app.state.ponte
    ponte.tratar_linha("DOOR 1 OPEN\n")
    portas = cliente.get("/api/portas", auth=SENHA).json()
    assert portas[0] == {"id": 1, "estado": "LIVRE", "aberta": True}
    ponte.tratar_linha("DOOR 1 CLOSED")
    assert cliente.get("/api/portas", auth=SENHA).json()[0]["aberta"] is False


def test_codigo_digitado_nao_vai_para_o_log(cliente):
    cliente.app.state.ponte.tratar_linha("CODE 482913")
    eventos = cliente.get("/api/eventos", auth=SENHA).json()
    assert eventos[0]["tipo"] == "CODIGO_DIGITADO"
    assert "482913" not in str(eventos)
    assert "482913" not in str(cliente.get("/api/arduino", auth=SENHA).json())
