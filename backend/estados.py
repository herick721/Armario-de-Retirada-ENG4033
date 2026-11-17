"""Máquina de estados do pedido.

Toda mudança de status passa por transicionar(). Se o evento não é permitido
no status atual, a mudança é recusada. É daqui que saem, nas próximas semanas,
o uso único (DEPOSITADO -> RETIRADO só uma vez) e a expiração.

    PAGO --depositar--> DEPOSITADO --retirar--> RETIRADO
                            |
                         expirar
                            v
                        EXPIRADO --recolher--> DEVOLVIDO
"""

# (status atual, evento) -> novo status
TRANSICOES = {
    ("PAGO", "depositar"): "DEPOSITADO",
    ("DEPOSITADO", "retirar"): "RETIRADO",
    ("DEPOSITADO", "expirar"): "EXPIRADO",
    ("EXPIRADO", "recolher"): "DEVOLVIDO",
}

STATUS_INICIAL = "PAGO"


class TransicaoInvalida(Exception):
    pass


def transicionar(status_atual: str, evento: str) -> str:
    """Devolve o novo status, ou levanta TransicaoInvalida."""
    try:
        return TRANSICOES[(status_atual, evento)]
    except KeyError:
        raise TransicaoInvalida(f"'{evento}' não é permitido com o pedido em {status_atual}") from None
