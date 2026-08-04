"""
tests/test_cliente.py — Testes automatizados do cliente (Dev B, etapa 6).

Dono: Desenvolvedor B.

Escopo:
    - TestParseComando: cobre integralmente parse_comando() (etapa 4),
      que é a lógica com maior densidade de regras de negócio no
      cliente e, por ser uma função pura (sem socket, sem I/O), não
      precisa de mocks.
    - TestEnviar: cobre enviar() (etapa 5) com um socket mockado
      (unittest.mock), validando que cada tipo de falha de rede é
      tratado sem levantar exceção para quem chama.

Não testamos aqui: conectar()/realizar_login()/main() fim-a-fim — essas
funções terminam o processo (sys.exit) ou bloqueiam em input()/recv(),
o que exigiria mocks bem mais elaborados para um ganho de cobertura
pequeno perto do que já é validado manualmente com
dev_tools/servidor_stub.py (ver TODO desse arquivo). Ficaria fora do
escopo pedido para a etapa 6, que é o parser de comandos.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

import cliente_app  # noqa: E402
import protocolo  # noqa: E402


class TestParseComandoTextoComum(unittest.TestCase):
    """Item 19 da etapa 6: texto comum (sem '/') vira mensagem geral."""

    def test_texto_simples_vira_mensagem_geral(self):
        acao, msg = cliente_app.parse_comando("oi pessoal")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_mensagem_geral_enviar("oi pessoal"))

    def test_texto_com_espacos_extras_e_removido_das_pontas(self):
        # Item 27: casos de borda — espaços extras nas pontas.
        acao, msg = cliente_app.parse_comando("   oi pessoal   ")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "oi pessoal")

    def test_mensagem_vazia_e_ignorada(self):
        # Item 27: caso de borda — mensagem vazia.
        acao, msg = cliente_app.parse_comando("")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_mensagem_so_com_espacos_e_ignorada(self):
        acao, msg = cliente_app.parse_comando("     ")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_texto_com_quebra_de_linha_no_final_e_tratado_como_texto_simples(self):
        # Item 26: texto contendo '\n' — normalmente input() já retira o
        # '\n' final sozinho, mas parse_comando não deve depender disso:
        # o .strip() interno cobre esse caso também.
        acao, msg = cliente_app.parse_comando("oi pessoal\n")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "oi pessoal")

    def test_texto_com_quebra_de_linha_interna_e_preservada(self):
        # Item 26: '\n' NO MEIO do texto (ex: colado de outro lugar) não
        # é removido — só as pontas são tratadas por .strip(). O texto
        # em si segue livre; framing de linha é responsabilidade de
        # protocolo.py, não de parse_comando().
        acao, msg = cliente_app.parse_comando("linha1\nlinha2")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "linha1\nlinha2")


class TestParseComandoPriv(unittest.TestCase):
    """Item 20: /priv <usuario> <mensagem>."""

    def test_priv_valido(self):
        acao, msg = cliente_app.parse_comando("/priv alice oi tudo bem?")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(
            msg, protocolo.msg_mensagem_privada_enviar("alice", "oi tudo bem?")
        )

    def test_priv_case_insensitive_no_nome_do_comando(self):
        acao, msg = cliente_app.parse_comando("/PRIV alice oi")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["destinatario"], "alice")

    def test_priv_sem_destinatario_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/priv")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_priv_sem_mensagem_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/priv alice")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_priv_com_mensagem_so_espacos_e_invalido(self):
        # Item 27: caso de borda — argumento presente mas vazio na prática.
        acao, msg = cliente_app.parse_comando("/priv alice    ")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoLista(unittest.TestCase):
    """Item 21: /lista."""

    def test_lista_valido(self):
        acao, msg = cliente_app.parse_comando("/lista")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_listar_usuarios())

    def test_lista_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/lista geral")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoEntrar(unittest.TestCase):
    """Item 22: /entrar <sala>."""

    def test_entrar_valido(self):
        acao, msg = cliente_app.parse_comando("/entrar jogos")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_entrar_sala("jogos"))

    def test_entrar_sem_sala_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/entrar")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_entrar_com_sala_so_espacos_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/entrar    ")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoSairSala(unittest.TestCase):
    """Item 23: /sair_sala."""

    def test_sair_sala_valido(self):
        acao, msg = cliente_app.parse_comando("/sair_sala")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_sair_sala())

    def test_sair_sala_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/sair_sala geral")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoHistorico(unittest.TestCase):
    """/historico [quantidade] — persistência de histórico de mensagens."""

    def test_historico_sem_argumento_usa_padrao_do_servidor(self):
        acao, msg = cliente_app.parse_comando("/historico")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_historico())
        self.assertNotIn("limite", msg)  # servidor decide o padrao, cliente nao envia nada

    def test_historico_com_quantidade_valida(self):
        acao, msg = cliente_app.parse_comando("/historico 10")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_historico(10))

    def test_historico_case_insensitive_no_nome_do_comando(self):
        acao, msg = cliente_app.parse_comando("/HISTORICO 5")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_historico(5))

    def test_historico_com_quantidade_zero_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/historico 0")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_historico_com_quantidade_negativa_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/historico -5")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_historico_com_texto_nao_numerico_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/historico geral")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_historico_com_quantidade_decimal_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/historico 5.5")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoSair(unittest.TestCase):
    """Item 24: /sair."""

    def test_sair_valido(self):
        acao, msg = cliente_app.parse_comando("/sair")
        self.assertEqual(acao, cliente_app.ACAO_SAIR)
        self.assertIsNone(msg)

    def test_sair_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/sair agora")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoInvalido(unittest.TestCase):
    """Item 25: comandos desconhecidos/malformados."""

    def test_comando_desconhecido(self):
        acao, msg = cliente_app.parse_comando("/naoexiste")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_barra_sozinha(self):
        acao, msg = cliente_app.parse_comando("/")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestValidarPorta(unittest.TestCase):
    """Etapa 5: validação de --porta (usada pelo argparse)."""

    def test_porta_valida(self):
        self.assertEqual(cliente_app.validar_porta("5000"), 5000)

    def test_porta_nao_numerica_levanta_erro_de_argumento(self):
        with self.assertRaises(cliente_app.argparse.ArgumentTypeError):
            cliente_app.validar_porta("abc")

    def test_porta_fora_da_faixa_levanta_erro_de_argumento(self):
        with self.assertRaises(cliente_app.argparse.ArgumentTypeError):
            cliente_app.validar_porta("99999")

    def test_porta_zero_levanta_erro_de_argumento(self):
        with self.assertRaises(cliente_app.argparse.ArgumentTypeError):
            cliente_app.validar_porta("0")


class TestEnviar(unittest.TestCase):
    """
    Etapa 5: enviar() não deve nunca deixar uma exceção de rede escapar
    para quem chama — sempre retorna True/False. Usa um socket mockado
    (unittest.mock), já que não queremos um socket TCP real neste teste.
    """

    def test_envio_bem_sucedido_retorna_true(self):
        sock = MagicMock()
        resultado = cliente_app.enviar(sock, protocolo.msg_listar_usuarios())
        self.assertTrue(resultado)
        sock.sendall.assert_called_once()

    def test_broken_pipe_retorna_false_sem_levantar_excecao(self):
        sock = MagicMock()
        sock.sendall.side_effect = BrokenPipeError()
        resultado = cliente_app.enviar(sock, protocolo.msg_listar_usuarios())
        self.assertFalse(resultado)

    def test_connection_reset_retorna_false_sem_levantar_excecao(self):
        sock = MagicMock()
        sock.sendall.side_effect = ConnectionResetError()
        resultado = cliente_app.enviar(sock, protocolo.msg_listar_usuarios())
        self.assertFalse(resultado)

    def test_os_error_generico_retorna_false_sem_levantar_excecao(self):
        sock = MagicMock()
        sock.sendall.side_effect = OSError("falha genérica")
        resultado = cliente_app.enviar(sock, protocolo.msg_listar_usuarios())
        self.assertFalse(resultado)

    def test_mensagem_malformada_retorna_false_sem_levantar_excecao(self):
        sock = MagicMock()
        resultado = cliente_app.enviar(sock, {"sem_tipo": True})
        self.assertFalse(resultado)
        sock.sendall.assert_not_called()


class TestConectarErros(unittest.TestCase):
    """
    Etapa 5: conectar() encerra o processo (sys.exit) com mensagem
    amigável para cada tipo de falha, em vez de deixar a exceção subir
    crua. Testamos isso patchando socket.socket para devolver um mock
    cujo connect() levanta a exceção desejada.
    """

    def _testar_erro_de_connect(self, excecao):
        sock_mock = MagicMock()
        sock_mock.connect.side_effect = excecao
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            with self.assertRaises(SystemExit):
                cliente_app.conectar("192.0.2.1", 5000)
        sock_mock.close.assert_called_once()

    def test_conexao_recusada(self):
        self._testar_erro_de_connect(ConnectionRefusedError())

    def test_ip_invalido(self):
        import socket as socket_module
        self._testar_erro_de_connect(socket_module.gaierror())

    def test_timeout(self):
        import socket as socket_module
        self._testar_erro_de_connect(socket_module.timeout())

    def test_os_error_generico(self):
        self._testar_erro_de_connect(OSError("falha genérica"))


if __name__ == "__main__":
    unittest.main()