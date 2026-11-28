"""Máquina de estados do pedido.

Toda mudança de status passa por transicionar(). Se o evento não é permitido
no status atual, a mudança é recusada. É daqui que saem o uso único
(DEPOSITADO -> RETIRADO só uma vez) e a expiração.

aplicar() é a versão que grava no banco: calcula o novo status com
transicionar() e só salva se ninguém mudou o pedido no meio do caminho.

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


def aplicar(pedido: dict, evento: str, **campos) -> dict:
    """Aplica o evento ao pedido no banco e devolve o pedido atualizado.

    Levanta TransicaoInvalida se o evento não vale no status atual OU se
    outra requisição mudou o pedido antes (ex.: o mesmo código usado duas
    vezes ao mesmo tempo).
    """
    from backend import db  # import aqui para transicionar() continuar sem banco

    novo = transicionar(pedido["status"], evento)
    atualizado = db.atualizar_pedido_se(pedido["_id"], pedido["status"], {"status": novo, **campos})
    if atualizado is None:
        raise TransicaoInvalida(f"o pedido {pedido['_id']} mudou de status antes de '{evento}'")
    db.registrar_evento(f"PEDIDO_{novo}", evento, porta_id=atualizado.get("porta_id"), pedido_id=pedido["_id"])
    return atualizado
