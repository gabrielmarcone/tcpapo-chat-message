# tcpapo-chat-message

Sistema de chat em tempo real com arquitetura cliente-servidor via sockets
TCP, com autenticação por nome único, mensagens gerais e privadas, salas
temáticas e listagem de usuários conectados.

Trabalho prático da disciplina **Redes de Computadores 2**.

## Autores

- **Dev A** — [nome completo] — servidor (`modelos.py`, `servidor.py`)
- **Dev B** — [nome completo] — cliente (`cliente_app.py`)

## Requisitos

- Python 3.10+ (apenas biblioteca padrão: `socket`, `threading`, `json`,
  `argparse` — nenhuma dependência externa necessária)
- `pytest` apenas para rodar a suíte de testes (`pip install pytest`)

## Como executar

**Servidor** (escuta em todas as interfaces de rede, `0.0.0.0`):

```bash
python servidor.py --porta 5000
```

**Cliente** (aponte para o IP real da máquina que está rodando o
servidor — nunca `localhost`/`127.0.0.1` no dia do teste em laboratório):

```bash
python cliente_app.py --ip 192.168.0.10 --porta 5000
```

Para descobrir o IP da máquina-servidor no laboratório:

- Windows: `ipconfig`
- Linux/Mac: `ip addr` ou `ifconfig`

## Comandos disponíveis no cliente

| Comando | Efeito |
|---|---|
| `<texto livre>` | Mensagem para todos na sala atual (chat geral, por padrão) |
| `/priv <nome> <texto>` | Mensagem privada para `<nome>` |
| `/lista` | Lista todos os usuários conectados e a sala de cada um |
| `/entrar <sala>` | Entra (ou cria) a sala `<sala>` |
| `/sair_sala` | Volta para a sala `"geral"` |
| `/sair` | Encerra a conexão de forma controlada |

## Estrutura do projeto

```
tcpapo-chat-message/
├── protocolo.py          # Protocolo de aplicação (Conjunto)
├── modelos.py             # Estruturas de dados do servidor (Dev A)
├── servidor.py            # Servidor (Dev A)
├── cliente_app.py         # Cliente (Dev B)
├── dev_tools/
│   ├── cliente_stub.py    # Cliente simulado p/ testar o servidor isolado (Dev A)
│   └── servidor_stub.py   # Servidor simulado p/ testar o cliente isolado (Dev B)
├── tests/
│   ├── test_protocolo.py  # Testes do protocolo (Conjunto)
│   ├── test_servidor.py   # Testes do servidor (Dev A)
│   └── test_cliente.py    # Testes do cliente (Dev B)
├── relatorio/
│   └── relatorio.md
├── README.md
└── .gitignore
```

Ver `relatorio/relatorio.md` para arquitetura detalhada, justificativa
TCP/UDP e descrição completa do protocolo.

## Testes

```bash
python -m pytest tests/
```

## Protocolo de aplicação

Mensagens são objetos JSON, uma por linha, delimitadas por `\n`. Ver
`protocolo.py` para a lista completa de tipos de mensagem e a seção 2 do
relatório técnico para a descrição detalhada.
