import pytest

from backend.estados import TransicaoInvalida, transicionar


def test_caminho_feliz():
    assert transicionar("PAGO", "depositar") == "DEPOSITADO"
    assert transicionar("DEPOSITADO", "retirar") == "RETIRADO"


def test_nao_retira_duas_vezes():
    with pytest.raises(TransicaoInvalida):
        transicionar("RETIRADO", "retirar")


def test_nao_retira_sem_deposito():
    with pytest.raises(TransicaoInvalida):
        transicionar("PAGO", "retirar")
