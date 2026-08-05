"""
tests/test_usuarios.py — Testes do módulo usuarios.py (cadastro e
autenticação por senha).

Cada teste usa um banco SQLite em memória (":memory:") próprio — isolado,
rápido, e não deixa arquivo nenhum no disco depois do teste.
"""

import threading

import pytest

from usuarios import TAMANHO_SALT_BYTES, Usuarios


@pytest.fixture
def usuarios():
    u = Usuarios(":memory:")
    yield u
    u.fechar()


def test_primeiro_uso_de_um_nome_cadastra_e_autentica(usuarios):
    autenticado, motivo = usuarios.autenticar("alice", "minhasenha")
    assert autenticado is True
    assert motivo is None


def test_segundo_login_com_senha_correta_autentica(usuarios):
    usuarios.autenticar("alice", "minhasenha")  # cadastra
    autenticado, motivo = usuarios.autenticar("alice", "minhasenha")
    assert autenticado is True
    assert motivo is None


def test_login_com_senha_errada_e_rejeitado_com_motivo_claro(usuarios):
    usuarios.autenticar("alice", "minhasenha")  # cadastra
    autenticado, motivo = usuarios.autenticar("alice", "senha_errada")
    assert autenticado is False
    assert motivo == "senha incorreta"


def test_cadastro_nao_e_refeito_a_cada_login(usuarios):
    """
    Se o cadastro fosse recriado (em vez de só verificado) em todo login,
    uma senha ANTIGA nunca mais autenticaria depois do primeiro login
    seguinte com ela mesma — o que não faz sentido. Confirma que o
    cadastro acontece uma vez só, no primeiro uso, e fica estável.
    """
    usuarios.autenticar("alice", "minhasenha")
    usuarios.autenticar("alice", "minhasenha")
    usuarios.autenticar("alice", "minhasenha")

    autenticado, _ = usuarios.autenticar("alice", "minhasenha")
    assert autenticado is True

    autenticado_errada, motivo = usuarios.autenticar("alice", "outra_senha")
    assert autenticado_errada is False
    assert motivo == "senha incorreta"


def test_nomes_diferentes_tem_contas_independentes(usuarios):
    usuarios.autenticar("alice", "senha_da_alice")
    usuarios.autenticar("bob", "senha_do_bob")

    autenticado_alice, _ = usuarios.autenticar("alice", "senha_da_alice")
    autenticado_bob, _ = usuarios.autenticar("bob", "senha_do_bob")
    assert autenticado_alice is True
    assert autenticado_bob is True

    # senha de um nao serve pro outro
    cruzado, motivo = usuarios.autenticar("alice", "senha_do_bob")
    assert cruzado is False
    assert motivo == "senha incorreta"


def test_nome_e_comparado_sem_distincao_de_maiusculas_minusculas(usuarios):
    """Mesma convenção de RegistroClientes (modelos.py): 'Alice' e
    'alice' são a MESMA conta, não contas diferentes."""
    usuarios.autenticar("Alice", "minhasenha")  # cadastra como "Alice"

    autenticado_minusculo, _ = usuarios.autenticar("alice", "minhasenha")
    autenticado_maiusculo, _ = usuarios.autenticar("ALICE", "minhasenha")
    assert autenticado_minusculo is True
    assert autenticado_maiusculo is True

    # senha errada tambem e reconhecida como a MESMA conta, independente do case
    errado, motivo = usuarios.autenticar("aLiCe", "senha_errada")
    assert errado is False
    assert motivo == "senha incorreta"


def test_senha_nunca_e_gravada_em_texto_puro(usuarios):
    """
    Checa o CONTEÚDO BRUTO do banco (não a API pública) — garante que a
    senha em si nunca aparece armazenada, só um hash derivado dela.
    """
    usuarios.autenticar("alice", "minhasenha_bem_especifica_de_teste")

    cursor = usuarios._conexao.execute("SELECT salt, hash_senha FROM usuarios WHERE nome = ?", ("alice",))
    linha = cursor.fetchone()
    assert linha is not None
    salt, hash_senha = linha

    assert "minhasenha_bem_especifica_de_teste" not in salt
    assert "minhasenha_bem_especifica_de_teste" not in hash_senha


def test_salt_e_diferente_para_cada_usuario_mesmo_com_senha_igual(usuarios):
    """
    Dois usuários com a MESMA senha precisam ter hashes DIFERENTES no
    banco — é o salt que garante isso. Sem salt, duas contas com a
    mesma senha teriam o mesmo hash, o que facilita ataques de tabela
    pré-computada (rainbow table).
    """
    usuarios.autenticar("alice", "mesma_senha_dos_dois")
    usuarios.autenticar("bob", "mesma_senha_dos_dois")

    cursor = usuarios._conexao.execute("SELECT nome, salt, hash_senha FROM usuarios ORDER BY nome")
    linhas = {nome: (salt, hash_senha) for nome, salt, hash_senha in cursor.fetchall()}

    salt_alice, hash_alice = linhas["alice"]
    salt_bob, hash_bob = linhas["bob"]

    assert salt_alice != salt_bob
    assert hash_alice != hash_bob
    assert len(salt_alice) == TAMANHO_SALT_BYTES * 2  # hex de N bytes = 2N caracteres


def test_registro_concorrente_do_mesmo_nome_novo_so_uma_senha_vence():
    """
    duas threads tentando autenticar (== cadastrar, por ser a primeira
    vez) o MESMO nome novo, ao mesmo tempo, com senhas DIFERENTES — só
    uma dessas senhas pode "vencer" e virar a senha real da conta; não
    pode haver um estado inconsistente onde as duas sejam aceitas como
    corretas depois (o que aconteceria se o INSERT não fosse atômico
    sob o lock).
    """
    u = Usuarios(":memory:")
    n = 20
    barreira = threading.Barrier(n)
    resultados = [None] * n

    def tarefa(indice):
        senha = f"senha_{indice}"
        barreira.wait()
        resultados[indice] = u.autenticar("alice", senha)

    threads = [threading.Thread(target=tarefa, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # todas as tentativas devem ter retornado (True, None) nesta rodada
    # -- ou cadastraram (primeira a chegar) ou coincidiram com a senha já
    # cadastrada (impossível todas colidirem, mas nenhuma pode travar/
    # lançar exceção)
    assert all(r is not None for r in resultados)

    # so uma senha das 20 pode ser a "correta" de verdade -- descobre
    # qual foi checando todas
    autenticadas_com_sucesso = [
        i for i in range(n) if u.autenticar(f"alice", f"senha_{i}")[0] is True
    ]
    assert len(autenticadas_com_sucesso) == 1, (
        f"deveria haver exatamente UMA senha vencedora, achei: {autenticadas_com_sucesso}"
    )

    u.fechar()


def test_reabrir_o_mesmo_arquivo_mantem_o_cadastro():
    """Persistência de verdade: fechar e reabrir o MESMO arquivo (não
    :memory:) preserva o cadastro entre execuções."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "teste_usuarios.db")

        u1 = Usuarios(caminho)
        u1.autenticar("alice", "minhasenha")
        u1.fechar()

        u2 = Usuarios(caminho)  # reabre o MESMO arquivo
        autenticado, _ = u2.autenticar("alice", "minhasenha")
        assert autenticado is True

        errado, motivo = u2.autenticar("alice", "senha_errada")
        assert errado is False
        assert motivo == "senha incorreta"

        u2.fechar()


def test_criar_tabela_e_idempotente():
    """Instanciar Usuarios duas vezes sobre o mesmo arquivo não deve
    falhar (CREATE TABLE IF NOT EXISTS)."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "teste.db")
        u1 = Usuarios(caminho)
        u1.fechar()

        u2 = Usuarios(caminho)  # não deve lançar
        u2.fechar()