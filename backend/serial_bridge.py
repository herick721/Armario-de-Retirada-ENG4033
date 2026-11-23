"""Ponte serial: o lado PC do protocolo com o Arduino (contrato 1).

115200 baud, texto ASCII, uma mensagem por linha terminada em \\n.

  Arduino -> PC: HELLO 2 | CODE 482913 | DOOR 1 OPEN | DOOR 1 CLOSED | PONG
  PC -> Arduino: UNLOCK 1 | LCD linha1|linha2 | BEEP OK/ERR/ALARM | PING

Roda em threads separadas do servidor web:
  - leitor: lê cada linha que chega e chama o handler certo;
  - heartbeat: manda PING a cada 2 s (sem PING por 6 s o Arduino tranca tudo).

Modo fake (sem Arduino): o terminal faz o papel do hardware. Digite, por
exemplo, DOOR 1 OPEN e aperte Enter; o que o PC enviaria aparece impresso.
"""
import re
import sys
import threading
import time
from collections import deque

from backend import config, db

INTERVALO_PING = 2  # segundos

# Comandos que o PC pode mandar. Qualquer outra coisa é recusada antes de
# chegar ao Arduino (ninguém injeta comando estranho pela API).
_COMANDOS_VALIDOS = [
    re.compile(r"UNLOCK [1-%d]" % db.NUM_PORTAS),
    re.compile(r"BEEP (OK|ERR|ALARM)"),
    re.compile(r"LCD [ -{}~]{0,16}\|[ -{}~]{0,16}"),  # ASCII imprimível sem '|', até 16 por linha
    re.compile(r"PING"),
]


def comando_valido(comando: str) -> bool:
    return any(p.fullmatch(comando) for p in _COMANDOS_VALIDOS)


class SerialBridge:
    def __init__(self, fake: bool = False, porta: str = config.SERIAL_PORT):
        self.fake = fake
        self.porta = porta
        self._serial = None
        self._lock = threading.Lock()  # duas threads não escrevem na serial ao mesmo tempo
        self._rodando = False
        self.conectado = fake
        self.ultimo_pong = None
        self.ultimas_mensagens = deque(maxlen=20)  # para o GET /api/arduino

    # ---------- ciclo de vida ----------

    def iniciar(self):
        self._rodando = True
        threading.Thread(target=self._loop_leitura, daemon=True, name="serial-leitor").start()
        threading.Thread(target=self._loop_heartbeat, daemon=True, name="serial-ping").start()

    def parar(self):
        self._rodando = False
        if self._serial:
            self._serial.close()

    # ---------- envio ----------

    def enviar(self, comando: str) -> None:
        if not comando_valido(comando):
            raise ValueError(f"comando fora do protocolo: {comando!r}")
        if comando != "PING":  # PING a cada 2 s poluiria o histórico e o terminal
            self._registrar("PC -> Arduino", comando)
            if self.fake:
                print(f"[PC -> Arduino] {comando}", flush=True)
        if self.fake:
            return
        with self._lock:
            if not self._serial:
                raise ConnectionError("Arduino não conectado")
            self._serial.write((comando + "\n").encode("ascii"))

    # ---------- recebimento ----------

    def tratar_linha(self, linha: str) -> None:
        """Interpreta uma mensagem do Arduino. Linha desconhecida é ignorada."""
        linha = linha.strip()
        if not linha:
            return
        partes = linha.split()
        tipo = partes[0].upper()

        if tipo == "PONG":
            self.ultimo_pong = time.time()
            return  # não guarda: chega a cada 2 s
        self._registrar("Arduino -> PC", linha if tipo != "CODE" else "CODE ******")

        if tipo == "HELLO":
            db.registrar_evento("ARDUINO_LIGOU", linha)
        elif tipo == "DOOR" and len(partes) == 3 and partes[1].isdigit() and partes[2] in ("OPEN", "CLOSED"):
            porta_id = int(partes[1])
            aberta = partes[2] == "OPEN"
            db.atualizar_porta(porta_id, aberta=aberta)
            db.registrar_evento("PORTA_ABERTA" if aberta else "PORTA_FECHADA", porta_id=porta_id)
        elif tipo == "CODE" and len(partes) == 2 and partes[1].isdigit() and len(partes[1]) == 6:
            # Validação do código é meta da Semana 2. O código digitado nunca
            # vai para o log: o painel não pode ver códigos.
            db.registrar_evento("CODIGO_DIGITADO", "validação ainda não implementada (S2)")
        else:
            db.registrar_evento("MENSAGEM_INVALIDA", linha[:60])

    # ---------- status (para a API) ----------

    def status(self) -> dict:
        return {
            "modo": "fake" if self.fake else "serial",
            "porta": None if self.fake else self.porta,
            "conectado": self.conectado,
            "segundos_desde_ultimo_pong": (
                round(time.time() - self.ultimo_pong, 1) if self.ultimo_pong else None
            ),
            "ultimas_mensagens": list(self.ultimas_mensagens),
        }

    # ---------- internos ----------

    def _registrar(self, direcao, mensagem):
        self.ultimas_mensagens.append({"quando": db.agora().isoformat(), "direcao": direcao, "mensagem": mensagem})

    def _loop_heartbeat(self):
        while self._rodando:
            try:
                self.enviar("PING")
                if self.fake:
                    self.ultimo_pong = time.time()  # o "Arduino" do terminal sempre responde
            except ConnectionError:
                pass  # o leitor está tentando reconectar
            time.sleep(INTERVALO_PING)

    def _loop_leitura(self):
        if self.fake:
            self._loop_leitura_terminal()
        else:
            self._loop_leitura_serial()

    def _loop_leitura_terminal(self):
        print("[fake] Arduino simulado. Digite mensagens como: HELLO 2, DOOR 1 OPEN, CODE 123456", flush=True)
        for linha in sys.stdin:
            if not self._rodando:
                break
            self.tratar_linha(linha)

    def _loop_leitura_serial(self):
        import serial  # pyserial; importado aqui para o modo fake não depender dele

        while self._rodando:
            try:
                with self._lock:
                    self._serial = serial.Serial(self.porta, config.SERIAL_BAUD, timeout=1)
                self.conectado = True
                db.registrar_evento("SERIAL_CONECTADA", self.porta)
                while self._rodando:
                    bruto = self._serial.readline()  # espera até 1 s por uma linha
                    if bruto:
                        self.tratar_linha(bruto.decode("ascii", errors="replace"))
            except serial.SerialException as erro:
                if self.conectado:
                    db.registrar_evento("SERIAL_DESCONECTADA", str(erro)[:100])
                self.conectado = False
                with self._lock:
                    self._serial = None
                time.sleep(2)  # tenta reconectar (cabo solto, Arduino reiniciando...)
