"""Ponto de entrada: sobe o servidor.

    python -m backend.main --fake      # sem Arduino: o terminal simula o hardware
    python -m backend.main             # com o Arduino na porta SERIAL_PORT
"""
import argparse

import uvicorn

from backend.api import criar_app


def main():
    parser = argparse.ArgumentParser(description="Backend do armário de retirada")
    parser.add_argument("--fake", action="store_true", help="simula o Arduino pelo terminal")
    parser.add_argument("--porta", type=int, default=8000, help="porta HTTP (padrão 8000)")
    args = parser.parse_args()

    app = criar_app(fake=args.fake)
    # 127.0.0.1: só este PC acessa. Para abrir na rede local, use 0.0.0.0.
    uvicorn.run(app, host="127.0.0.1", port=args.porta)


if __name__ == "__main__":
    main()
