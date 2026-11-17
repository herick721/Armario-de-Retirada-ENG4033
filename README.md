# Armário de Retirada — ENG4033

> README provisório, que será completado ao longo do semestre.

Projeto Integrado de Microcontroladores 2026.2, Tema D, em parceria com a Stone.

## A ideia

Um armário com portas travadas onde o cliente retira um pedido que pagou online,
sem precisar de atendente. Depois da compra, ele recebe um código de 6 dígitos
que vale uma única vez e não pode ser adivinhado. É só digitar o código no
teclado do armário que a porta certa abre.

O Arduino controla o hardware (travas, sensores, teclado, LCD) e o PC decide
tudo: valida os códigos, guarda os pedidos e mostra o painel do lojista.

## Stack

- **Firmware:** Arduino Uno (C++)
- **Backend:** Python 3 + FastAPI + MongoDB + pyserial
- **Interface:** HTML + HTMX servido pelo próprio backend

## Estrutura

```
Armario-de-Retirada-ENG4033/
├── README.md
├── docs/         <- contratos: protocolo serial, rotas da API, banco
├── firmware/     <- código do Arduino e esquemático
├── backend/      <- API em Python, banco, ponte serial e segurança
├── interface/    <- loja web, painel do lojista e mensagens do LCD
└── montagem/     <- medidas, arquivos de corte e peças 3D
```

## Equipe

| Parte | Responsável |
|---|---|
| Firmware | Aluno 1 |
| Backend e segurança | Herick Pinheiro |
| Interface e montagem | Aluno 3 |
