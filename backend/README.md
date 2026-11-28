# Backend e segurança

API em Python (FastAPI) + MongoDB + ponte serial com o Arduino. É o único que
mexe no banco e muda o status de um pedido.

## Rodar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# MongoDB precisa estar rodando (padrão: mongodb://localhost:27017)
export SECRET_KEY="uma-frase-longa-e-aleatoria"      # Windows: set SECRET_KEY=...
export PAINEL_SENHA="outra-senha"

python -m backend.main --fake   # sem Arduino: o terminal faz o papel do hardware
python -m backend.main          # com o Arduino em SERIAL_PORT (padrão /dev/ttyACM0)
```

Depois abra:

| Endereço | O quê |
|---|---|
| http://localhost:8000/loja | loja do cliente (interface/loja.html) |
| http://localhost:8000/painel | painel do lojista (quando interface/painel.html existir) |
| http://localhost:8000/docs | documentação da API, dá para testar as rotas por ali |

Variáveis de ambiente: `SECRET_KEY`, `PAINEL_USUARIO` (padrão `lojista`),
`PAINEL_SENHA`, `MONGO_URL`, `MONGO_DB`, `SERIAL_PORT`.

## Testes

```bash
pytest backend -v
```

Rodam sem MongoDB (mongomock) e sem Arduino. `test_fluxos.py` faz o papel do
Arduino e cobre os fluxos B, C e D do documento: depósito confirmado pelo
sensor, porta 1 ocupada → porta 2, código de uso único, código vencido,
bloqueio após 3 erros, expiração e recolha, violação de porta.

## Testar o fluxo inteiro no modo `--fake`

1. Na loja, compre um produto e anote o código.
2. Em `/docs`, `GET /api/pedidos` (usuário `lojista`) para pegar o id, e
   `POST /api/pedidos/{id}/depositar`. O terminal mostra `UNLOCK 1`.
3. No terminal, digite `DOOR 1 OPEN` e depois `DOOR 1 CLOSED`. A loja muda
   para "Pronto na porta 1".
4. Digite `CODE <código>`. O terminal mostra `UNLOCK 1` e `BEEP OK`, e a loja
   muda para "Retirado". Digitar o mesmo código de novo dá `BEEP ERR`.

## Arquivos

| Arquivo | Faz |
|---|---|
| `api.py` | rotas da API (contrato 2) e as páginas da pasta `interface/` |
| `armario.py` | regras do armário: depósito, retirada, recolha, violação, prazos |
| `codigos.py` | gera, guarda (só HMAC) e valida códigos; bloqueio por tentativas |
| `estados.py` | máquina de estados do pedido; `aplicar()` grava de forma atômica |
| `serial_bridge.py` | protocolo serial (contrato 1), PING, reconexão, modo fake |
| `tarefas.py` | thread que vence depósitos sem porta aberta e expira pedidos |
| `db.py` | único arquivo que fala com o MongoDB |

## Como cada fluxo passa pelo backend

**Depósito.** `POST /api/pedidos/{id}/depositar` reserva a primeira porta
LIVRE (operação atômica), manda `UNLOCK n` e `LCD Deposito porta n|Feche a porta`
e responde na hora com o pedido ainda `PAGO` e `porta_id` preenchido. Quando
chegam `DOOR n OPEN` e `DOOR n CLOSED`, o pedido vira `DEPOSITADO` com
`expira_em` = agora + 48 h. Se a porta não abrir em 15 s, a reserva é desfeita.

**Retirada.** `CODE 482913` → se o teclado estiver bloqueado, nada abre. Senão
calcula o HMAC e procura pedido `DEPOSITADO` com `expira_em` no futuro. Achou:
o pedido vira `RETIRADO` **antes** do `UNLOCK` (uso único, atômico), depois
`BEEP OK` e o LCD. Quando a porta abre e fecha, ela volta a LIVRE. Não achou:
`BEEP ERR` e `LCD Codigo invalido|Restam N tent.`.

**Bloqueio.** 3 erros seguidos bloqueiam o teclado por 60 s; cada novo
bloqueio dobra (120, 240, 480 s) até 15 min; um acerto zera. Fica salvo no
banco, então reiniciar o servidor não libera o teclado.

**Expiração e recolha.** A cada 30 s, pedido `DEPOSITADO` vencido vira
`EXPIRADO`. `POST /api/pedidos/{id}/recolher` destrava; ao abrir e fechar, o
pedido vira `DEVOLVIDO` e a porta fica LIVRE.

## Eventos (para o painel)

Os eventos que começam com `ALERTA_` devem aparecer em destaque:
`ALERTA_VIOLACAO` (porta abriu sem UNLOCK, manda `BEEP ALARM`),
`ALERTA_CODIGO_REUSADO`, `ALERTA_TECLADO_BLOQUEADO`,
`ALERTA_PORTA_ABERTA_DEMAIS` (mais de 60 s), `ALERTA_RETIRADA_NAO_ABERTA`
(código aceito mas a porta não abriu em 15 s).

O código digitado nunca vai para o log nem para `GET /api/arduino`.
