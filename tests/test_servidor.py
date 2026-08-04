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
import sys
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
    assert c.receber()["tipo"] == "login_erro"

    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


def test_login_com_nome_contendo_espaco_e_rejeitado(servidor_rodando):
    """
    Bug real encontrado em teste manual: um apelido com espaço (ex:
    "Joao Pedro") é aceito no login, mas quebra /priv — que espera
    exatamente dois argumentos separados por espaço (destinatário e
    texto), então só "Joao" (a primeira palavra) era usado como
    destinatário, e a mensagem privada sempre falhava com "destinatario
    'Joao' nao encontrado". Em vez de complicar o parsing do cliente com
    aspas/escape, a correção é simples e direta: apelido não pode conter
    espaço, com erro claro na hora do login (não só descoberto depois,
    ao tentar usar /priv).
    """
    porta, registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar(protocolo.msg_login("Joao Pedro"))
    resposta = c.receber()
    assert resposta["tipo"] == "login_erro"
    assert "espaco" in resposta["motivo"]
    assert registro.buscar("Joao Pedro") is None

    # conexão continua viva — pode tentar de novo com um nome válido
    c.enviar(protocolo.msg_login("joao_pedro"))
    assert c.receber() == {"tipo": "login_ok", "nome": "joao_pedro"}

    c.fechar()


def test_login_com_nome_contendo_tab_ou_quebra_de_linha_e_rejeitado(servidor_rodando):
    """
    Mesma validação, mas com outros caracteres de espaço em branco além
    do espaço comum — usa any(c.isspace() ...) no servidor, não só a
    checagem de " " literal, então tab e \\n também devem ser pegos.
    """
    porta, _registro = servidor_rodando
    c = ClienteDeTeste(porta)

    c.enviar(protocolo.msg_login("joao\tpedro"))
    assert c.receber()["tipo"] == "login_erro"

    c.fechar()


def test_login_com_nome_muito_longo_e_rejeitado(servidor_rodando):
    porta, registro = servidor_rodando
    c = ClienteDeTeste(porta)

    nome_longo = "x" * 150
    c.enviar(protocolo.msg_login(nome_longo))
    resposta = c.receber()
    assert resposta["tipo"] == "login_erro"
    assert "longo" in resposta["motivo"]
    assert registro.buscar(nome_longo) is None

    c.enviar(protocolo.msg_login("alice"))
    assert c.receber() == {"tipo": "login_ok", "nome": "alice"}

    c.fechar()


def test_login_duplicado_sem_distincao_de_maiusculas_minusculas(servidor_rodando):
    """
    Bug real encontrado: 'Alice' e 'alice' conseguiam logar ao mesmo
    tempo como usuários diferentes — e /priv alice só encontrava o que
    tivesse EXATAMENTE esse case. Complementa o teste equivalente em
    tests/test_modelos.py, mas aqui de ponta a ponta com sockets reais.
    """
    porta, _registro = servidor_rodando
    c1 = ClienteDeTeste(porta)
    c1.enviar(protocolo.msg_login("Alice"))
    assert c1.receber()["tipo"] == "login_ok"

    c2 = ClienteDeTeste(porta)
    c2.enviar(protocolo.msg_login("alice"))  # mesmo nome, case diferente
    resposta = c2.receber()
    assert resposta["tipo"] == "login_erro"

    c2.enviar(protocolo.msg_login("alice2"))
    assert c2.receber()["tipo"] == "login_ok"

    c1.fechar()
    c2.fechar()


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


def test_desconexao_e_registrada_no_console_do_servidor(servidor_rodando, capsys):
    """
    Regressão de UX: o servidor já logava 'Nova conexao de (...)' mas não
    dizia nada quando um cliente desconectava — dificultando acompanhar
    quem está conectado só olhando o console do servidor.
    """
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.fechar()

    # dá um instante para a thread do servidor processar a desconexão
    # e imprimir, antes de checar a saída
    import time
    time.sleep(0.2)

    saida = capsys.readouterr().out
    assert "Conexao encerrada: alice" in saida


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
    assert resposta_seguinte == {"tipo": "lista_usuarios", "usuarios": [{"nome": "alice", "sala": "geral"}]}

    alice.fechar()


# --------------------------------------------------------------------------
# Mensagem privada (etapa 6)
# --------------------------------------------------------------------------

def test_mensagem_privada_chega_ao_destinatario_correto(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"  # bob entrou

    alice.enviar(protocolo.msg_mensagem_privada_enviar("bob", "oi em particular"))
    recebido = bob.receber()
    assert recebido == {"tipo": "mensagem_privada", "remetente": "alice", "texto": "oi em particular"}

    alice.fechar()
    bob.fechar()


def test_mensagem_privada_nao_vaza_para_terceiros(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"

    carol = ClienteDeTeste(porta)
    carol.enviar(protocolo.msg_login("carol"))
    assert carol.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"  # carol entrou
    assert bob.receber()["tipo"] == "notificacao"

    alice.enviar(protocolo.msg_mensagem_privada_enviar("bob", "so pra voce, bob"))
    assert bob.receber() == {"tipo": "mensagem_privada", "remetente": "alice", "texto": "so pra voce, bob"}

    # carol NAO deve receber nada
    carol.sock.settimeout(0.3)
    with pytest.raises(socket.timeout):
        carol.receber()

    alice.fechar()
    bob.fechar()
    carol.fechar()


def test_mensagem_privada_independe_de_sala(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"

    bob.enviar(protocolo.msg_entrar_sala("jogos"))
    bob.receber()  # confirmação "voce entrou na sala 'jogos'"
    assert alice.receber()["tipo"] == "notificacao"  # "bob saiu da sala" (geral)

    # mesmo em salas diferentes, a privada deve chegar normalmente
    alice.enviar(protocolo.msg_mensagem_privada_enviar("bob", "mensagem entre salas"))
    assert bob.receber() == {"tipo": "mensagem_privada", "remetente": "alice", "texto": "mensagem entre salas"}

    alice.fechar()
    bob.fechar()


def test_mensagem_privada_destinatario_inexistente_recebe_erro(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_mensagem_privada_enviar("fantasma", "oi"))
    resposta = alice.receber()
    assert resposta["tipo"] == "erro"
    assert "fantasma" in resposta["motivo"]

    alice.fechar()


def test_mensagem_privada_campo_destinatario_invalido(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar({"tipo": "mensagem_privada", "destinatario": "", "texto": "oi"})
    resposta = alice.receber()
    assert resposta["tipo"] == "erro"
    assert "destinatario" in resposta["motivo"]

    alice.fechar()


def test_mensagem_privada_com_destinatario_quebrado_nao_propaga_erro():
    """
    Cobre _enviar_seguro_para_cliente isolando uma falha de envio — se o
    destinatário de uma mensagem privada tiver o socket quebrado no
    exato momento do envio (ex: caiu um instante atrás), a falha deve
    ser isolada e não deve propagar para quem está enviando (o remetente
    nem fica sabendo; a remoção do destinatário quebrado é feita pela
    própria thread dele, seção 9 da Especificação).
    """
    registro = RegistroClientes()

    sock_quebrado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_quebrado.close()
    destinatario_quebrado = Cliente(nome="quebrado", sock=sock_quebrado, endereco=("127.0.0.1", 0))
    registro.adicionar(destinatario_quebrado)

    sock_remetente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    remetente = Cliente(nome="remetente", sock=sock_remetente, endereco=("127.0.0.1", 0))
    registro.adicionar(remetente)

    msg = protocolo.msg_mensagem_privada_enviar("quebrado", "oi")
    continuar = servidor._rotear_mensagem(registro, remetente, msg)  # não deve lançar

    assert continuar is True


# --------------------------------------------------------------------------
# Salas (etapa 7)
# --------------------------------------------------------------------------

def test_entrar_sala_move_o_cliente_e_notifica_as_duas_salas(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"  # bob entrou no chat (sala geral)

    bob.enviar(protocolo.msg_entrar_sala("jogos"))

    # bob recebe a confirmação direta
    confirmacao = bob.receber()
    assert confirmacao == {"tipo": "notificacao", "texto": "voce entrou na sala 'jogos'"}

    # alice (que ficou em "geral") recebe a notificação de saída de bob
    assert alice.receber() == {"tipo": "notificacao", "texto": "bob saiu da sala"}

    assert registro.buscar("bob").sala_atual == "jogos"

    alice.fechar()
    bob.fechar()


def test_entrar_sala_notifica_quem_ja_estava_na_sala_nova_mas_nao_o_proprio():
    """
    Precisa de 3 clientes: um já na sala "jogos" antes de bob entrar, para
    confirmar que ele recebe a notificação de entrada de bob — e que o
    próprio bob NÃO recebe a própria notificação de volta (só a
    confirmação direta, testada no caso acima).
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock_servidor.getsockname()[1]
    thread = threading.Thread(target=servidor.loop_accept, args=(sock_servidor, registro), daemon=True)
    thread.start()

    try:
        carol = ClienteDeTeste(porta)
        carol.enviar(protocolo.msg_login("carol"))
        assert carol.receber()["tipo"] == "login_ok"
        carol.enviar(protocolo.msg_entrar_sala("jogos"))
        assert carol.receber()["tipo"] == "notificacao"  # confirmação própria

        bob = ClienteDeTeste(porta)
        bob.enviar(protocolo.msg_login("bob"))
        assert bob.receber()["tipo"] == "login_ok"

        bob.enviar(protocolo.msg_entrar_sala("jogos"))
        assert bob.receber() == {"tipo": "notificacao", "texto": "voce entrou na sala 'jogos'"}

        # carol (ja estava em "jogos") ve bob entrando
        assert carol.receber() == {"tipo": "notificacao", "texto": "bob entrou na sala"}

        # bob NAO deve ver a propria notificacao de entrada de novo
        bob.sock.settimeout(0.3)
        with pytest.raises(socket.timeout):
            bob.receber()

        carol.fechar()
        bob.fechar()
    finally:
        sock_servidor.close()


def test_sair_sala_volta_para_geral_reaproveitando_o_mesmo_mecanismo(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_entrar_sala("jogos"))
    assert alice.receber()["tipo"] == "notificacao"
    assert registro.buscar("alice").sala_atual == "jogos"

    alice.enviar(protocolo.msg_sair_sala())
    confirmacao = alice.receber()
    assert confirmacao == {"tipo": "notificacao", "texto": "voce entrou na sala 'geral'"}
    assert registro.buscar("alice").sala_atual == "geral"

    alice.fechar()


def test_entrar_na_mesma_sala_que_ja_esta_e_no_op_com_aviso():
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock_servidor.getsockname()[1]
    thread = threading.Thread(target=servidor.loop_accept, args=(sock_servidor, registro), daemon=True)
    thread.start()

    try:
        alice = ClienteDeTeste(porta)
        alice.enviar(protocolo.msg_login("alice"))
        assert alice.receber()["tipo"] == "login_ok"

        # alice ja esta em "geral" por padrao
        alice.enviar(protocolo.msg_entrar_sala("geral"))
        resposta = alice.receber()
        assert resposta == {"tipo": "notificacao", "texto": "voce ja esta na sala 'geral'"}

        # nao deve ter disparado nenhum broadcast de saida/entrada (nao ha
        # mais nada na fila alem do aviso acima)
        alice.sock.settimeout(0.3)
        with pytest.raises(socket.timeout):
            alice.receber()

        alice.fechar()
    finally:
        sock_servidor.close()


def test_entrar_sala_sem_distincao_de_maiusculas_minusculas(servidor_rodando):
    """
    Bug real encontrado (parecido com o do nome de usuário): 'Jogos' e
    'jogos' eram tratadas como salas DIFERENTES — dois clientes que
    combinam de se encontrar na "mesma" sala, mas digitam o nome com
    capitalização diferente, ficavam cada um sozinho, sem ver o outro,
    sem nenhum erro ou aviso.
    """
    porta, registro = servidor_rodando
    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"

    bob.enviar(protocolo.msg_entrar_sala("Jogos"))  # com maiuscula
    bob.receber()  # confirmação

    carol = ClienteDeTeste(porta)
    carol.enviar(protocolo.msg_login("carol"))
    assert carol.receber()["tipo"] == "login_ok"

    carol.enviar(protocolo.msg_entrar_sala("jogos"))  # minusculo -- mesma sala?
    carol.receber()  # confirmação
    # se caiu na mesma sala, bob deve ver a notificação de entrada da carol
    assert bob.receber() == {"tipo": "notificacao", "texto": "carol entrou na sala"}

    # e uma mensagem geral de bob deve chegar até a carol
    bob.enviar(protocolo.msg_mensagem_geral_enviar("oi da sala jogos"))
    assert carol.receber() == {
        "tipo": "mensagem_geral", "remetente": "bob", "texto": "oi da sala jogos"
    }

    assert registro.buscar("bob").sala_atual == registro.buscar("carol").sala_atual == "jogos"

    bob.fechar()
    carol.fechar()


def test_entrar_sala_muito_longa_e_rejeitada(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_entrar_sala("x" * 100))
    resposta = alice.receber()
    assert resposta["tipo"] == "erro"
    assert "longo" in resposta["motivo"]

    alice.fechar()


def test_entrar_sala_campo_invalido(servidor_rodando):
    porta, _registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar({"tipo": "entrar_sala", "sala": ""})
    resposta = alice.receber()
    assert resposta["tipo"] == "erro"
    assert "sala" in resposta["motivo"]

    alice.fechar()


def test_mensagem_geral_apos_trocar_de_sala_vai_para_a_sala_nova(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"

    alice.enviar(protocolo.msg_entrar_sala("jogos"))
    assert alice.receber()["tipo"] == "notificacao"  # confirmação
    assert bob.receber() == {"tipo": "notificacao", "texto": "alice saiu da sala"}

    # bob (ainda em "geral") manda mensagem geral -- alice (agora em
    # "jogos") NAO deve receber
    bob.enviar(protocolo.msg_mensagem_geral_enviar("oi geral"))
    alice.sock.settimeout(0.3)
    with pytest.raises(socket.timeout):
        alice.receber()

    alice.fechar()
    bob.fechar()


# --------------------------------------------------------------------------
# Listagem de usuários (etapa 8)
# --------------------------------------------------------------------------

def test_listar_usuarios_mostra_todos_com_sala_correta(servidor_rodando):
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"
    assert alice.receber()["tipo"] == "notificacao"

    bob.enviar(protocolo.msg_entrar_sala("jogos"))
    bob.receber()
    assert alice.receber()["tipo"] == "notificacao"

    alice.enviar(protocolo.msg_listar_usuarios())
    resposta = alice.receber()
    assert resposta["tipo"] == "lista_usuarios"
    usuarios_ordenados = sorted(resposta["usuarios"], key=lambda u: u["nome"])
    assert usuarios_ordenados == [
        {"nome": "alice", "sala": "geral"},
        {"nome": "bob", "sala": "jogos"},
    ]

    alice.fechar()
    bob.fechar()


def test_listar_usuarios_funciona_de_qualquer_sala(servidor_rodando):
    """
    Requisito da Especificação (seção 7): listagem mostra TODOS os
    conectados, não só os da sala de quem perguntou.
    """
    porta, registro = servidor_rodando
    alice = ClienteDeTeste(porta)
    alice.enviar(protocolo.msg_login("alice"))
    assert alice.receber()["tipo"] == "login_ok"

    alice.enviar(protocolo.msg_entrar_sala("jogos"))
    alice.receber()  # confirmação

    bob = ClienteDeTeste(porta)
    bob.enviar(protocolo.msg_login("bob"))
    assert bob.receber()["tipo"] == "login_ok"

    # alice (em "jogos") pede a lista -- deve incluir bob (em "geral") também
    alice.enviar(protocolo.msg_listar_usuarios())
    resposta = alice.receber()
    nomes = {u["nome"] for u in resposta["usuarios"]}
    assert nomes == {"alice", "bob"}

    alice.fechar()
    bob.fechar()


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


class _SocketFalsoQueQuebraNoRecv:
    """
    Fake de socket cujo recv() levanta OSError depois do login (em vez de
    retornar b""), simulando uma queda abrupta de conexão (ex:
    ConnectionResetError) durante o loop de roteamento de mensagens —
    cobre o `except OSError` externo de tratar_cliente, distinto do caso
    "recv retorna vazio" (fechamento gracioso, já coberto por outro
    teste).
    """

    def __init__(self, mensagens_para_receber):
        self._fila_recv = list(mensagens_para_receber)

    def recv(self, _tamanho):
        if self._fila_recv:
            return self._fila_recv.pop(0)
        raise OSError("conexao resetada abruptamente (simulado)")

    def sendall(self, _dados):
        pass

    def close(self):
        pass


def test_tratar_cliente_trata_oserror_abrupto_durante_roteamento_sem_propagar():
    registro = RegistroClientes()
    login_bytes = protocolo.serializar(protocolo.msg_login("alice"))
    sock_falso = _SocketFalsoQueQuebraNoRecv([login_bytes])

    # apos o login, o proximo recv() levanta OSError (simulando reset) em
    # vez de retornar b"" -- deve ser tratado pelo except OSError externo,
    # sem propagar, e o cliente deve ser removido do registro do mesmo jeito
    servidor.tratar_cliente(sock_falso, ("127.0.0.1", 0), registro)

    assert registro.buscar("alice") is None


# --------------------------------------------------------------------------
# Bootstrap: criar_socket_servidor, loop_accept, main
# --------------------------------------------------------------------------

def test_loop_accept_retorna_se_socket_ja_estiver_fechado():
    """
    Fechar o socket ANTES de chamar loop_accept: accept() deve falhar
    imediatamente com OSError, e loop_accept deve simplesmente retornar,
    sem lançar exceção nem travar.
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)
    sock_servidor.close()

    servidor.loop_accept(sock_servidor, registro)  # não deve lançar, nem travar


def test_loop_accept_encerra_rapido_mesmo_sem_conexoes_pendentes():
    """
    Regressão do bug de Ctrl+C demorado: mesmo com a thread já dentro do
    loop de accept() e nenhuma conexão pendente, fechar o socket de fora
    deve fazer a thread terminar rapidamente — graças ao timeout de
    accept() (TIMEOUT_ACCEPT_SEGUNDOS), não à sorte de o SO interromper
    uma chamada bloqueante a partir de outra thread (que não tem garantia
    em todas as plataformas — no Windows, em especial, isso podia demorar
    muito ou nunca acontecer sem uma conexão nova chegar).
    """
    registro = RegistroClientes()
    sock_servidor = servidor.criar_socket_servidor("127.0.0.1", 0)

    thread = threading.Thread(target=servidor.loop_accept, args=(sock_servidor, registro), daemon=True)
    thread.start()
    time.sleep(0.05)  # garante que a thread já entrou no loop de accept()

    sock_servidor.close()
    thread.join(timeout=servidor.TIMEOUT_ACCEPT_SEGUNDOS + 1.5)

    assert not thread.is_alive()


@pytest.mark.skipif(sys.platform == "win32", reason="comportamento de SO_REUSEADDR e diferente no Windows")
def test_criar_socket_servidor_impede_dois_binds_na_mesma_porta_linux_mac():
    """
    No Linux/Mac, dois sockets não podem escutar a mesma porta ao mesmo
    tempo, mesmo com SO_REUSEADDR — o segundo bind deve falhar. Já
    confirmado manualmente (dois processos reais na mesma porta), este
    teste automatiza essa mesma garantia.
    """
    sock1 = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock1.getsockname()[1]

    with pytest.raises(OSError):
        servidor.criar_socket_servidor("127.0.0.1", porta)

    sock1.close()


@pytest.mark.skipif(sys.platform != "win32", reason="SO_EXCLUSIVEADDRUSE so existe no Windows")
def test_criar_socket_servidor_impede_dois_binds_na_mesma_porta_windows():
    """
    Regressão do bug real encontrado em teste manual no Windows: com
    SO_REUSEADDR puro, o Windows permitia dois processos escutando a
    MESMA porta ao mesmo tempo, silenciosamente (sem erro nenhum) — bem
    diferente do Linux/Mac. SO_EXCLUSIVEADDRUSE corrige isso: o segundo
    bind deve falhar, exatamente como no Linux/Mac.

    Só roda no Windows (skipped em outras plataformas) porque
    SO_EXCLUSIVEADDRUSE nem existe no módulo socket fora do Windows —
    não há como simular esse comportamento de outra forma.
    """
    sock1 = servidor.criar_socket_servidor("127.0.0.1", 0)
    porta = sock1.getsockname()[1]

    with pytest.raises(OSError):
        servidor.criar_socket_servidor("127.0.0.1", porta)

    sock1.close()


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


def test_main_porta_ja_em_uso_mostra_erro_amigavel_sem_traceback(monkeypatch, capsys):
    """
    Regressão de UX: antes deste teste, uma porta já em uso (ex: dois
    servidor.py na mesma porta) derrubava main() com um traceback cru —
    inconsistente com o padrão de mensagens amigáveis já usado em
    cliente_app.py. Agora deve sair com código 1 e mensagem clara,
    sem propagar a exceção original.
    """
    def criar_socket_servidor_fake(_host, _porta):
        raise OSError("[Errno 98] Address already in use")

    monkeypatch.setattr(servidor, "criar_socket_servidor", criar_socket_servidor_fake)
    monkeypatch.setattr("sys.argv", ["servidor.py", "--porta", "5000"])

    with pytest.raises(SystemExit) as exc_info:
        servidor.main()

    assert exc_info.value.code == 1
    saida = capsys.readouterr().out
    assert "não foi possível iniciar o servidor" in saida
    assert "já está em uso" in saida


def test_main_trata_keyboardinterrupt_sem_propagar(monkeypatch, capsys):
    def loop_accept_fake(_sock_servidor, _registro):
        raise KeyboardInterrupt

    monkeypatch.setattr(servidor, "loop_accept", loop_accept_fake)
    monkeypatch.setattr("sys.argv", ["servidor.py"])

    servidor.main()  # não deve propagar a exceção

    saida = capsys.readouterr().out
    assert "Encerrando servidor" in saida


# --------------------------------------------------------------------------
# Utilitários de saída no terminal (cores, horário, formatação de endereço)
# --------------------------------------------------------------------------

def test_c_aplica_cor_quando_habilitada(monkeypatch):
    """
    _USAR_COR é sempre False sob pytest (stdout não é um tty de verdade),
    então o ramo "aplica cor" de _c() nunca é exercitado nos outros
    testes — força aqui para confirmar que, quando habilitado, o texto
    vem envolvido pelo código ANSI certo.
    """
    monkeypatch.setattr(servidor, "_USAR_COR", True)
    resultado = servidor._c("oi", servidor._Cor.VERDE)
    assert resultado == f"{servidor._Cor.VERDE}oi{servidor._Cor.RESET}"


def test_c_nao_aplica_cor_quando_desabilitada():
    resultado = servidor._c("oi", servidor._Cor.VERDE)
    assert resultado == "oi"  # sem nenhum código ANSI


def test_formatar_endereco_tupla_normal():
    assert servidor._formatar_endereco(("127.0.0.1", 5000)) == "127.0.0.1:5000"


def test_formatar_endereco_valor_inesperado_nao_quebra():
    """
    Se por algum motivo `endereco` não for a tupla (ip, porta) esperada
    (ex: None, ou um tipo sem indexação), _formatar_endereco não deve
    lançar exceção — só cair no repr bruto como último recurso.
    """
    assert servidor._formatar_endereco(None) == "None"
    assert servidor._formatar_endereco(42) == "42"