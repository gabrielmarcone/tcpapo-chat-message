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
    (broadcast) NÃO devem ocorrer enquanto o lock está retido. Por isso
    listar_por_sala() e listar_todos() sempre devolvem uma CÓPIA (uma nova
    lista), tirada e devolvida com o lock já liberado — quem chama nunca
    precisa (nem deve) segurar o lock deste objeto durante o envio de rede.

Nota de concorrência adicional (não estava no TODO original, mas é
necessária): buscar() devolve a referência viva ao objeto Cliente, porque
servidor.py precisa alterar sala_atual dele ao processar entrar_sala/
sair_sala. Se essa mutação fosse feita diretamente por quem chama
(cliente.sala_atual = nova_sala, fora do lock), ela poderia correr em
paralelo com uma leitura concorrente em listar_por_sala()/listar_todos()
feita por outra thread no meio de um broadcast. Por isso existe o método
mudar_sala(nome, nova_sala), que faz a mutação sob o lock — servidor.py
deve sempre usar esse método para trocar a sala de um cliente, nunca
atribuir sala_atual diretamente depois de um buscar().
"""

import threading
from typing import Optional


class Cliente:
    """
    Representa uma conexão de cliente ativa no servidor.

    Atributos:
        nome: apelido único do usuário (chave no RegistroClientes).
        socket: socket TCP da conexão com este cliente.
        endereco: endereço (ip, porta) de origem, como retornado por
            socket.accept().
        sala_atual: nome da sala em que o cliente está agora. Nunca deve
            ser alterado diretamente por código fora deste módulo — usar
            RegistroClientes.mudar_sala() (ver nota de concorrência acima).
    """

    def __init__(self, nome: str, sock, endereco, sala_atual: str = "geral"):
        self.nome = nome
        self.socket = sock
        self.endereco = endereco
        self.sala_atual = sala_atual

    def __repr__(self) -> str:
        return (
            f"Cliente(nome={self.nome!r}, endereco={self.endereco!r}, "
            f"sala_atual={self.sala_atual!r})"
        )


class RegistroClientes:
    """
    Registro central de clientes conectados: dict nome -> Cliente,
    protegido por um único Lock global compartilhado entre todas as
    threads do servidor.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._clientes: dict[str, Cliente] = {}

    def adicionar(self, cliente: Cliente) -> bool:
        """
        Adiciona o cliente ao registro, se o nome ainda não estiver em
        uso. Retorna True se adicionou, False se o nome já existia (login
        duplicado) — quem chama decide o que fazer com False (ex:
        responder login_erro sem fechar a conexão).
        """
        with self._lock:
            if cliente.nome in self._clientes:
                return False
            self._clientes[cliente.nome] = cliente
            return True

    def remover(self, nome: str) -> None:
        """
        Remove o cliente do registro, se existir. Idempotente: chamar de
        novo para um nome já removido (ou nunca adicionado) não levanta
        erro — importante porque tanto a saída limpa quanto a desconexão
        abrupta chamam este método, e não há garantia de que só uma
        dessas duas vias vá disparar.
        """
        with self._lock:
            self._clientes.pop(nome, None)

    def buscar(self, nome: str) -> Optional[Cliente]:
        """
        Retorna o objeto Cliente associado a `nome`, ou None se não
        existir. Devolve a referência viva (não uma cópia) — ver nota de
        concorrência no topo do arquivo sobre por que sala_atual não deve
        ser mutado diretamente a partir do resultado deste método.
        """
        with self._lock:
            return self._clientes.get(nome)

    def mudar_sala(self, nome: str, nova_sala: str) -> bool:
        """
        Atualiza a sala_atual de um cliente já registrado, sob o lock
        global. Retorna True se o cliente existia e foi atualizado, False
        se o nome não estava (mais) registrado — pode acontecer se o
        cliente desconectou entre o momento em que a thread pegou o
        comando e o momento em que tentou aplicar a troca de sala.
        """
        with self._lock:
            cliente = self._clientes.get(nome)
            if cliente is None:
                return False
            cliente.sala_atual = nova_sala
            return True

    def listar_todos(self) -> list[tuple[str, str]]:
        """
        Retorna uma lista de pares (nome, sala_atual) de TODOS os
        clientes conectados — não só os da sala de quem pergunta, que é
        o requisito da seção 7 da Especificação para o comando /lista.
        """
        with self._lock:
            return [(cliente.nome, cliente.sala_atual) for cliente in self._clientes.values()]

    def listar_por_sala(self, sala: str) -> list[Cliente]:
        """
        Retorna uma NOVA lista (cópia) com os clientes cuja sala_atual é
        `sala`. Quem chama pode iterar essa lista e fazer os envios de
        broadcast livremente, já sem o lock retido (ele já foi liberado
        antes deste método retornar) — ver regra crítica no topo do
        arquivo.
        """
        with self._lock:
            return [cliente for cliente in self._clientes.values() if cliente.sala_atual == sala]

    def quantidade(self) -> int:
        """Número de clientes conectados agora. Útil para testes e para
        decisões operacionais simples (ex: log de status do servidor)."""
        with self._lock:
            return len(self._clientes)