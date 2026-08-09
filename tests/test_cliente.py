"""
tests/test_cliente.py — Testes automatizados do cliente (cliente_app.py)

Escopo:
    - TestParseComando: cobre integralmente parse_comando(), que é a
      lógica com maior densidade de regras de negócio no cliente e, por
      ser uma função pura (sem socket, sem I/O), não precisa de mocks.
    - TestEnviar: cobre enviar() com um socket mockado (unittest.mock),
      validando que cada tipo de falha de rede é tratado sem levantar
      exceção para quem chama.
    - Reconexão automática (TestTentarConectarUmaVez,
      TestRelogarAutomaticamente, TestDormirInterrompivel,
      TestTentarReconectar, TestReceberAteCair,
      TestSupervisionarConexaoIntegracao): cobrem a lógica de espera
      exponencial, reautenticação automática, restauração de sala e
      pedido de histórico após uma queda inesperada de conexão — tudo
      com sockets mockados, para não depender de um servidor real nem
      de tempos de espera longos.

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
import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch

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


class TestParseComandoAjuda(unittest.TestCase):
    """/ajuda -- reimprime a lista de comandos, 100% local."""

    def test_ajuda_nao_envia_nada_ao_servidor(self):
        acao, msg = cliente_app.parse_comando("/ajuda")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_ajuda_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/ajuda alguma_coisa")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_ajuda_case_insensitive(self):
        acao, msg = cliente_app.parse_comando("/AJUDA")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_ajuda_aparece_na_lista_de_comandos(self):
        """Diferente dos easter eggs, /ajuda é um comando de verdade --
        precisa aparecer na lista mostrada ao usuário."""
        self.assertIn(cliente_app.COMANDO_AJUDA, cliente_app.TEXTO_AJUDA_COMANDOS)

    def test_ajuda_imprime_a_lista_de_comandos(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente_app.parse_comando("/ajuda")
        conteudo = saida.getvalue()
        # confirma que reimprime pelo menos um comando conhecido, não so
        # uma mensagem generica
        self.assertIn(cliente_app.COMANDO_PRIV, conteudo)
        self.assertIn(cliente_app.COMANDO_HISTORICO, conteudo)


class TestParseComandoLimpar(unittest.TestCase):
    """/limpar -- limpa a tela, sem afetar o histórico do servidor."""

    def test_limpar_nao_envia_nada_ao_servidor(self):
        acao, msg = cliente_app.parse_comando("/limpar")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_limpar_com_argumento_e_invalido(self):
        acao, msg = cliente_app.parse_comando("/limpar tudo")
        self.assertEqual(acao, cliente_app.ACAO_INVALIDO)
        self.assertIsNone(msg)

    def test_limpar_case_insensitive(self):
        acao, msg = cliente_app.parse_comando("/LIMPAR")
        self.assertEqual(acao, cliente_app.ACAO_VAZIO)
        self.assertIsNone(msg)

    def test_limpar_aparece_na_lista_de_comandos(self):
        self.assertIn(cliente_app.COMANDO_LIMPAR, cliente_app.TEXTO_AJUDA_COMANDOS)

    def test_limpar_envia_sequencia_ansi_quando_e_terminal(self):
        with patch.object(cliente_app, "_USAR_COR", True):
            with contextlib.redirect_stdout(io.StringIO()) as saida:
                cliente_app.parse_comando("/limpar")
        self.assertIn("\033[2J", saida.getvalue())

    def test_limpar_tambem_limpa_o_buffer_de_rolagem(self):
        """
        \\033[2J sozinho só limpa a área visível -- o conteúdo antigo
        continua existindo no buffer de rolagem (scrollback) e reaparece
        se o usuário rolar a tela pra cima. \\033[3J é a parte que limpa
        o scrollback de verdade; sem ela, /limpar só "empurra" o
        conteúdo antigo para fora da vista, sem apagar nada de fato.
        """
        with patch.object(cliente_app, "_USAR_COR", True):
            with contextlib.redirect_stdout(io.StringIO()) as saida:
                cliente_app.parse_comando("/limpar")
        self.assertIn("\033[3J", saida.getvalue())

    def test_limpar_nao_envia_sequencia_ansi_quando_nao_e_terminal(self):
        """Saída redirecionada/capturada por teste não deve receber bytes
        de controle -- eles não fariam sentido nesse contexto."""
        with patch.object(cliente_app, "_USAR_COR", False):
            with contextlib.redirect_stdout(io.StringIO()) as saida:
                cliente_app.parse_comando("/limpar")
        self.assertNotIn("\033[2J", saida.getvalue())

    def test_limpar_menciona_que_historico_do_servidor_nao_e_afetado(self):
        """Ponto central do comando, a pedido: limpar a tela não apaga o
        histórico persistido no servidor."""
        with patch.object(cliente_app, "_USAR_COR", True):
            with contextlib.redirect_stdout(io.StringIO()) as saida:
                cliente_app.parse_comando("/limpar")
        self.assertIn(cliente_app.COMANDO_HISTORICO, saida.getvalue())


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


# ==============================================================================
# Reconexão automática
# ==============================================================================

class TestEstadoClienteCamposDeConexao(unittest.TestCase):
    """EstadoCliente precisa guardar tudo que a reconexão automática
    precisa para reconectar e reautenticar sozinha, além da sala atual
    já existente antes desta funcionalidade."""

    def test_campos_novos_comecam_none(self):
        estado = cliente_app.EstadoCliente()
        self.assertIsNone(estado.ip)
        self.assertIsNone(estado.porta)
        self.assertIsNone(estado.nome)
        self.assertIsNone(estado.senha)
        self.assertIsNone(estado.sock)

    def test_sala_atual_continua_geral_por_padrao(self):
        """Não pode quebrar o comportamento já existente."""
        estado = cliente_app.EstadoCliente()
        self.assertEqual(estado.sala_atual, "geral")

    def test_tem_lock_para_protecao_concorrente_do_socket(self):
        estado = cliente_app.EstadoCliente()
        self.assertTrue(hasattr(estado, "lock"))
        # precisa se comportar como um lock de verdade (usável em "with")
        with estado.lock:
            pass


class TestTentarConectarUmaVez(unittest.TestCase):
    """
    _tentar_conectar_uma_vez() é a base tanto da primeira conexão
    (conectar()) quanto da reconexão automática -- mas, diferente de
    conectar(), NUNCA encerra o processo: sempre devolve (socket, None)
    ou (None, motivo), não importa o que aconteça.
    """

    def test_sucesso_devolve_socket_e_none(self):
        sock_mock = MagicMock()
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            sock, motivo = cliente_app._tentar_conectar_uma_vez("192.0.2.1", 5000)
        self.assertIs(sock, sock_mock)
        self.assertIsNone(motivo)
        sock_mock.settimeout.assert_any_call(None)  # timeout removido após sucesso

    def test_falha_devolve_none_e_motivo_sem_levantar_excecao(self):
        sock_mock = MagicMock()
        sock_mock.connect.side_effect = ConnectionRefusedError()
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            sock, motivo = cliente_app._tentar_conectar_uma_vez("192.0.2.1", 5000)
        self.assertIsNone(sock)
        self.assertIsNotNone(motivo)
        self.assertIn("recusada", motivo)
        sock_mock.close.assert_called_once()

    def test_timeout_tem_mensagem_especifica(self):
        import socket as socket_module
        sock_mock = MagicMock()
        sock_mock.connect.side_effect = socket_module.timeout()
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            sock, motivo = cliente_app._tentar_conectar_uma_vez("192.0.2.1", 5000)
        self.assertIsNone(sock)
        self.assertIn("esgotado", motivo)

    def test_keyboard_interrupt_fecha_socket_e_repropaga(self):
        """Precisa fechar o socket ANTES de repropagar, senão vaza o
        descritor -- conectar() (chamador) depende disso pra encerrar
        de forma limpa num Ctrl+C durante a conexão."""
        sock_mock = MagicMock()
        sock_mock.connect.side_effect = KeyboardInterrupt()
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            with self.assertRaises(KeyboardInterrupt):
                cliente_app._tentar_conectar_uma_vez("192.0.2.1", 5000)
        sock_mock.close.assert_called_once()

    def test_conectar_continua_encerrando_processo_em_caso_de_falha(self):
        """conectar() (usada na primeira conexão) precisa continuar
        chamando sys.exit -- só a reconexão automática usa a variante
        que não encerra o processo."""
        sock_mock = MagicMock()
        sock_mock.connect.side_effect = ConnectionRefusedError()
        with patch("cliente_app.socket.socket", return_value=sock_mock):
            with self.assertRaises(SystemExit):
                cliente_app.conectar("192.0.2.1", 5000)


class TestRelogarAutomaticamente(unittest.TestCase):
    """
    _relogar_automaticamente() reenvia login+senha sem perguntar nada ao
    usuário -- usada só pela reconexão automática, nunca no primeiro
    login da sessão (que continua sendo realizar_login(), interativo).
    """

    def _resposta_serializada(self, tipo, **campos):
        import protocolo
        msg = {"tipo": tipo, **campos}
        return protocolo.serializar(msg)

    def test_login_ok_retorna_sucesso(self):
        sock_mock = MagicMock()
        sock_mock.recv.return_value = self._resposta_serializada(
            "login_ok", nome="alice"
        )
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha123"
        )
        self.assertTrue(sucesso)
        self.assertIsNone(motivo)
        sock_mock.sendall.assert_called_once()

    def test_login_erro_retorna_falha_com_motivo_do_servidor(self):
        sock_mock = MagicMock()
        sock_mock.recv.return_value = self._resposta_serializada(
            "login_erro", motivo="senha incorreta"
        )
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha_errada"
        )
        self.assertFalse(sucesso)
        self.assertEqual(motivo, "senha incorreta")

    def test_timeout_na_resposta_retorna_falha(self):
        import socket as socket_module
        sock_mock = MagicMock()
        sock_mock.recv.side_effect = socket_module.timeout()
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha123"
        )
        self.assertFalse(sucesso)
        self.assertIn("esgotado", motivo)

    def test_conexao_cai_durante_reautenticacao_retorna_falha(self):
        sock_mock = MagicMock()
        sock_mock.recv.side_effect = ConnectionResetError()
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha123"
        )
        self.assertFalse(sucesso)
        self.assertIsNotNone(motivo)

    def test_servidor_fecha_conexao_retorna_falha(self):
        sock_mock = MagicMock()
        sock_mock.recv.return_value = b""
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha123"
        )
        self.assertFalse(sucesso)

    def test_falha_ao_enviar_login_retorna_falha_sem_lancar(self):
        sock_mock = MagicMock()
        sock_mock.sendall.side_effect = BrokenPipeError()
        sucesso, buffer, motivo = cliente_app._relogar_automaticamente(
            sock_mock, "alice", "senha123"
        )
        self.assertFalse(sucesso)

    def test_timeout_do_socket_e_removido_ao_final_mesmo_com_sucesso(self):
        sock_mock = MagicMock()
        sock_mock.recv.return_value = self._resposta_serializada(
            "login_ok", nome="alice"
        )
        cliente_app._relogar_automaticamente(sock_mock, "alice", "senha123")
        # ultima chamada a settimeout deve ser None (removendo o timeout)
        self.assertEqual(sock_mock.settimeout.call_args_list[-1], call(None))


class TestDormirInterrompivel(unittest.TestCase):
    """_dormir_interrompivel() precisa esperar aproximadamente o tempo
    pedido, mas retornar na hora se evento_encerrando for sinalizado no
    meio -- essencial para /sair e Ctrl+C interromperem uma reconexão em
    andamento sem demora."""

    def test_espera_o_tempo_pedido_quando_nao_interrompido(self):
        evento = threading.Event()
        inicio = time.time()
        cliente_app._dormir_interrompivel(0.3, evento)
        decorrido = time.time() - inicio
        self.assertGreaterEqual(decorrido, 0.28)
        self.assertLess(decorrido, 0.6)

    def test_retorna_cedo_se_evento_e_sinalizado_no_meio(self):
        evento = threading.Event()

        def sinalizar_logo():
            time.sleep(0.1)
            evento.set()

        threading.Thread(target=sinalizar_logo, daemon=True).start()
        inicio = time.time()
        cliente_app._dormir_interrompivel(5.0, evento)
        decorrido = time.time() - inicio
        self.assertLess(decorrido, 1.0)

    def test_retorna_imediatamente_se_evento_ja_setado(self):
        evento = threading.Event()
        evento.set()
        inicio = time.time()
        cliente_app._dormir_interrompivel(5.0, evento)
        self.assertLess(time.time() - inicio, 0.5)


class TestTentarReconectar(unittest.TestCase):
    """
    _tentar_reconectar() é o coração da funcionalidade: espera
    exponencial, reautenticação automática, restauração de sala e
    pedido de histórico. Os testes patcham as constantes de tempo para
    valores minúsculos, para não deixar a suíte lenta.
    """

    def _estado_basico(self):
        estado = cliente_app.EstadoCliente()
        estado.ip = "192.0.2.1"
        estado.porta = 5000
        estado.nome = "alice"
        estado.senha = "senha123"
        estado.sala_atual = "geral"
        return estado

    def test_reconecta_na_primeira_tentativa(self):
        estado = self._estado_basico()
        evento = threading.Event()
        sock_novo = MagicMock()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(sock_novo, None)), \
             patch("cliente_app._relogar_automaticamente", return_value=(True, b"sobra", None)):
            sucesso, buffer = cliente_app._tentar_reconectar(estado, evento)

        self.assertTrue(sucesso)
        self.assertEqual(buffer, b"sobra")
        self.assertIs(estado.sock, sock_novo)

    def test_espera_dobra_a_cada_falha_ate_o_teto(self):
        estado = self._estado_basico()
        evento = threading.Event()
        esperas_usadas = []

        def registrar_espera(segundos, evento_encerrando):
            esperas_usadas.append(segundos)

        respostas = [(None, "falhou")] * 4 + [(MagicMock(), None)]

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 1.0), \
             patch("cliente_app.RECONEXAO_ESPERA_MAXIMA_SEGUNDOS", 4.0), \
             patch("cliente_app.RECONEXAO_DESISTIR_APOS_SEGUNDOS", 1000.0), \
             patch("cliente_app._dormir_interrompivel", side_effect=registrar_espera), \
             patch("cliente_app._tentar_conectar_uma_vez", side_effect=respostas), \
             patch("cliente_app._relogar_automaticamente", return_value=(True, b"", None)):
            sucesso, _ = cliente_app._tentar_reconectar(estado, evento)

        self.assertTrue(sucesso)
        # 1, 2, 4, 4, 4 -- dobra ate o teto de 4.0 e depois estabiliza
        self.assertEqual(esperas_usadas, [1.0, 2.0, 4.0, 4.0, 4.0])

    def test_desiste_apos_esgotar_tempo_total(self):
        estado = self._estado_basico()
        evento = threading.Event()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 10.0), \
             patch("cliente_app.RECONEXAO_ESPERA_MAXIMA_SEGUNDOS", 10.0), \
             patch("cliente_app.RECONEXAO_DESISTIR_APOS_SEGUNDOS", 25.0), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(None, "sempre falha")):
            sucesso, buffer = cliente_app._tentar_reconectar(estado, evento)

        self.assertFalse(sucesso)
        self.assertEqual(buffer, b"")
        self.assertIsNone(estado.sock)  # nunca foi trocado, ja que nunca conectou

    def test_cancela_se_evento_encerrando_e_setado_no_meio(self):
        estado = self._estado_basico()
        evento = threading.Event()

        def dormir_e_cancelar(segundos, evento_encerrando):
            evento_encerrando.set()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel", side_effect=dormir_e_cancelar), \
             patch("cliente_app._tentar_conectar_uma_vez") as mock_conectar:
            sucesso, buffer = cliente_app._tentar_reconectar(estado, evento)

        self.assertFalse(sucesso)
        mock_conectar.assert_not_called()  # nem chegou a tentar -- cancelado antes

    def test_desiste_se_reautenticacao_falhar(self):
        """Nome pode ter sido tomado por outra pessoa enquanto a conexão
        estava caída -- tentar de novo com o mesmo nome não resolveria,
        então desiste em vez de ficar tentando para sempre."""
        estado = self._estado_basico()
        evento = threading.Event()
        sock_novo = MagicMock()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(sock_novo, None)), \
             patch("cliente_app._relogar_automaticamente", return_value=(False, b"", "nome ja em uso")):
            sucesso, buffer = cliente_app._tentar_reconectar(estado, evento)

        self.assertFalse(sucesso)
        sock_novo.close.assert_called_once()  # nao deve vazar o socket que conectou mas nao autenticou

    def test_restaura_sala_diferente_de_geral_apos_reconectar(self):
        estado = self._estado_basico()
        estado.sala_atual = "jogos"
        evento = threading.Event()
        sock_novo = MagicMock()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(sock_novo, None)), \
             patch("cliente_app._relogar_automaticamente", return_value=(True, b"", None)):
            cliente_app._tentar_reconectar(estado, evento)

        import protocolo
        chamadas = [c.args[0] for c in sock_novo.sendall.call_args_list]
        mensagens_enviadas = []
        for dados in chamadas:
            msgs, _ = protocolo.extrair_mensagens(dados)
            mensagens_enviadas.extend(msgs)
        tipos = [m["tipo"] for m in mensagens_enviadas]
        self.assertIn(protocolo.TIPO_ENTRAR_SALA, tipos)
        entrar = next(m for m in mensagens_enviadas if m["tipo"] == protocolo.TIPO_ENTRAR_SALA)
        self.assertEqual(entrar["sala"], "jogos")

    def test_nao_tenta_reentrar_em_sala_se_ja_era_geral(self):
        estado = self._estado_basico()
        estado.sala_atual = "geral"
        evento = threading.Event()
        sock_novo = MagicMock()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(sock_novo, None)), \
             patch("cliente_app._relogar_automaticamente", return_value=(True, b"", None)):
            cliente_app._tentar_reconectar(estado, evento)

        import protocolo
        chamadas = [c.args[0] for c in sock_novo.sendall.call_args_list]
        mensagens_enviadas = []
        for dados in chamadas:
            msgs, _ = protocolo.extrair_mensagens(dados)
            mensagens_enviadas.extend(msgs)
        tipos = [m["tipo"] for m in mensagens_enviadas]
        self.assertNotIn(protocolo.TIPO_ENTRAR_SALA, tipos)

    def test_pede_historico_apos_reconectar(self):
        estado = self._estado_basico()
        evento = threading.Event()
        sock_novo = MagicMock()

        with patch("cliente_app.RECONEXAO_ESPERA_INICIAL_SEGUNDOS", 0.01), \
             patch("cliente_app._dormir_interrompivel"), \
             patch("cliente_app._tentar_conectar_uma_vez", return_value=(sock_novo, None)), \
             patch("cliente_app._relogar_automaticamente", return_value=(True, b"", None)):
            cliente_app._tentar_reconectar(estado, evento)

        import protocolo
        chamadas = [c.args[0] for c in sock_novo.sendall.call_args_list]
        mensagens_enviadas = []
        for dados in chamadas:
            msgs, _ = protocolo.extrair_mensagens(dados)
            mensagens_enviadas.extend(msgs)
        tipos = [m["tipo"] for m in mensagens_enviadas]
        self.assertIn(protocolo.TIPO_HISTORICO, tipos)


class TestReceberAteCair(unittest.TestCase):
    """_receber_ate_cair() nunca deve encerrar o processo -- sempre
    retorna normalmente, deixando a decisão de reconectar (ou não) para
    quem chamou (supervisionar_conexao)."""

    def test_retorna_normalmente_quando_conexao_cai(self):
        sock_mock = MagicMock()
        sock_mock.recv.side_effect = ConnectionResetError()
        evento = threading.Event()
        estado = cliente_app.EstadoCliente()
        # nao deve levantar excecao nem chamar os._exit
        cliente_app._receber_ate_cair(sock_mock, b"", evento, estado)
        self.assertFalse(evento.is_set())  # nao seta o evento sozinho

    def test_retorna_normalmente_quando_servidor_fecha(self):
        sock_mock = MagicMock()
        sock_mock.recv.return_value = b""
        evento = threading.Event()
        estado = cliente_app.EstadoCliente()
        cliente_app._receber_ate_cair(sock_mock, b"", evento, estado)
        self.assertFalse(evento.is_set())

    def test_retorna_quando_evento_encerrando_e_sinalizado_por_fora(self):
        sock_mock = MagicMock()
        evento = threading.Event()
        evento.set()
        estado = cliente_app.EstadoCliente()
        # com o evento ja setado, nem deveria tentar recv()
        cliente_app._receber_ate_cair(sock_mock, b"", evento, estado)
        sock_mock.recv.assert_not_called()


class TestSupervisionarConexaoIntegracao(unittest.TestCase):
    """
    Testa supervisionar_conexao() de ponta a ponta (com mocks),
    confirmando que ela: (a) não tenta reconectar quando a saída foi
    pedida pelo usuário; (b) reconecta e continua operando quando a
    queda foi inesperada; (c) encerra o processo quando a reconexão
    desiste de vez.
    """

    def test_nao_reconecta_se_usuario_pediu_saida(self):
        estado = cliente_app.EstadoCliente()
        estado.sock = MagicMock()
        evento = threading.Event()

        def receber_e_sair(sock, buffer, evento_encerrando, estado_):
            evento_encerrando.set()  # simula /sair fechando a conexao

        with patch("cliente_app._receber_ate_cair", side_effect=receber_e_sair), \
             patch("cliente_app._tentar_reconectar") as mock_reconectar:
            cliente_app.supervisionar_conexao(estado, evento, b"")

        mock_reconectar.assert_not_called()

    def test_reconecta_e_volta_a_receber_apos_queda_inesperada(self):
        estado = cliente_app.EstadoCliente()
        estado.sock = MagicMock()
        evento = threading.Event()
        chamadas_receber = []

        def receber(sock, buffer, evento_encerrando, estado_):
            chamadas_receber.append(sock)
            if len(chamadas_receber) >= 2:
                evento_encerrando.set()  # encerra no segundo ciclo p/ nao rodar pra sempre

        sock_novo = MagicMock()

        with patch("cliente_app._receber_ate_cair", side_effect=receber), \
             patch("cliente_app._tentar_reconectar", return_value=(True, b"")) as mock_reconectar:
            cliente_app.supervisionar_conexao(estado, evento, b"")

        mock_reconectar.assert_called_once()
        self.assertEqual(len(chamadas_receber), 2)

    def test_encerra_processo_quando_reconexao_desiste(self):
        estado = cliente_app.EstadoCliente()
        estado.sock = MagicMock()
        evento = threading.Event()

        with patch("cliente_app._receber_ate_cair"), \
             patch("cliente_app._tentar_reconectar", return_value=(False, b"")), \
             patch("cliente_app._encerrar_processo_final") as mock_exit:
            cliente_app.supervisionar_conexao(estado, evento, b"")

        mock_exit.assert_called_once()
        self.assertTrue(evento.is_set())

    def test_nao_encerra_processo_a_forca_se_usuario_cancelou_durante_reconexao(self):
        """Regressão: se o usuário mandou /sair ou Ctrl+C bem no meio de
        uma tentativa de reconexão, _tentar_reconectar retorna False
        (cancelado) com evento_encerrando JÁ setado -- isso não é uma
        desistência de verdade, e não deve forçar os._exit(); o
        encerramento normal (via main()/encerrar()) já está em curso."""
        estado = cliente_app.EstadoCliente()
        estado.sock = MagicMock()
        evento = threading.Event()

        def reconectar_cancelado(estado_, evento_encerrando):
            evento_encerrando.set()  # simula o /sair setando o evento
            return False, b""

        with patch("cliente_app._receber_ate_cair"), \
             patch("cliente_app._tentar_reconectar", side_effect=reconectar_cancelado), \
             patch("cliente_app._encerrar_processo_final") as mock_exit:
            cliente_app.supervisionar_conexao(estado, evento, b"")

        mock_exit.assert_not_called()


class TestEncerrarUsaSocketAtual(unittest.TestCase):
    """encerrar() precisa fechar o socket ATUAL guardado em estado
    (não um socket antigo capturado no início da sessão), já que uma
    reconexão pode ter trocado o socket no meio da sessão."""

    def test_fecha_o_socket_guardado_em_estado(self):
        estado = cliente_app.EstadoCliente()
        sock_atual = MagicMock()
        estado.sock = sock_atual
        evento = threading.Event()

        cliente_app.encerrar(estado, evento)

        sock_atual.close.assert_called_once()
        self.assertTrue(evento.is_set())

    def test_seta_evento_antes_de_fechar_para_supervisor_nao_tentar_reconectar(self):
        estado = cliente_app.EstadoCliente()
        estado.sock = MagicMock()
        evento = threading.Event()
        ordem = []
        evento_set_original = evento.set
        estado.sock.close.side_effect = lambda: ordem.append("close")
        with patch.object(evento, "set", side_effect=lambda: (ordem.append("set"), evento_set_original())):
            cliente_app.encerrar(estado, evento)
        self.assertEqual(ordem, ["set", "close"])


if __name__ == "__main__":
    unittest.main()