"""
tests/test_servidor.py — Testes de integração do servidor (servidor.py)

Dono: DEV A.

Diferente de tests/test_modelos.py (que testa RegistroClientes chamando os
métodos diretamente), estes testes sobem um servidor de verdade num socket
TCP real (porta 0 = o SO escolhe uma porta livre) e conectam clientes de
teste reais nele — exercitando o loop de accept, o loop de leitura, o
framing via protocolo.py, e o roteamento, exatamente como vai acontecer na
demonstração ao vivo com cliente_app.py real.

O teste de concorrência de login duplicado (duas threads tentando registrar
o mesmo nome ao mesmo tempo) já está coberto rigorosamente em
tests/test_modelos.py::test_adicionar_mesmo_nome_sob_concorrencia — não
repetido aqui em nível de socket para evitar um teste de integração instável
por causa de timing de rede, quando a garantia real já vem do Lock em
RegistroClientes.
"""

import socket
import threading
import time

import pytest

import protocolo
import servidor
from modelos import Cliente, RegistroClientes


# --------------------------------------------------------------------------
# Infraestrutura de teste
# --------------------------------------------------------------------------

@pytest.fixture
def servidor_rodando():
    """
    Sobe um servidor real numa porta livre escolhida pelo SO, num thread
    de accept em background. Devolve (porta, registro) para os testes.
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock_servidor.getsockname()[1]

    thread_accept = threading.Thread(
        target=servidor.loop_accept,
        args=(sock_servidor, registro),
        daemon=True,
    )
    thread_accept.start()

    yield porta, registro

    sock_servidor.close()


class ClienteDeTeste:
    """
    Wrapper fino sobre um socket real, usado só pelos testes deste
    arquivo — mantém sua própria fila de mensagens decodificadas,
    exatamente como um cliente de verdade precisaria fazer (usando
    protocolo.py para servir de "fake client").
    """

    def __init__(self, porta: int, timeout: float = 2.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(("127.0.0.1", porta))
        self._buffer = b""
        self._fila = []

    def enviar(self, mensagem: dict) -> None:
        self.sock.sendall(protocolo.serializar(mensagem))

    def enviar_bruto(self, dados: bytes) -> None:
        """Para testar mensagens malformadas — bypassa protocolo.serializar."""
        self.sock.sendall(dados)

    def receber(self) -> dict:
        """Bloqueia (até o timeout) até ter uma mensagem completa e a devolve."""
        while not self._fila:
            dados = self.sock.recv(4096)
            if not dados:
                raise ConnectionError("conexao fechada inesperadamente pelo servidor")
            self._buffer += dados
            mensagens, self._buffer = protocolo.extrair_mensagens(self._buffer)
            self._fila.extend(mensagens)
        return self._fila.pop(0)

    def fechar(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def test_login_com_sucesso(servidor_rodando):
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


def test_login_nome_duplicado_permite_nova_tentativa(servidor_rodando):
    porta, _registro = servidor_rodando
    c1 = ClienteDeTeste(porta)
    c1.enviar(protocolo.msg_login("alice"))
    assert c1.receber()["tipo"] == "login_ok"

    c2 = ClienteDeTeste(porta)
    c2.enviar(protocolo.msg_login("alice"))
    assert c2.receber()["tipo"] == "login_erro"

    # decisão da Especificação (seção 5): a conexão continua aberta,
    # permitindo nova tentativa com outro nome, sem reconectar
    c2.enviar(protocolo.msg_login("alice2"))
    assert c2.receber() == {"tipo": "login_ok", "nome": "alice2"}

    c1.fechar()
    c2.fechar()


def test_primeira_mensagem_deve_ser_login(servidor_rodando):
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar(protocolo.msg_sair())  # tenta antes de logar
    assert c.receber()["tipo"] == "erro"

    # conexão continua viva — pode logar em seguida
    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


def test_login_com_nome_vazio_e_rejeitado(servidor_rodando):
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar({"tipo": "login", "nome": "   "})
    assert c.receber()["tipo"] == "erro"

    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


# --------------------------------------------------------------------------
# Mensagem geral / broadcast
# --------------------------------------------------------------------------

def test_broadcast_chega_aos_outros_da_sala(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"

    # alice recebe a notificação de entrada do bob (ela já estava na sala)
    assert alice.receber() == {"tipo": "notificacao", "texto": "bob entrou no chat"}

    alice.enviar(protocolo.msg_mensagem_geral_enviar("oi pessoal"))
    recebido_por_bob = bob.receber()
    assert recebido_por_bob == {"tipo": "mensagem_geral", "remetente": "alice", "texto": "oi pessoal"}

    alice.fechar()
    bob.fechar()


def test_remetente_nao_recebe_a_propria_mensagem_geral(servidor_rodando):
    """
    Decisão de design registrada em servidor.py: o remetente não recebe
    de volta a própria mensagem_geral no broadcast, porque já vê o que
    digitou no próprio terminal.
    """
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_mensagem_geral_enviar("oi"))

    alice.sock.settimeout(0.3)
    with pytest.raises(socket.timeout):
        alice.receber()

    alice.fechar()


def test_broadcast_nao_vaza_para_fora_da_sala_do_remetente():
    """
    Ainda que salas (entrar_sala) não estejam implementadas nesta etapa,
    o filtro por sala em _broadcast_sala já está ativo e é testável
    manipulando sala_atual diretamente via RegistroClientes.mudar_sala —
    útil como teste de regressão para quando a etapa de salas for
    implementada de verdade.
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock_servidor.getsockname()[1]
    thread_accept = threading.Thread(target=servidor.loop_accept, args=(sock_servidor, registro), daemon=True)
    thread_accept.start()

    try:
        alice = ClienteDeTeste(porta)
        alice.enviar(protocolo.msg_login("alice"))
        assert alice.receber()["tipo"] == "login_ok"

        bob = ClienteDeTeste(porta)
        bob.enviar(protocolo.msg_login("bob"))
        assert bob.receber()["tipo"] == "login_ok"
        assert alice.receber()["tipo"] == "notificacao"  # bob entrou

        # move bob para outra sala diretamente no registro (sem depender
        # do comando entrar_sala, que ainda não existe nesta etapa)
        assert registro.mudar_sala("bob", "jogos") is True

        alice.enviar(protocolo.msg_mensagem_geral_enviar("oi"))

        bob.sock.settimeout(0.3)
        with pytest.raises(socket.timeout):
            bob.receber()  # bob não deve receber, está em outra sala

        alice.fechar()
        bob.fechar()
    finally:
        sock_servidor.close()


# --------------------------------------------------------------------------
# Desconexão (limpa e abrupta) e remoção do registro
# --------------------------------------------------------------------------

def test_comando_sair_encerra_conexao_de_forma_limpa(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_sair())

    # o servidor deve fechar do lado dele; o próximo recv() deve retornar
    # vazio (conexão encerrada), não travar nem lançar exceção
    dados = alice.sock.recv(4096)
    assert dados == b""

    assert registro.buscar("alice") is None

    alice.fechar()


def test_desconexao_abrupta_remove_do_registro_e_notifica(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber() == {"tipo": "notificacao", "texto": "bob entrou no chat"}

    bob.fechar()  # desconexão abrupta — sem enviar 'sair'

    assert alice.receber() == {"tipo": "notificacao", "texto": "bob saiu do chat"}
    assert registro.buscar("bob") is None

    alice.fechar()


# --------------------------------------------------------------------------
# Robustez de protocolo
# --------------------------------------------------------------------------

def test_mensagem_malformada_recebe_erro_mas_conexao_continua(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar_bruto(b'{"sem_tipo_valido": true}\n')
    resposta_erro = alice.receber()
    assert resposta_erro["tipo"] == "erro"
    assert "sem_tipo_valido" in resposta_erro["motivo"]  # é o erro da linha malformada

    # a conexão deve continuar viva — E a mensagem seguinte deve ser
    # processada normalmente, não repetir o mesmo erro de framing (isso é
    # o que garante que a linha ruim foi de fato consumida do buffer, não
    # só que "algum erro" veio de volta)
    alice.enviar(protocolo.msg_listar_usuarios())
    resposta_seguinte = alice.receber()
    assert resposta_seguinte["tipo"] == "erro"
    assert "nao implementado" in resposta_seguinte["motivo"]

    alice.fechar()


def test_recursos_ainda_nao_implementados_respondem_erro_explicito(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    for msg in (
        protocolo.msg_mensagem_privada_enviar("bob", "oi"),
        protocolo.msg_entrar_sala("jogos"),
        protocolo.msg_sair_sala(),
        protocolo.msg_listar_usuarios(),
    ):
        alice.enviar(msg)
        resposta = alice.receber()
        assert resposta["tipo"] == "erro"

    alice.fechar()


def test_mensagem_malformada_durante_a_fase_de_login(servidor_rodando):
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar_bruto(b'{"sem_tipo_valido": true}\n')
    resposta_erro = c.receber()
    assert resposta_erro["tipo"] == "erro"

    # a linha malformada precisa ser CONSUMIDA do buffer — se não for
    # (bug corrigido em protocolo.ErroProtocolo), o login abaixo receberia
    # o mesmo erro de framing de novo, em vez de login_ok
    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


def test_tipo_de_mensagem_totalmente_desconhecido_recebe_erro(servidor_rodando):
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)
    c.enviar(protocolo.msg_login("alice"))
    assert c.receber()["tipo"] == "login_ok"

    c.enviar({"tipo": "tipo_que_nao_existe_no_protocolo"})
    assert c.receber()["tipo"] == "erro"

    c.fechar()


def test_desconexao_antes_de_completar_login_nao_derruba_o_servidor(servidor_rodando):
    porta, registro = servidor_rodando

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", porta))
    sock.close()  # desconecta antes de mandar QUALQUER mensagem

    # o servidor não deve quebrar — um cliente novo consegue logar em seguida
    time.sleep(0.1)
    c = ClienteDeTeste(porta)
    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}
    assert registro.buscar("alice") is not None

    c.fechar()


# --------------------------------------------------------------------------
# Unidade: _enviar_erro_seguro e _broadcast_sala (funções internas)
# --------------------------------------------------------------------------

def test_enviar_erro_seguro_nao_propaga_excecao_com_socket_fechado():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()
    servidor._enviar_erro_seguro(sock, "motivo qualquer")  # não deve lançar


def test_broadcast_sala_isola_falha_de_destinatario_sem_interromper_os_demais():
    """
    Um destinatário com socket já quebrado não deve impedir que os demais
    destinatários da mesma sala recebam a mensagem (etapa 11/12 da
    Especificação, seção 9 — robustez do broadcast).
    """
    registro = RegistroClientes()

    sock_quebrado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_quebrado.close()
    cliente_quebrado = Cliente(nome="quebrado", sock=sock_quebrado, endereco=("127.0.0.1", 0))
    registro.adicionar(cliente_quebrado)

    sock_servidor_local = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_servidor_local.bind(("127.0.0.1", 0))
    sock_servidor_local.listen()
    porta_local = sock_servidor_local.getsockname()[1]

    sock_ouvinte = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_ouvinte.settimeout(2.0)
    sock_ouvinte.connect(("127.0.0.1", porta_local))
    sock_lado_servidor, _ = sock_servidor_local.accept()
    cliente_ouvinte = Cliente(nome="ouvinte", sock=sock_lado_servidor, endereco=("127.0.0.1", 0))
    registro.adicionar(cliente_ouvinte)

    # não deve lançar exceção, apesar do destinatário "quebrado" estar na mesma sala
    servidor._broadcast_sala(registro, "geral", protocolo.msg_notificacao("oi"))

    dados = sock_ouvinte.recv(4096)
    mensagens, _resto = protocolo.extrair_mensagens(dados)
    assert mensagens[0] == {"tipo": "notificacao", "texto": "oi"}

    sock_ouvinte.close()
    sock_servidor_local.close()


# --------------------------------------------------------------------------
# Robustez do encerramento (finally) mesmo se close() falhar
# --------------------------------------------------------------------------

class _SocketFalsoQueBraNoClose:
    """
    Fake mínimo de socket.socket, usado só para testar de forma
    determinística que tratar_cliente() não propaga exceção quando
    sock_cliente.close() falha no bloco finally — cenário defensivo
    (socket já fechado por outro motivo) difícil de forçar de forma
    confiável com um socket real, por depender de timing.
    """

    def __init__(self, mensagens_para_receber):
        self._fila_recv = list(mensagens_para_receber)

    def recv(self, _tamanho):
        if self._fila_recv:
            return self._fila_recv.pop(0)
        return b""

    def sendall(self, _dados):
        pass  # não importa para este teste

    def close(self):
        raise OSError("socket ja estava fechado")


def test_tratar_cliente_nao_propaga_erro_se_close_falhar_apos_login():
    registro = RegistroClientes()
    login_bytes = protocolo.serializar(protocolo.msg_login("alice"))
    sair_bytes = protocolo.serializar(protocolo.msg_sair())
    sock_falso = _SocketFalsoQueBraNoClose([login_bytes, sair_bytes])

    servidor.tratar_cliente(sock_falso, ("127.0.0.1", 0), registro)  # não deve propagar

    assert registro.buscar("alice") is None


def test_tratar_cliente_nao_propaga_erro_se_close_falhar_antes_do_login():
    registro = RegistroClientes()
    sock_falso = _SocketFalsoQueBraNoClose([])  # recv já retorna vazio: cliente nunca loga

    servidor.tratar_cliente(sock_falso, ("127.0.0.1", 0), registro)  # não deve propagar


# --------------------------------------------------------------------------
# Bootstrap: criar_socket_servidor, loop_accept, main
# --------------------------------------------------------------------------

def test_loop_accept_retorna_se_socket_ja_estiver_fechado():
    """
    Fechar o socket ANTES de chamar loop_accept (em vez de tentar
    interromper um accept() já bloqueado a partir de outra thread, que
    não tem garantia de funcionar em todas as plataformas/SOs — é uma
    limitação conhecida de sockets bloqueantes, não um bug do projeto):
    accept() deve falhar imediatamente com OSError, e loop_accept deve
    simplesmente retornar, sem lançar exceção nem travar.
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    sock_servidor.close()

    servidor.loop_accept(sock_servidor, registro)  # não deve lançar, nem travar


def test_main_le_porta_via_argumento_e_nao_bloqueia(monkeypatch, capsys):
    """
    Smoke test de main(): confirma que o parsing de --porta funciona e que
    o servidor sobe/desce sem lançar exceção. loop_accept é substituído por
    um fake que não bloqueia (evita o teste travar esperando conexões).
    """
    recebido = {}

    def loop_accept_fake(sock_servidor, _registro):
        recebido["porta"] = sock_servidor.getsockname()[1]

    monkeypatch.setattr(servidor, "loop_accept", loop_accept_fake)
    monkeypatch.setattr("sys.argv", ["servidor.py", "--porta", "0"])

    servidor.main()

    assert "porta" in recebido
    saida = capsys.readouterr().out
    assert "Servidor escutando em" in saida


def test_main_trata_keyboardinterrupt_sem_propagar(monkeypatch, capsys):
    def loop_accept_fake(_sock_servidor, _registro):
        raise KeyboardInterrupt

    monkeypatch.setattr(servidor, "loop_accept", loop_accept_fake)
    monkeypatch.setattr("sys.argv", ["servidor.py"])

    servidor.main()  # não deve propagar a exceção

    saida = capsys.readouterr().out
    assert "Encerrando servidor" in saida