"""Geração e proteção dos códigos de retirada.

Regras do documento do projeto (seção Segurança):
  - gerar com `secrets` (aleatório criptográfico), NUNCA com `random`;
  - guardar no banco só o HMAC(SECRET_KEY, código), nunca o código;
  - gerar de novo se colidir com um código ativo.

Validação, uso único e bloqueio por tentativas ficam para a Semana 2.
"""
import hashlib
import hmac
import secrets

from backend import config, db


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
