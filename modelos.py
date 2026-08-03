"""
modelos.py — Estruturas de dados do servidor (tcpapo-chat-message)

Dono: DEV A. Não editado por outra pessoa.

Responsabilidade:
    - Classe Cliente: representa uma conexão ativa (nome, socket, endereco,
      sala_atual — valor padrão "geral").
    - Classe RegistroClientes: dicionário nome_usuario -> Cliente, protegido
      por um único Lock global compartilhado entre todas as threads do
      servidor (uma thread por cliente conectado).

Referência: seções 3 e 4 da Especificação de Arquitetura.

Regra crítica de concorrência (seção 3):
    Operações de envio de rede (socket.send) para MÚLTIPLOS destinatários
    (broadcast) NÃO devem ocorrer enquanto o lock está retido. O padrão
    correto é:
        1. Adquirir o lock.
        2. Copiar a lista de destinatários relevantes (ex: todos os
           clientes de uma sala) para uma lista local.
        3. Liberar o lock.
        4. Iterar a lista local e fazer os envios — já sem o lock.

--------------------------------------------------------------------------
TODO (Dev A) — nesta ordem:
--------------------------------------------------------------------------

1. class Cliente:
       Atributos: nome (str), socket (socket.socket), endereco (tuple),
       sala_atual (str, padrão "geral").
       Considerar __repr__ útil para debug.

2. class RegistroClientes:
       Atributo interno: dict nome -> Cliente, e um threading.Lock().

       Métodos (todos adquirindo o lock internamente):
           adicionar(cliente: Cliente) -> bool
               Retorna False se o nome já existir (login duplicado);
               True e adiciona se o nome for livre.
           remover(nome: str) -> None
               Remove do dicionário se existir; não deve lançar erro se
               o nome já não existir (idempotente).
           buscar(nome: str) -> Cliente | None
           listar_todos() -> list[tuple[str, str]]
               Retorna pares (nome, sala_atual) de TODOS os conectados
               (requisito: listagem mostra todos, não só os da sala do
               solicitante).
           listar_por_sala(sala: str) -> list[Cliente]
               Usado para broadcast — quem vai chamar isso deve copiar o
               retorno e liberar o lock antes de enviar (ver regra crítica
               acima). Considerar retornar já uma cópia da lista (não a
               referência viva ao dict) para segurança.

3. Testar esta classe isoladamente antes de integrar com servidor.py —
   ver tests/test_servidor.py (casos de concorrência: múltiplas threads
   adicionando/removendo ao mesmo tempo).
"""

import threading  # noqa: F401


class Cliente:
    """TODO (Dev A): implementar conforme o item 1 acima."""

    def __init__(self, nome, sock, endereco, sala_atual="geral"):
        raise NotImplementedError


class RegistroClientes:
    """TODO (Dev A): implementar conforme o item 2 acima."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clientes = {}  # nome -> Cliente

    def adicionar(self, cliente: Cliente) -> bool:
        raise NotImplementedError

    def remover(self, nome: str) -> None:
        raise NotImplementedError

    def buscar(self, nome: str):
        raise NotImplementedError

    def listar_todos(self):
        raise NotImplementedError

    def listar_por_sala(self, sala: str):
        raise NotImplementedError
