"""
tests/test_persistencia.py — Testes do módulo persistencia.py

Cada teste usa um banco SQLite em memória (":memory:") próprio — isolado,
rápido, e não deixa arquivo nenhum no disco depois do teste.
"""

import threading

import pytest

from persistencia import Historico, LIMITE_MAXIMO, LIMITE_PADRAO


@pytest.fixture
def historico():
    h = Historico(":memory:")
    yield h
    h.fechar()


def test_buscar_recentes_sala_vazia_retorna_lista_vazia(historico):
    assert historico.buscar_recentes("geral") == []


def test_registrar_e_buscar_uma_mensagem(historico):
    historico.registrar("geral", "alice", "oi pessoal")
    resultado = historico.buscar_recentes("geral")

    assert len(resultado) == 1
    assert resultado[0]["remetente"] == "alice"
    assert resultado[0]["texto"] == "oi pessoal"
    assert len(resultado[0]["hora"]) == 8  # formato HH:MM:SS
    assert resultado[0]["hora"].count(":") == 2


def test_ordem_cronologica_mais_antiga_primeiro(historico):
    historico.registrar("geral", "alice", "primeira")
    historico.registrar("geral", "bob", "segunda")
    historico.registrar("geral", "carol", "terceira")

    resultado = historico.buscar_recentes("geral")

    assert [m["texto"] for m in resultado] == ["primeira", "segunda", "terceira"]


def test_respeita_limite(historico):
    for i in range(10):
        historico.registrar("geral", "alice", f"mensagem {i}")

    resultado = historico.buscar_recentes("geral", limite=3)

    assert len(resultado) == 3
    # deve ser as 3 MAIS RECENTES (7, 8, 9), em ordem cronológica
    assert [m["texto"] for m in resultado] == ["mensagem 7", "mensagem 8", "mensagem 9"]


def test_limite_nao_informado_usa_padrao(historico):
    for i in range(LIMITE_PADRAO + 5):
        historico.registrar("geral", "alice", f"mensagem {i}")

    resultado = historico.buscar_recentes("geral", limite=None)

    assert len(resultado) == LIMITE_PADRAO


def test_limite_acima_do_teto_e_reduzido(historico):
    for i in range(LIMITE_MAXIMO + 20):
        historico.registrar("geral", "alice", f"mensagem {i}")

    resultado = historico.buscar_recentes("geral", limite=LIMITE_MAXIMO + 20)

    assert len(resultado) == LIMITE_MAXIMO


def test_limite_invalido_cai_no_padrao(historico):
    """
    Valores que não fazem sentido como "quantas mensagens eu quero"
    (negativo, zero, string, bool, etc. vindos de uma mensagem malformada
    ou de um cliente mal-comportado) não devem quebrar a consulta — só
    caem no padrão, silenciosamente.
    """
    for i in range(30):
        historico.registrar("geral", "alice", f"mensagem {i}")

    for limite_invalido in (-5, 0, "10", 3.5, True, False, None):
        resultado = historico.buscar_recentes("geral", limite=limite_invalido)
        assert len(resultado) == LIMITE_PADRAO, f"falhou para limite={limite_invalido!r}"


def test_salas_diferentes_nao_se_misturam(historico):
    historico.registrar("geral", "alice", "mensagem da geral")
    historico.registrar("jogos", "bob", "mensagem de jogos")

    resultado_geral = historico.buscar_recentes("geral")
    resultado_jogos = historico.buscar_recentes("jogos")

    assert [m["texto"] for m in resultado_geral] == ["mensagem da geral"]
    assert [m["texto"] for m in resultado_jogos] == ["mensagem de jogos"]


def test_registro_concorrente_de_varias_threads_nao_perde_mensagem():
    """
    Várias threads (simulando várias conexões de cliente) registrando ao
    mesmo tempo — nenhuma mensagem pode se perder, e o banco não pode
    corromper (sqlite3 não é thread-safe por padrão sem o Lock que
    Historico usa internamente).
    """
    h = Historico(":memory:")
    n = 50
    barreira = threading.Barrier(n)

    def tarefa(indice):
        barreira.wait()
        h.registrar("geral", f"user{indice}", f"mensagem {indice}")

    threads = [threading.Thread(target=tarefa, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    resultado = h.buscar_recentes("geral", limite=n)
    assert len(resultado) == n
    textos = {m["texto"] for m in resultado}
    assert textos == {f"mensagem {i}" for i in range(n)}

    h.fechar()


def test_criar_tabela_e_idempotente():
    """Instanciar Historico duas vezes sobre o mesmo arquivo não deve
    falhar (CREATE TABLE IF NOT EXISTS)."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "teste.db")
        h1 = Historico(caminho)
        h1.registrar("geral", "alice", "oi")
        h1.fechar()

        h2 = Historico(caminho)  # reabre o MESMO arquivo
        resultado = h2.buscar_recentes("geral")
        h2.fechar()

        assert len(resultado) == 1
        assert resultado[0]["texto"] == "oi"