"""Configurações lidas de variáveis de ambiente.

Nada de senha ou chave escrita no código: quem roda o servidor define isso
no terminal (veja backend/README.md). Os valores padrão servem só para
desenvolvimento.
"""
import os
import sys

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "armario")

# Chave do HMAC dos códigos. Se vazar, os códigos guardados no banco ficam
# vulneráveis; por isso ela nunca vai para o GitHub.
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = "chave-de-desenvolvimento-NAO-use-em-producao"
    print("AVISO: SECRET_KEY não definida, usando chave de desenvolvimento.", file=sys.stderr)

# Login do painel (HTTP Basic)
PAINEL_USUARIO = os.getenv("PAINEL_USUARIO", "lojista")
PAINEL_SENHA = os.getenv("PAINEL_SENHA", "trocar-esta-senha")

# Porta serial do Arduino (Linux: /dev/ttyACM0, Windows: COM3)
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = 115200
