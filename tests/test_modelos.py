"""
tests/test_modelos.py — Testes de modelos.py (Cliente, RegistroClientes)

Dono: DEV A.

Separado de tests/test_servidor.py (que fica para quando servidor.py tiver
o loop de accept/roteamento de fato implementado) porque estes testes só
dependem de modelos.py — testá-los aqui, isoladamente, não exige nenhum
socket real nem servidor.py existir.

O caso mais importante aqui é test_adicionar_mesmo_nome_sob_concorrencia:
não basta adicionar() ser "logicamente" correto — precisa ser correto
quando várias threads tentam registrar o MESMO nome ao mesmo tempo, que é
exatamente o cenário real de dois clientes tentando logar com o nome
"alice" no mesmo instante.
"""

import socket
import threading

import pytest

from modelos import Cliente, RegistroClientes


def _cliente_fake(nome: str, sala: str = "geral") -> Cliente:
    """
    Cria um Cliente com um socket real (não conectado) só para ter um
    objeto socket.socket válido — os testes aqui não fazem I/O de rede de
    verdade, só exercitam RegistroClientes.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    return Cliente(nome=nome, sock=sock, endereco=("127.0.0.1", 0), sala_atual=sala)


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------

def test_cliente_valores_padrao():
    c = _cliente_fake("alice")
    assert c.nome == "alice"
    assert c.sala_atual == "geral"
    assert c.endereco == ("127.0.0.1", 0)


def test_cliente_repr_nao_quebra():
    c = _cliente_fake("alice")
    texto = repr(c)
    assert "alice" in texto
    assert "geral" in texto


# --------------------------------------------------------------------------
# RegistroClientes — operações básicas
# --------------------------------------------------------------------------

def test_adicionar_novo_cliente_retorna_true():
    registro = RegistroClientes()
    assert registro.adicionar(_cliente_fake("alice")) is True
    assert registro.quantidade() == 1


def test_adicionar_nome_duplicado_retorna_false():
    registro = RegistroClientes()
    assert registro.adicionar(_cliente_fake("alice")) is True
    assert registro.adicionar(_cliente_fake("alice")) is False
    assert registro.quantidade() == 1  # o segundo NÃO deve ter sobrescrito o primeiro


def test_adicionar_nome_duplicado_sem_distincao_de_maiusculas_minusculas():
    """
    Bug real observado: 'Alice' e 'alice' conseguiam se conectar ao
    mesmo tempo como usuários "diferentes" — confuso pra quem tenta
    mandar mensagem privada usando o case que lembra, e potencialmente
    dois clientes pensando que são o único 'Alice' do chat.
    """
    registro = RegistroClientes()
    assert registro.adicionar(_cliente_fake("Alice")) is True
    assert registro.adicionar(_cliente_fake("alice")) is False
    assert registro.adicionar(_cliente_fake("ALICE")) is False
    assert registro.quantidade() == 1


def test_remover_cliente_existente():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice"))
    registro.remover("alice")
    assert registro.buscar("alice") is None
    assert registro.quantidade() == 0


def test_remover_e_idempotente():
    registro = RegistroClientes()
    # remover um nome que nunca existiu não deve levantar erro
    registro.remover("fantasma")
    registro.adicionar(_cliente_fake("alice"))
    registro.remover("alice")
    registro.remover("alice")  # segunda vez, já removido — também não deve quebrar
    assert registro.quantidade() == 0


def test_buscar_cliente_inexistente_retorna_none():
    registro = RegistroClientes()
    assert registro.buscar("ninguem") is None


def test_buscar_retorna_o_mesmo_objeto():
    registro = RegistroClientes()
    cliente = _cliente_fake("alice")
    registro.adicionar(cliente)
    assert registro.buscar("alice") is cliente


def test_buscar_sem_distincao_de_maiusculas_minusculas():
    registro = RegistroClientes()
    cliente = _cliente_fake("Alice")
    registro.adicionar(cliente)

    # busca com qualquer combinação de case deve achar o mesmo objeto
    assert registro.buscar("alice") is cliente
    assert registro.buscar("ALICE") is cliente
    assert registro.buscar("aLiCe") is cliente

    # e o nome de EXIBIÇÃO continua com a capitalização original
    assert registro.buscar("alice").nome == "Alice"


def test_remover_sem_distincao_de_maiusculas_minusculas():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("Alice"))
    registro.remover("alice")  # remove usando case diferente do cadastro
    assert registro.buscar("Alice") is None
    assert registro.quantidade() == 0


# --------------------------------------------------------------------------
# RegistroClientes — mudar_sala
# --------------------------------------------------------------------------

def test_mudar_sala_de_cliente_existente():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice"))
    assert registro.mudar_sala("alice", "jogos") is True
    assert registro.buscar("alice").sala_atual == "jogos"


def test_mudar_sala_sem_distincao_de_maiusculas_minusculas():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("Alice"))
    assert registro.mudar_sala("ALICE", "jogos") is True
    assert registro.buscar("Alice").sala_atual == "jogos"


def test_mudar_sala_de_cliente_inexistente_retorna_false():
    registro = RegistroClientes()
    assert registro.mudar_sala("fantasma", "jogos") is False


# --------------------------------------------------------------------------
# RegistroClientes — listagens
# --------------------------------------------------------------------------

def test_listar_todos_vazio():
    registro = RegistroClientes()
    assert registro.listar_todos() == []


def test_listar_todos_mostra_todos_independente_da_sala():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice", sala="geral"))
    registro.adicionar(_cliente_fake("bob", sala="jogos"))

    resultado = sorted(registro.listar_todos())
    assert resultado == [("alice", "geral"), ("bob", "jogos")]


def test_listar_por_sala_filtra_corretamente():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice", sala="geral"))
    registro.adicionar(_cliente_fake("bob", sala="jogos"))
    registro.adicionar(_cliente_fake("carol", sala="geral"))

    da_sala_geral = registro.listar_por_sala("geral")
    nomes = sorted(c.nome for c in da_sala_geral)
    assert nomes == ["alice", "carol"]

    da_sala_jogos = registro.listar_por_sala("jogos")
    assert [c.nome for c in da_sala_jogos] == ["bob"]


def test_listar_por_sala_sala_inexistente_retorna_lista_vazia():
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice"))
    assert registro.listar_por_sala("sala-que-nao-existe") == []


def test_listar_por_sala_retorna_copia_independente():
    """
    O broadcast em servidor.py vai copiar a lista sob lock, liberar o
    lock, e só então enviar. Isso só é seguro se listar_por_sala()
    devolver de fato uma lista nova — mutar a lista retornada (ex: dar um
    .clear() nela) não pode afetar o estado interno do RegistroClientes.
    """
    registro = RegistroClientes()
    registro.adicionar(_cliente_fake("alice"))

    resultado = registro.listar_por_sala("geral")
    resultado.clear()  # mutação local — não deve vazar para dentro

    assert registro.quantidade() == 1
    assert len(registro.listar_por_sala("geral")) == 1


# --------------------------------------------------------------------------
# Concorrência — o motivo de existir o Lock
# --------------------------------------------------------------------------

def test_registro_sob_concorrencia_varios_nomes_distintos():
    """
    N threads adicionando N clientes com nomes DIFERENTES ao mesmo tempo:
    nenhuma atualização pode se perder, e o resultado final deve ter
    exatamente N clientes, todos presentes em listar_todos().
    """
    registro = RegistroClientes()
    n = 50
    barreira = threading.Barrier(n)

    def tarefa(indice):
        barreira.wait()  # maximiza a chance real de colisão entre threads
        registro.adicionar(_cliente_fake(f"usuario_{indice}"))

    threads = [threading.Thread(target=tarefa, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert registro.quantidade() == n
    nomes = {nome for nome, _sala in registro.listar_todos()}
    assert nomes == {f"usuario_{i}" for i in range(n)}


def test_adicionar_mesmo_nome_sob_concorrencia():
    """
    O teste mais importante deste arquivo: várias threads tentando
    registrar o MESMO nome ao mesmo tempo (simula dois clientes reais
    tentando logar como "alice" no mesmo instante). Exatamente UMA deve
    ter sucesso (True); todas as outras devem receber False — nunca duas
    threads podem "vencer" o adicionar() para o mesmo nome.
    """
    registro = RegistroClientes()
    n = 30
    barreira = threading.Barrier(n)
    resultados = [None] * n

    def tarefa(indice):
        barreira.wait()
        resultados[indice] = registro.adicionar(_cliente_fake("alice"))

    threads = [threading.Thread(target=tarefa, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert resultados.count(True) == 1
    assert resultados.count(False) == n - 1
    assert registro.quantidade() == 1


def test_adicionar_mesmo_nome_com_cases_diferentes_sob_concorrencia():
    """
    Mesmo teste acima, mas cada thread tenta um CASE diferente do mesmo
    nome ("Alice", "alice", "ALICE", "aLiCe", ...) — a normalização por
    casefold() precisa continuar garantindo que só uma vence, mesmo
    quando nenhuma delas literalmente bate com as outras char a char.
    """
    registro = RegistroClientes()
    variantes = ["alice", "Alice", "ALICE", "aLiCe", "AlIcE"] * 6  # 30 tentativas
    barreira = threading.Barrier(len(variantes))
    resultados = [None] * len(variantes)

    def tarefa(indice, nome):
        barreira.wait()
        resultados[indice] = registro.adicionar(_cliente_fake(nome))

    threads = [threading.Thread(target=tarefa, args=(i, nome)) for i, nome in enumerate(variantes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert resultados.count(True) == 1
    assert resultados.count(False) == len(variantes) - 1
    assert registro.quantidade() == 1


def test_adicionar_e_remover_concorrente_nao_quebra():
    """
    Metade das threads adiciona clientes com nomes distintos, a outra
    metade tenta remover nomes que podem ou não já ter sido adicionados
    ainda — não deve haver exceção nem deadlock, independente da ordem
    real de execução.
    """
    registro = RegistroClientes()
    n = 40

    def adicionar_tarefa(indice):
        registro.adicionar(_cliente_fake(f"usuario_{indice}"))

    def remover_tarefa(indice):
        registro.remover(f"usuario_{indice}")  # pode ou não existir ainda

    threads = []
    for i in range(n):
        threads.append(threading.Thread(target=adicionar_tarefa, args=(i,)))
        threads.append(threading.Thread(target=remover_tarefa, args=(i,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # não travou nem lançou exceção — e o estado final é consistente:
    # todo cliente que sobrou realmente está no dict interno
    for nome, _sala in registro.listar_todos():
        assert registro.buscar(nome) is not None