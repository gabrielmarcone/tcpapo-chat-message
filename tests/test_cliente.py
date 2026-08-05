"""
tests/test_cliente.py — Testes automatizados do cliente (cliente_app.py)

Escopo:
    - TestParseComando: cobre integralmente parse_comando(), que é a
      lógica com maior densidade de regras de negócio no cliente e, por
      ser uma função pura (sem socket, sem I/O), não precisa de mocks.
    - TestEnviar: cobre enviar() com um socket mockado (unittest.mock),
      validando que cada tipo de falha de rede é tratado sem levantar
      exceção para quem chama.

Não testamos aqui: conectar()/realizar_login()/main() fim-a-fim — essas
funções terminam o processo (sys.exit) ou bloqueiam em input()/recv(),
o que exigiria mocks bem mais elaborados para um ganho de cobertura
pequeno perto do que já é validado com testes de integração reais
(processo cliente + servidor) e com dev_tools/servidor_stub.py.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

import cliente_app  # noqa: E402
import protocolo  # noqa: E402


class TestParseComandoTextoComum(unittest.TestCase):
    """Texto comum (sem '/') vira mensagem geral."""

    def test_texto_simples_vira_mensagem_geral(self):
        acao, msg = cliente_app.parse_comando("oi pessoal")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_mensagem_geral_enviar("oi pessoal"))

    def test_texto_com_espacos_extras_e_removido_das_pontas(self):
        # Caso de borda: espaços extras nas pontas.
        acao, msg = cliente_app.parse_comando("   oi pessoal   ")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "oi pessoal")

    def test_mensagem_vazia_e_ignorada(self):
        # Caso de borda: mensagem vazia.
        acao, msg = cliente_app.parse_comando("")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_mensagem_so_com_espacos_e_ignorada(self):
        acao, msg = cliente_app.parse_comando("     ")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_texto_com_quebra_de_linha_no_final_e_tratado_como_texto_simples(self):
        # Texto contendo '\n' — normalmente input() já retira o
        # '\n' final sozinho, mas parse_comando não deve depender disso:
        # o .strip() interno cobre esse caso também.
        acao, msg = cliente_app.parse_comando("oi pessoal\n")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "oi pessoal")

    def test_texto_com_quebra_de_linha_interna_e_preservada(self):
        # '\n' no meio do texto (ex: colado de outro lugar) não
        # é removido — só as pontas são tratadas por .strip(). O texto
        # em si segue livre; framing de linha é responsabilidade de
        # protocolo.py, não de parse_comando().
        acao, msg = cliente_app.parse_comando("linha1\nlinha2")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg["texto"], "linha1\nlinha2")


class TestParseComandoPriv(unittest.TestCase):
    """/priv <usuario> <mensagem>."""

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
        # Caso de borda: argumento presente mas vazio na prática.
        acao, msg = cliente_app.parse_comando("/priv alice    ")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoLista(unittest.TestCase):
    """/lista."""

    def test_lista_valido(self):
        acao, msg = cliente_app.parse_comando("/lista")
        self.assertEqual(acao, cliente_app.ACAO_ENVIAR)
        self.assertEqual(msg, protocolo.msg_listar_usuarios())

    def test_lista_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/lista geral")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoEntrar(unittest.TestCase):
    """/entrar <sala>."""

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
    """/sair_sala."""

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
    """/sair."""

    def test_sair_valido(self):
        acao, msg = cliente_app.parse_comando("/sair")
        self.assertEqual(acao, cliente_app.ACAO_SAIR)
        self.assertIsNone(msg)

    def test_sair_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/sair agora")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestParseComandoCafeEasterEgg(unittest.TestCase):
    """/cafe — comando secreto, 100% local (não manda nada ao servidor)."""

    def test_cafe_nao_envia_nada_ao_servidor(self):
        acao, msg = cliente_app.parse_comando("/cafe")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_cafe_case_insensitive(self):
        acao, msg = cliente_app.parse_comando("/CAFE")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_cafe_nao_aparece_na_ajuda(self):
        """Parte do ponto de ser um easter egg: fica de fora da lista de
        comandos que o cliente mostra ao conectar."""
        self.assertNotIn(cliente_app.COMANDO_CAFE, cliente_app.TEXTO_AJUDA_COMANDOS)

    def test_cafe_imprime_a_arte_e_nao_lanca_excecao(self):
        # só confirma que roda sem quebrar e produz alguma saída --
        # o conteúdo exato da arte é só estético, não vale testar
        # caractere por caractere.
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente_app.parse_comando("/cafe")
        self.assertGreater(len(saida.getvalue()), 0)


class TestParseComandoMinecraftEasterEgg(unittest.TestCase):
    """/minecraft — mesmo raciocínio de /cafe: secreto e 100% local."""

    def test_minecraft_nao_envia_nada_ao_servidor(self):
        acao, msg = cliente_app.parse_comando("/minecraft")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_minecraft_case_insensitive(self):
        acao, msg = cliente_app.parse_comando("/MINECRAFT")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_minecraft_nao_aparece_na_ajuda(self):
        self.assertNotIn(cliente_app.COMANDO_MINECRAFT, cliente_app.TEXTO_AJUDA_COMANDOS)

    def test_minecraft_imprime_a_arte_e_nao_lanca_excecao(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente_app.parse_comando("/minecraft")
        self.assertGreater(len(saida.getvalue()), 0)

    def test_arte_do_creeper_tem_todas_as_linhas_do_mesmo_tamanho(self):
        """Arte desalinhada (linhas de tamanhos diferentes) fica torta em
        qualquer terminal — confirma que isso nunca acontece por acidente
        numa edição futura."""
        linhas = [l for l in cliente_app._ARTE_CREEPER.split("\n") if l]
        larguras = {len(l) for l in linhas}
        self.assertEqual(len(larguras), 1, f"linhas com tamanhos diferentes: {larguras}")


class TestParseComandoBatmanEasterEgg(unittest.TestCase):
    """/batman — mesmo raciocínio de /cafe: secreto e 100% local."""

    def test_batman_nao_envia_nada_ao_servidor(self):
        acao, msg = cliente_app.parse_comando("/batman")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_batman_case_insensitive(self):
        acao, msg = cliente_app.parse_comando("/BATMAN")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_batman_nao_aparece_na_ajuda(self):
        self.assertNotIn(cliente_app.COMANDO_BATMAN, cliente_app.TEXTO_AJUDA_COMANDOS)

    def test_batman_imprime_a_arte_e_nao_lanca_excecao(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente_app.parse_comando("/batman")
        self.assertGreater(len(saida.getvalue()), 0)

    def test_arte_do_morcego_tem_todas_as_linhas_do_mesmo_tamanho(self):
        linhas = [l for l in cliente_app._ARTE_MORCEGO.split("\n") if l]
        larguras = {len(l) for l in linhas}
        self.assertEqual(len(larguras), 1, f"linhas com tamanhos diferentes: {larguras}")


class TestCorDoUsuario(unittest.TestCase):
    """Cor consistente por remetente (hash do nome), estilo IRC/Discord."""

    def test_mesmo_nome_sempre_a_mesma_cor(self):
        cor1 = cliente_app._cor_do_usuario("alice")
        cor2 = cliente_app._cor_do_usuario("alice")
        self.assertEqual(cor1, cor2)

    def test_nomes_com_case_diferente_tem_a_mesma_cor(self):
        """Mesma convenção do resto do sistema: 'Alice' e 'alice' são a
        mesma pessoa (RegistroClientes, usuarios.py), logo a mesma cor."""
        self.assertEqual(
            cliente_app._cor_do_usuario("Alice"),
            cliente_app._cor_do_usuario("alice"),
        )
        self.assertEqual(
            cliente_app._cor_do_usuario("BOB"),
            cliente_app._cor_do_usuario("bob"),
        )

    def test_cor_sempre_vem_da_paleta_de_usuario(self):
        for nome in ("alice", "bob", "carol", "dave", "eve", "um_nome_bem_longo_qualquer"):
            self.assertIn(cliente_app._cor_do_usuario(nome), cliente_app._Cor.USUARIO)

    def test_estavel_entre_execucoes_simulando_hash_randomizado(self):
        """
        A cor não pode depender de hash() nativo do Python (que muda a
        cada processo por causa de PYTHONHASHSEED aleatório) -- senão a
        cor de 'alice' mudaria toda vez que o cliente fosse reiniciado.
        Confirma isso rodando em dois subprocessos com seeds de hash
        DIFERENTES de propósito e comparando o resultado.
        """
        import subprocess

        codigo = (
            "import sys; sys.path.insert(0, '.'); import cliente_app; "
            "print(cliente_app._cor_do_usuario('alice'))"
        )
        resultado1 = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": "1"},
        )
        resultado2 = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": "2"},
        )
        self.assertEqual(resultado1.stdout.strip(), resultado2.stdout.strip())


class TestParseComandoInvalido(unittest.TestCase):
    """Comandos desconhecidos/malformados."""

    def test_comando_desconhecido(self):
        acao, msg = cliente_app.parse_comando("/naoexiste")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_barra_sozinha(self):
        acao, msg = cliente_app.parse_comando("/")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)


class TestValidarPorta(unittest.TestCase):
    """Validação de --porta (usada pelo argparse)."""

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
    enviar() não deve nunca deixar uma exceção de rede escapar para quem
    chama — sempre retorna True/False. Usa um socket mockado
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
    conectar() encerra o processo (sys.exit) com mensagem amigável para
    cada tipo de falha, em vez de deixar a exceção subir crua. Testamos
    isso patchando socket.socket para devolver um mock cujo connect()
    levanta a exceção desejada.
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


class TestMensagensDeSistema(unittest.TestCase):
    """
    _ok/_erro/_aviso usam símbolo (✓ ✗ ⚠); _info especificamente foi
    revertido de volta pro texto "[info]" a pedido — sem símbolo. Este
    teste trava essa escolha, pra não voltar a mudar sem querer numa
    edição futura.
    """

    def test_info_usa_texto_sem_simbolo(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente_app._info("mensagem de teste")
        self.assertIn("[info]", saida.getvalue())
        self.assertNotIn("ℹ", saida.getvalue())


if __name__ == "__main__":
    unittest.main()