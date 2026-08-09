# 💬 tcpapo-chat-message

> Um chat multiusuário em tempo real, cliente-servidor sobre sockets TCP puros, construído do zero com a biblioteca padrão do Python.

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependências](https://img.shields.io/badge/depend%C3%AAncias%20em%20produ%C3%A7%C3%A3o-nenhuma-brightgreen.svg)](#-tecnologias)
[![Testes](https://img.shields.io/badge/testes-288%20passing-success.svg)](#-testes)
[![Status](https://img.shields.io/badge/status-completo-success.svg)](#-funcionalidades)

---

## 📌 Sobre o projeto

**tcpapo-chat-message** é um sistema de chat em tempo real, com
arquitetura cliente-servidor clássica sobre **sockets TCP**, feito para
a disciplina de Redes de Computadores. Sem frameworks, sem
dependências externas em produção — só `socket`, `threading` e o resto
da biblioteca padrão do Python, do jeito que a disciplina pede.

O servidor aceita múltiplos clientes simultâneos (uma thread por
conexão), autentica com senha, organiza conversas em salas, guarda
histórico de mensagens em disco e ainda deixa tudo bonito no terminal —
com cor por usuário, símbolos de status, e uns easter eggs pra alegrar
a demonstração.

---

## 🏗️ Arquitetura

Projeto em módulos, cada um com uma responsabilidade única:

```text
tcpapo-chat-message/
├── protocolo.py      # Vocabulário comum: tipos de mensagem, serialização JSON e framing
├── modelos.py         # Estado do servidor: Cliente e RegistroClientes (thread-safe)
├── persistencia.py    # Histórico de mensagens em SQLite
├── usuarios.py         # Cadastro de usuários e autenticação por senha (SQLite)
├── servidor.py          # Servidor TCP: accept loop, login, roteamento de mensagens
└── cliente_app.py        # Cliente de terminal: conexão, comandos, interface colorida, reconexão automática
```

**Servidor**: uma thread principal em loop de `accept()`; cada cliente
aceito ganha sua própria thread, do login até a desconexão. Um segundo
socket, UDP, roda numa thread à parte só para responder a pedidos de
descoberta automática (ver seção de Funcionalidades).
**Protocolo**: mensagens JSON, uma por linha, delimitadas por `\n` —
simples e tolerante a fragmentação do TCP.
**Concorrência**: um lock global serializa eventos que precisam manter
ordem (login, troca de sala); cada cliente tem seu próprio lock de
envio, pra mensagens de threads diferentes nunca se misturarem no
mesmo socket.
**Cliente**: uma thread de fundo cuida da recepção de mensagens e,
se a conexão cair de forma inesperada, assume também a reconexão
automática — reautenticando e restaurando a sessão sozinha, sem
travar a thread principal (que continua livre pra ler o teclado).

---

## ⚡ Funcionalidades

### 🟢 Núcleo
- [x] Comunicação em tempo real entre múltiplos clientes via TCP
- [x] Servidor multi-thread (uma thread por cliente conectado)
- [x] Protocolo próprio: mensagens JSON com framing por linha
- [x] Nome de usuário único, sem distinção de maiúsculas/minúsculas
- [x] Mensagens gerais (broadcast restrito à sala do remetente)
- [x] Mensagens privadas entre usuários
- [x] Notificação automática de entrada/saída
- [x] Listagem de usuários conectados, com a sala de cada um
- [x] Salas temáticas, criadas livremente (`/entrar <sala>`)
- [x] Encerramento controlado (`/sair`) e tratamento de queda abrupta

### 🔌 Reconexão automática
- [x] Detecta queda inesperada da conexão (servidor caiu, rede falhou) e tenta reconectar sozinho, sem precisar reiniciar o cliente
- [x] Espera exponencial entre tentativas — 2s, 4s, 8s, 16s, até um teto de 30s — sem martelar o servidor
- [x] Reautentica automaticamente com o mesmo nome e senha do login original, e restaura a sala em que o usuário estava
- [x] Recupera o histórico recente da sala ao reconectar, para não perder o que foi trocado durante a queda
- [x] Desiste com aviso claro após 5 minutos de tentativas sem sucesso — nunca fica tentando para sempre
- [x] Cancelável a qualquer momento com `/sair` ou Ctrl+C, mesmo no meio de uma tentativa

### 🔍 Descoberta automática de servidor
- [x] `--descobrir` como alternativa a `--ip`: encontra o servidor sozinho na rede local via UDP broadcast, sem precisar saber o endereço de antemão
- [x] Se mais de um servidor responder (dois grupos testando ao mesmo tempo no laboratório, por exemplo), mostra uma lista para escolher
- [x] Recurso independente do chat em si: se a rede bloquear broadcast ou a porta de descoberta estiver ocupada, o TCP continua funcionando normalmente — só a descoberta fica indisponível, com aviso claro
- [x] `--ip` continua funcionando exatamente como sempre, sem nenhuma mudança — a descoberta é só mais uma opção, nunca obrigatória

### 🔒 Persistência & segurança
- [x] Cadastro automático no primeiro login (sem etapa separada de "criar conta")
- [x] Senha protegida com **hash + salt aleatório por usuário**, nunca gravada em texto puro
- [x] Comparação de senha em **tempo constante** (`hmac.compare_digest`), contra timing attack
- [x] Histórico de mensagens persistente em SQLite, sobrevive a reinícios do servidor
- [x] Histórico isolado automaticamente por porta (duas instâncias nunca misturam dados sem querer)

### 🎨 Interface de terminal
- [x] Cor consistente por usuário (estilo IRC/Discord antigo, hash do nome → cor)
- [x] Mensagens de sistema com símbolo padronizado (✓ sucesso, ✗ erro, ⚠ aviso)
- [x] Sua própria mensagem enviada aparece destacada, diferente das dos outros
- [x] Timestamps em todas as mensagens
- [x] Comando `/limpar` para limpar a tela sem perder o histórico salvo no servidor

### 🐣 Easter eggs
- **`/cafe`** — porque toda madrugada de código precisa de uma pausa
- **`/minecraft`** — um creeper em ASCII art, bem verde
- **`/batman`** — o sinal do morcego, pra quando o bug já é meia-noite

---

## 🛠️ Tecnologias

Todo o programa roda só com a **biblioteca padrão do Python** — zero
dependência externa para usar o chat:

| Módulo | Uso no projeto |
| :--- | :--- |
| [`socket`](https://docs.python.org/3/library/socket.html) | Comunicação TCP cliente-servidor |
| [`threading`](https://docs.python.org/3/library/threading.html) | Uma thread por cliente no servidor; thread de recepção no cliente |
| [`json`](https://docs.python.org/3/library/json.html) | Serialização das mensagens do protocolo |
| [`argparse`](https://docs.python.org/3/library/argparse.html) | Configuração via linha de comando |
| [`sqlite3`](https://docs.python.org/3/library/sqlite3.html) | Persistência de histórico e cadastro de usuários |
| [`hashlib`](https://docs.python.org/3/library/hashlib.html) / [`hmac`](https://docs.python.org/3/library/hmac.html) / [`secrets`](https://docs.python.org/3/library/secrets.html) | Hash de senha com salt e comparação segura |
| [`getpass`](https://docs.python.org/3/library/getpass.html) | Senha digitada sem eco no terminal |

**Só para desenvolvimento** (não é necessário pra rodar o chat):

- [`pytest`](https://docs.pytest.org/) — suíte de testes automatizados
- [`pytest-cov`](https://pytest-cov.readthedocs.io/) — relatório de cobertura

---

## 🚀 Começando

### Pré-requisitos

- **Python 3.10+**

### Instalação

```bash
git clone https://github.com/gabrielmarcone/tcpapo-chat-message.git
cd tcpapo-chat-message
```

Não tem nenhuma instalação a fazer pra rodar o chat em si — é só
Python puro. Se quiser rodar os testes também:

```bash
python -m venv .venv
source .venv/bin/activate       # Linux/Mac
.venv\Scripts\activate          # Windows

pip install pytest pytest-cov
```

---

## 💻 Executando

### 1. Suba o servidor

```bash
# porta padrão: 5000, escutando em todas as interfaces de rede
python servidor.py --porta 5000
```

<details>
<summary>Outras opções do servidor</summary>

| Argumento | Padrão | Descrição |
| :--- | :--- | :--- |
| `--porta` | `5000` | Porta TCP de escuta |
| `--banco` | isolado por porta | Arquivo SQLite do histórico (padrão: `chat_historico_<porta>.db`) |
| `--banco-usuarios` | `chat_usuarios.db` | Arquivo SQLite do cadastro de usuários |
| `--porta-descoberta` | `5001` | Porta UDP para responder a pedidos de descoberta automática |
| `--sem-descoberta` | desativado | Desliga a descoberta automática (o chat TCP não é afetado) |

</details>

### 2. Conecte um cliente

Em outro terminal (ou outra máquina da rede), de duas formas:

**Informando o IP manualmente:**

```bash
python cliente_app.py --ip 127.0.0.1 --porta 5000
```

> Troque `127.0.0.1` pelo IP real do servidor se estiver testando
> entre máquinas diferentes (`ipconfig` no Windows, `ip addr` no
> Linux/Mac). O servidor escuta em `0.0.0.0`, então aceita conexões de
> qualquer máquina da rede local — só cuide da liberação de firewall na
> máquina que roda o servidor.

**Ou deixando o cliente encontrar o servidor sozinho:**

```bash
python cliente_app.py --descobrir
```

Manda um broadcast UDP na rede local e conecta automaticamente a quem
responder — útil quando não se sabe o IP do servidor de antemão. Se
mais de um servidor responder, o cliente mostra uma lista para
escolher. Não funciona através de roteador (só na mesma rede local) e
pode ser bloqueado por Wi-Fi com isolamento de cliente ativado — nesse
caso, use `--ip` manualmente.

Ao conectar (por qualquer uma das duas formas), escolha um apelido e
uma senha. Primeiro login com um nome novo já cadastra a senha; nos
seguintes, a mesma senha é exigida.

---

## 💬 Comandos disponíveis

| Comando | Descrição | Exemplo |
| :--- | :--- | :--- |
| `<texto livre>` | Mensagem para a sala atual | `oi pessoal` |
| `/priv <usuario> <mensagem>` | Mensagem privada | `/priv alice oi!` |
| `/lista` | Lista usuários conectados e suas salas | `/lista` |
| `/entrar <sala>` | Entra numa sala (criada se não existir) | `/entrar jogos` |
| `/sair_sala` | Volta para a sala `"geral"` | `/sair_sala` |
| `/historico [quantidade]` | Mensagens recentes da sala atual (padrão 20, máx. 100) | `/historico 5` |
| `/ajuda` | Mostra a lista de comandos novamente | `/ajuda` |
| `/limpar` | Limpa a tela (o histórico salvo no servidor não é afetado) | `/limpar` |
| `/sair` | Encerra a conexão | `/sair` |
| `Ctrl+C` | Encerra a conexão a qualquer momento | — |

Nomes de usuário e sala: sem espaço, até 30 caracteres, sem distinção
de maiúsculas/minúsculas.

---

## 🧪 Testes

```bash
python -m pytest tests/ -v
```

288 testes, cobrindo desde os módulos isolados até testes de
integração que sobem um servidor real e conectam clientes de teste de
verdade nele — login, broadcast, salas, privadas, histórico,
reconexão automática, descoberta via UDP e concorrência, tudo de
ponta a ponta.

Com relatório de cobertura:

```bash
python -m pytest tests/ --cov=modelos --cov=protocolo --cov=persistencia --cov=usuarios --cov=servidor --cov-report=term-missing
```

---

## 📄 Protocolo

Mensagens são objetos JSON, um por linha, delimitados por `\n`:

```json
{"tipo": "login", "nome": "alice", "senha": "minhasenha"}
```

Todo o vocabulário do protocolo vive em `protocolo.py` — nenhuma outra
parte do projeto monta ou interpreta JSON manualmente. A descoberta
automática de servidor usa esse mesmo formato, só que sobre UDP em vez
de TCP: cada datagrama já é uma mensagem completa por si só, sem
precisar do framing por `\n`. Detalhes completos da arquitetura e das
decisões de design em `relatorio/relatorio.md`.

---

## 👥 Autores

Trabalho desenvolvido em dupla para a disciplina de Redes de Computadores 2.

- **Caio Cordeiro Matos** - [GitHub](https://github.com/ccaiomatos)
- **Gabriel Marcone Magalhães Santos** — [GitHub](https://github.com/gabrielmarcone)