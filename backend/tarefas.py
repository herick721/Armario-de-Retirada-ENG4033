"""Tarefas que rodam sozinhas, numa thread separada do servidor web.

  - a cada 1 s: depósito/retirada cuja porta não abriu em 15 s, porta aberta demais;
  - a cada 30 s: pedidos DEPOSITADOS vencidos viram EXPIRADO (o código deixa de valer).

Mesmo sem esta tarefa o código vencido já é recusado, porque a validação
confere expira_em; ela serve para o painel mostrar o botão "recolher".
"""
import threading
import time

INTERVALO_PRAZOS = 1  # segundos
INTERVALO_EXPIRACAO = 30


class Tarefas:
    def __init__(self, armario):
        self.armario = armario
        self._rodando = False

    def iniciar(self):
        self._rodando = True
        threading.Thread(target=self._loop, daemon=True, name="tarefas").start()

    def parar(self):
        self._rodando = False

    def _loop(self):
        ultima_expiracao = 0.0
        while self._rodando:
            try:
                self.armario.verificar_prazos()
                if time.monotonic() - ultima_expiracao >= INTERVALO_EXPIRACAO:
                    self.armario.expirar_vencidos()
                    ultima_expiracao = time.monotonic()
            except Exception as erro:  # uma falha não pode matar a thread
                print(f"[tarefas] erro: {erro!r}", flush=True)
            time.sleep(INTERVALO_PRAZOS)
