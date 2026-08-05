"""
usuarios.py — Cadastro de usuários e autenticação por senha (tcpapo-chat-message)

Responsabilidade:
    - Persistir em SQLite o cadastro de usuários (nome + salt + hash da
      senha), usado para autenticação no login.
    - Autenticar um par (nome, senha): se o nome ainda não existe no
      banco, CRIA o cadastro nesse primeiro uso (decisão de design: não
      há etapa separada de "criar conta" — ver relatório); se já existe,
      confere a senha contra o hash salvo.

Decisões de design (registrar no relatório):
    - A senha NUNCA é gravada em texto puro. Guardamos
      sha256(salt + senha), com um salt aleatório de 16 bytes por
      usuário (secrets.token_hex), gerado uma vez no cadastro e
      reaproveitado em toda verificação seguinte. Um salt por usuário
      impede que dois usuários com a mesma senha tenham o mesmo hash no
      banco, e invalida ataques de tabela pré-computada (rainbow table)
      genérica.
    - hashlib.sha256 é da stdlib (sem dependência externa) e mais que
      suficiente pra um trabalho acadêmico, mas é uma função de hash
      RÁPIDA — isso a torna fraca contra força bruta/dicionário offline
      caso o banco vaze (hardware comum testa bilhões de tentativas por
      segundo). bcrypt/scrypt/argon2 existem justamente para serem
      LENTOS de propósito, o que é a escolha correta em produção. Aqui
      ficamos com sha256+salt por simplicidade — evita o erro mais grave
      (senha em texto puro) — mas essa limitação deve constar
      explicitamente no relatório como ressalva consciente, não omissão.
    - Comparação do hash feita com hmac.compare_digest (tempo constante)
      em vez de "==", para não abrir margem a timing attack — custa uma
      linha e mostra atenção ao detalhe.
    - Nome usado como chave é casefold() — mesma convenção de
      RegistroClientes em modelos.py, "Alice" e "alice" são a mesma
      conta.
    - Mesmo padrão de concorrência de persistencia.py: uma única conexão
      SQLite compartilhada entre threads, protegida por um Lock.
"""

import hashlib
import hmac
import secrets
import sqlite3
import threading
from typing import Optional, Tuple

CAMINHO_BANCO_USUARIOS_PADRAO = "chat_usuarios.db"
TAMANHO_SALT_BYTES = 16


class Usuarios:
    """
    Cadastro de usuários (nome -> salt + hash da senha), com autenticação
    que também faz o registro automático no primeiro uso.

    Uma única instância é compartilhada entre todas as threads do
    servidor (uma por cliente conectado) — por isso o Lock em toda
    operação que toca o banco (mesmo racional de persistencia.Historico).
    """

    def __init__(self, caminho_banco: str = CAMINHO_BANCO_USUARIOS_PADRAO):
        self._lock = threading.Lock()
        self._conexao = sqlite3.connect(caminho_banco, check_same_thread=False)
        self._criar_tabela()

    def _criar_tabela(self) -> None:
        with self._lock:
            self._conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS usuarios (
                    nome TEXT PRIMARY KEY,
                    salt TEXT NOT NULL,
                    hash_senha TEXT NOT NULL
                )
                """
            )
            self._conexao.commit()

    @staticmethod
    def _chave(nome: str) -> str:
        return nome.casefold()

    @staticmethod
    def _calcular_hash(salt: str, senha: str) -> str:
        return hashlib.sha256((salt + senha).encode("utf-8")).hexdigest()

    def autenticar(self, nome: str, senha: str) -> Tuple[bool, Optional[str]]:
        """
        Autentica (ou cadastra, no primeiro uso) o par (nome, senha).

        Retorna (True, None) se autenticado com sucesso — conta nova
        criada agora, ou senha confere com a conta já existente.
        Retorna (False, motivo) se a senha não confere com a conta
        existente; `motivo` já vem pronto para protocolo.msg_login_erro.

        NÃO faz a checagem de "nome já está online agora" — isso
        continua responsabilidade de RegistroClientes, chamada ANTES
        desta função em servidor.py:_processar_login (ver seção 3 do
        passo a passo).
        """
        chave = self._chave(nome)
        with self._lock:
            cursor = self._conexao.execute(
                "SELECT salt, hash_senha FROM usuarios WHERE nome = ?", (chave,)
            )
            linha = cursor.fetchone()

            if linha is None:
                # Primeiro uso deste nome: cadastra agora, com a senha
                # informada. Sem etapa separada de "criar conta" —
                # decisão de design registrada no relatório.
                salt = secrets.token_hex(TAMANHO_SALT_BYTES)
                hash_senha = self._calcular_hash(salt, senha)
                self._conexao.execute(
                    "INSERT INTO usuarios (nome, salt, hash_senha) VALUES (?, ?, ?)",
                    (chave, salt, hash_senha),
                )
                self._conexao.commit()
                return True, None

            salt, hash_salvo = linha
            hash_calculado = self._calcular_hash(salt, senha)
            if hmac.compare_digest(hash_calculado, hash_salvo):
                return True, None
            return False, "senha incorreta"

    def fechar(self) -> None:
        with self._lock:
            self._conexao.close()