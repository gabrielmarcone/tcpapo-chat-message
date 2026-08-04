"""
persistencia.py — Persistência de histórico de mensagens (tcpapo-chat-message)

Responsabilidade:
    Guardar em SQLite (biblioteca padrão, sem dependência externa) cada
    mensagem geral roteada pelo servidor, e devolver as mais recentes de
    uma sala quando um cliente pede via o comando /historico.

Decisões de design (registrar no relatório):
    - Só mensagem GERAL é persistida, nunca privada. Logar conversa
      privada em disco é uma escolha de privacidade que não deveria ser
      tomada silenciosamente — fica de fora por padrão.
    - Sob demanda (comando /historico), não despejado automaticamente ao
      entrar numa sala — menos invasivo, e mais fácil de testar/depurar.
    - SQLite em vez de arquivo de texto: permite pedir "as últimas N
      mensagens desta sala" com uma query simples (ORDER BY + LIMIT), em
      vez de reler e filtrar um arquivo inteiro toda vez.
    - Uma única conexão SQLite compartilhada entre todas as threads,
      protegida por um Lock — mesmo padrão já usado em
      RegistroClientes (modelos.py). sqlite3 não permite usar a mesma
      conexão de threads diferentes sem cuidado extra
      (check_same_thread=False resolve o lado do driver; o Lock resolve
      o lado da lógica, evitando que dois INSERTs concorrentes
      atrapalhem um ao outro).
"""

import sqlite3
import threading
from datetime import datetime
from typing import Optional

CAMINHO_BANCO_PADRAO = "chat_historico.db"
LIMITE_PADRAO = 20
LIMITE_MAXIMO = 100  # teto — impede pedir a tabela inteira de uma vez


class Historico:
    """
    Guarda e recupera mensagens gerais por sala, em SQLite.

    Uma única instância é compartilhada entre todas as threads do
    servidor (uma por cliente conectado) — por isso o Lock em toda
    operação que toca o banco.
    """

    def __init__(self, caminho_banco: str = CAMINHO_BANCO_PADRAO):
        self._lock = threading.Lock()
        # check_same_thread=False: precisamos que a MESMA conexão seja
        # usada por várias threads (uma por cliente) — sem isso, sqlite3
        # levanta erro se um objeto de conexão for usado por uma thread
        # diferente da que o criou. O Lock acima garante que o acesso,
        # mesmo vindo de threads diferentes, nunca acontece ao mesmo
        # tempo de verdade.
        self._conexao = sqlite3.connect(caminho_banco, check_same_thread=False)
        self._criar_tabela()

    def _criar_tabela(self) -> None:
        with self._lock:
            self._conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS mensagens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sala TEXT NOT NULL,
                    remetente TEXT NOT NULL,
                    texto TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            self._conexao.commit()

    def registrar(self, sala: str, remetente: str, texto: str) -> None:
        """Grava uma mensagem geral no histórico da sala informada."""
        agora = datetime.now().isoformat()
        with self._lock:
            self._conexao.execute(
                "INSERT INTO mensagens (sala, remetente, texto, timestamp) VALUES (?, ?, ?, ?)",
                (sala, remetente, texto, agora),
            )
            self._conexao.commit()

    def buscar_recentes(self, sala: str, limite: Optional[int] = None) -> list:
        """
        Retorna as `limite` mensagens mais recentes da sala, em ORDEM
        CRONOLÓGICA (mais antiga primeiro — igual a rolar pra cima num
        chat de verdade), já formatadas para exibição:
        [{"remetente": ..., "texto": ..., "hora": "HH:MM:SS"}, ...]

        `limite` é normalizado aqui (não confia no valor cru vindo da
        rede): None ou inválido vira o padrão; qualquer valor acima do
        teto é reduzido ao teto — protege contra um cliente pedindo um
        número absurdo de mensagens de uma vez.
        """
        limite = self._normalizar_limite(limite)

        with self._lock:
            cursor = self._conexao.execute(
                "SELECT remetente, texto, timestamp FROM mensagens "
                "WHERE sala = ? ORDER BY id DESC LIMIT ?",
                (sala, limite),
            )
            linhas = cursor.fetchall()

        linhas.reverse()  # veio mais-novo-primeiro; queremos cronológico
        return [
            {"remetente": remetente, "texto": texto, "hora": _formatar_hora(timestamp)}
            for remetente, texto, timestamp in linhas
        ]

    @staticmethod
    def _normalizar_limite(limite: Optional[int]) -> int:
        if not isinstance(limite, int) or isinstance(limite, bool) or limite <= 0:
            return LIMITE_PADRAO
        return min(limite, LIMITE_MAXIMO)

    def fechar(self) -> None:
        with self._lock:
            self._conexao.close()


def _formatar_hora(timestamp_iso: str) -> str:
    """'2026-08-04T14:32:05.123456' -> '14:32:05' (mesmo formato usado no
    resto da interface, tanto no console do servidor quanto no cliente)."""
    return datetime.fromisoformat(timestamp_iso).strftime("%H:%M:%S")