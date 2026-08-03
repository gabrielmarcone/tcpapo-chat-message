"""
tests/test_cliente.py — Testes do cliente (cliente_app.py)

Dono: DEV B. Não editado por outra pessoa.

--------------------------------------------------------------------------
TODO (Dev B) — casos mínimos a cobrir:
--------------------------------------------------------------------------

1. test_parser_mensagem_geral
       Texto sem "/" no início deve ser interpretado como mensagem_geral,
       gerando a mensagem correta via protocolo.py.

2. test_parser_comando_privada
       "/priv fulano oi tudo bem" deve extrair destinatario="fulano" e
       texto="oi tudo bem" corretamente.

3. test_parser_comando_privada_com_argumentos_faltando
       "/priv" ou "/priv fulano" (sem texto) deve gerar uma mensagem de
       uso amigável, sem lançar exceção não tratada.

4. test_parser_comando_lista
       "/lista" deve gerar a mensagem listar_usuarios, sem argumentos.

5. test_parser_comando_entrar_sala
       "/entrar jogos" deve gerar entrar_sala com sala="jogos".

6. test_parser_comando_sair_sala
       "/sair_sala" deve gerar sair_sala, sem argumentos.

7. test_parser_comando_sair
       "/sair" deve gerar a mensagem sair e sinalizar o encerramento do
       loop principal do cliente.

8. test_tratamento_erro_conexao_recusada
       Tentar conectar a uma porta sem servidor escutando deve resultar em
       mensagem amigável ao usuário, não um traceback cru.

9. test_tratamento_erro_ip_invalido
       IP malformado ou inalcançável deve ser tratado de forma
       equivalente ao item 8.
"""

import cliente_app  # noqa: F401
import protocolo  # noqa: F401


def test_parser_mensagem_geral():
    raise NotImplementedError


def test_parser_comando_privada():
    raise NotImplementedError


def test_parser_comando_privada_com_argumentos_faltando():
    raise NotImplementedError


def test_parser_comando_lista():
    raise NotImplementedError


def test_parser_comando_entrar_sala():
    raise NotImplementedError


def test_parser_comando_sair_sala():
    raise NotImplementedError


def test_parser_comando_sair():
    raise NotImplementedError


def test_tratamento_erro_conexao_recusada():
    raise NotImplementedError


def test_tratamento_erro_ip_invalido():
    raise NotImplementedError
