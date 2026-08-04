"""
tests/test_protocolo.py — Testes do módulo protocolo.py

Dono: CONJUNTO (Dev A e Dev B, mantido pelos dois).

Este é o teste mais importante do projeto: valida o framing por
delimitador de linha e a validação de forma da mensagem, que são a base
de tudo o resto (servidor, cliente, stubs). Deve passar 100% antes de
servidor.py e cliente_app.py avançarem além do esqueleto.

Inclui os casos descobertos ao comparar as duas implementações
independentes (Dev A e Dev B) antes da fusão: mensagem sem campo 'tipo' e
JSON que não é um objeto agora são rejeitados com ErroProtocolo, em vez de
passarem adiante silenciosamente.
"""

import json

import pytest

import protocolo
from protocolo import ErroProtocolo


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------

def test_serializar_mensagem_simples():
    mensagem = protocolo.msg_login("alice", "senha123")
    bruto = protocolo.serializar(mensagem)

    assert isinstance(bruto, bytes)
    assert bruto.endswith(b"\n")
    assert bruto.count(b"\n") == 1  # exatamente um delimitador de framing

    texto = bruto.decode("utf-8").rstrip("\n")
    assert json.loads(texto) == {"tipo": "login", "nome": "alice", "senha": "senha123"}


def test_serializar_exige_campo_tipo():
    with pytest.raises(ErroProtocolo):
        protocolo.serializar({"nome": "alice"})


def test_serializar_exige_dict():
    with pytest.raises(ErroProtocolo):
        protocolo.serializar("não sou um dict")


def test_serializar_exige_tipo_string_nao_vazia():
    with pytest.raises(ErroProtocolo):
        protocolo.serializar({"tipo": ""})
    with pytest.raises(ErroProtocolo):
        protocolo.serializar({"tipo": 123})


# --------------------------------------------------------------------------
# Framing / extração — casos básicos
# --------------------------------------------------------------------------

def test_extrair_buffer_vazio_nao_quebra():
    mensagens, resto = protocolo.extrair_mensagens(b"")
    assert mensagens == []
    assert resto == b""


def test_extrair_exige_bytes():
    with pytest.raises(TypeError):
        protocolo.extrair_mensagens("não sou bytes")


def test_extrair_uma_mensagem_completa():
    linha = protocolo.serializar(protocolo.msg_sair())
    mensagens, resto = protocolo.extrair_mensagens(linha)

    assert len(mensagens) == 1
    assert mensagens[0] == {"tipo": "sair"}
    assert resto == b""


def test_extrair_duas_mensagens_grudadas_num_unico_buffer():
    # simula dois recv() concatenados num único buffer, como acontece de
    # verdade quando o SO entrega mais de uma mensagem por vez
    linha1 = protocolo.serializar(protocolo.msg_mensagem_geral_enviar("oi"))
    linha2 = protocolo.serializar(protocolo.msg_listar_usuarios())
    buffer = linha1 + linha2

    mensagens, resto = protocolo.extrair_mensagens(buffer)

    assert len(mensagens) == 2
    assert mensagens[0] == {"tipo": "mensagem_geral", "texto": "oi"}
    assert mensagens[1] == {"tipo": "listar_usuarios"}
    assert resto == b""


def test_extrair_mensagem_cortada_ao_meio_entre_duas_chamadas():
    linha_completa = protocolo.serializar(protocolo.msg_sair_sala())
    linha_alvo = protocolo.serializar(protocolo.msg_entrar_sala("jogos"))
    linha_parcial = linha_alvo[:10]
    buffer = linha_completa + linha_parcial

    mensagens, resto = protocolo.extrair_mensagens(buffer)

    # só a mensagem completa deve sair; a parcial nunca é descartada
    assert len(mensagens) == 1
    assert mensagens[0] == {"tipo": "sair_sala"}
    assert resto == linha_parcial

    # simula o restante da segunda mensagem chegando na próxima leitura
    mensagens2, resto2 = protocolo.extrair_mensagens(resto + linha_alvo[10:])
    assert len(mensagens2) == 1
    assert mensagens2[0] == {"tipo": "entrar_sala", "sala": "jogos"}
    assert resto2 == b""


def test_extrair_ignora_linhas_vazias_e_so_espaco():
    buffer = b"\n   \n" + protocolo.serializar(protocolo.msg_sair())
    mensagens, resto = protocolo.extrair_mensagens(buffer)
    assert mensagens == [{"tipo": "sair"}]
    assert resto == b""


def test_extrair_tolera_crlf():
    # caso alguma ferramenta/ambiente injete \r\n em vez de \n puro
    linha = protocolo.serializar(protocolo.msg_sair())
    buffer = linha[:-1] + b"\r\n"  # troca o \n final por \r\n
    mensagens, resto = protocolo.extrair_mensagens(buffer)
    assert mensagens == [{"tipo": "sair"}]
    assert resto == b""


def test_texto_com_quebra_de_linha_nao_quebra_framing():
    """
    Substitui a etapa de "sanitização manual de \n" do plano original: se
    o texto do usuário contém uma quebra de linha real e o framing
    continua correto, sanitização adicional é desnecessária — porque
    json.dumps já escapa o \n como os caracteres \\ e n dentro da string,
    não como um byte de quebra real.
    """
    texto_multilinha = "linha 1\nlinha 2\nlinha 3"
    linha = protocolo.serializar(protocolo.msg_mensagem_geral_enviar(texto_multilinha))

    assert linha.count(b"\n") == 1  # só o delimitador de framing final

    mensagens, resto = protocolo.extrair_mensagens(linha)
    assert mensagens[0]["texto"] == texto_multilinha
    assert resto == b""


# --------------------------------------------------------------------------
# Framing / extração — validação de forma (casos revelados na comparação
# entre as duas implementações independentes, antes da fusão)
# --------------------------------------------------------------------------

def test_extrair_rejeita_mensagem_sem_campo_tipo():
    buffer = json.dumps({"nome": "alice"}).encode("utf-8") + b"\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer)


def test_extrair_rejeita_json_que_nao_e_objeto():
    buffer = json.dumps(["nao", "sou", "um", "objeto"]).encode("utf-8") + b"\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer)


def test_extrair_rejeita_tipo_vazio_ou_nao_string():
    buffer_vazio = json.dumps({"tipo": ""}).encode("utf-8") + b"\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer_vazio)

    buffer_numero = json.dumps({"tipo": 123}).encode("utf-8") + b"\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer_numero)


def test_extrair_linha_json_invalida_levanta_erro_claro():
    buffer = b"isto nao e json valido\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer)


def test_extrair_bytes_invalidos_utf8_levanta_erro_claro():
    # \xff\xfe não é uma sequência utf-8 válida — simula corrupção na rede
    # ou um cliente mal-implementado enviando encoding errado
    buffer = b"\xff\xfe\n"
    with pytest.raises(ErroProtocolo):
        protocolo.extrair_mensagens(buffer)


def test_extrair_mensagem_invalida_no_meio_do_buffer_preserva_as_validas_antes():
    """
    Este é o teste que teria pegado, em protocolo.py, o bug encontrado
    depois via teste de integração em servidor.py: sem os atributos
    mensagens_processadas/buffer_restante na exceção, quem chama perderia
    tanto a mensagem válida de antes quanto a posição no buffer — ficando
    preso reprocessando a mesma linha ruim para sempre.
    """
    valida1 = protocolo.serializar(protocolo.msg_sair())
    invalida = json.dumps({"sem_tipo": True}).encode("utf-8") + b"\n"
    valida2 = protocolo.serializar(protocolo.msg_listar_usuarios())
    buffer = valida1 + invalida + valida2

    with pytest.raises(ErroProtocolo) as exc_info:
        protocolo.extrair_mensagens(buffer)

    erro = exc_info.value
    # a mensagem válida ANTES da linha ruim não pode ser perdida
    assert erro.mensagens_processadas == [{"tipo": "sair"}]

    # o buffer_restante deve conter a mensagem válida DEPOIS da linha
    # ruim, pronta para ser extraída numa nova chamada — sem reprocessar
    # a linha malformada de novo
    mensagens_restantes, resto_final = protocolo.extrair_mensagens(erro.buffer_restante)
    assert mensagens_restantes == [{"tipo": "listar_usuarios"}]
    assert resto_final == b""


def test_extrair_erro_sem_mensagens_processadas_usa_valores_padrao():
    # quando a linha ruim é a primeira do buffer, não há nada antes dela
    buffer = json.dumps({"sem_tipo": True}).encode("utf-8") + b"\n"
    with pytest.raises(ErroProtocolo) as exc_info:
        protocolo.extrair_mensagens(buffer)

    erro = exc_info.value
    assert erro.mensagens_processadas == []
    assert erro.buffer_restante == b""


def test_erro_protocolo_levantado_por_serializar_tem_valores_padrao():
    # serializar() não tem conceito de "buffer" — os atributos extras
    # devem existir com valores padrão inofensivos, sem quebrar quem
    # captura a exceção esperando só a mensagem de texto
    with pytest.raises(ErroProtocolo) as exc_info:
        protocolo.serializar({"nome": "sem tipo"})

    erro = exc_info.value
    assert erro.mensagens_processadas == []
    assert erro.buffer_restante == b""


# --------------------------------------------------------------------------
# Funções auxiliares de construção — cobertura de cada uma
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "construtor,args,esperado",
    [
        (protocolo.msg_login, ("alice", "senha123"), {"tipo": "login", "nome": "alice", "senha": "senha123"}),
        (protocolo.msg_mensagem_geral_enviar, ("oi",), {"tipo": "mensagem_geral", "texto": "oi"}),
        (
            protocolo.msg_mensagem_privada_enviar,
            ("bob", "oi"),
            {"tipo": "mensagem_privada", "destinatario": "bob", "texto": "oi"},
        ),
        (protocolo.msg_listar_usuarios, (), {"tipo": "listar_usuarios"}),
        (protocolo.msg_entrar_sala, ("jogos",), {"tipo": "entrar_sala", "sala": "jogos"}),
        (protocolo.msg_sair_sala, (), {"tipo": "sair_sala"}),
        (protocolo.msg_sair, (), {"tipo": "sair"}),
        (protocolo.msg_login_ok, ("alice",), {"tipo": "login_ok", "nome": "alice"}),
        (protocolo.msg_login_erro, ("nome em uso",), {"tipo": "login_erro", "motivo": "nome em uso"}),
        (
            protocolo.msg_mensagem_geral_repassar,
            ("alice", "oi"),
            {"tipo": "mensagem_geral", "remetente": "alice", "texto": "oi"},
        ),
        (
            protocolo.msg_mensagem_privada_repassar,
            ("alice", "oi"),
            {"tipo": "mensagem_privada", "remetente": "alice", "texto": "oi"},
        ),
        (protocolo.msg_notificacao, ("alice entrou",), {"tipo": "notificacao", "texto": "alice entrou"}),
        (protocolo.msg_erro, ("destinatário inexistente",), {"tipo": "erro", "motivo": "destinatário inexistente"}),
    ],
)
def test_construtores_de_mensagem(construtor, args, esperado):
    assert construtor(*args) == esperado


def test_msg_lista_usuarios_formato_com_chaves_nomeadas():
    usuarios = [("alice", "geral"), ("bob", "jogos")]
    mensagem = protocolo.msg_lista_usuarios(usuarios)

    assert mensagem == {
        "tipo": "lista_usuarios",
        "usuarios": [
            {"nome": "alice", "sala": "geral"},
            {"nome": "bob", "sala": "jogos"},
        ],
    }

    # round-trip completo de serialização + extração
    linha = protocolo.serializar(mensagem)
    mensagens, resto = protocolo.extrair_mensagens(linha)
    assert mensagens[0] == mensagem
    assert resto == b""


def test_msg_historico_sem_limite():
    assert protocolo.msg_historico() == {"tipo": "historico"}


def test_msg_historico_com_limite():
    assert protocolo.msg_historico(10) == {"tipo": "historico", "limite": 10}


def test_msg_historico_resposta_formato():
    mensagens = [
        {"remetente": "alice", "texto": "oi", "hora": "14:32:05"},
        {"remetente": "bob", "texto": "e ai", "hora": "14:33:10"},
    ]
    mensagem = protocolo.msg_historico_resposta("geral", mensagens)

    assert mensagem == {
        "tipo": "historico_resposta",
        "sala": "geral",
        "mensagens": mensagens,
    }

    # round-trip completo de serialização + extração
    linha = protocolo.serializar(mensagem)
    mensagens_extraidas, resto = protocolo.extrair_mensagens(linha)
    assert mensagens_extraidas[0] == mensagem
    assert resto == b""


def test_todas_as_mensagens_construidas_sao_serializaveis():
    """
    Verificação de sanidade: toda função construtora deve produzir algo
    que serializar() aceita sem erro (nenhuma delas deveria, por engano,
    esquecer o campo 'tipo' ou produzir um tipo vazio).
    """
    construidas = [
        protocolo.msg_login("alice", "senha123"),
        protocolo.msg_mensagem_geral_enviar("oi"),
        protocolo.msg_mensagem_privada_enviar("bob", "oi"),
        protocolo.msg_listar_usuarios(),
        protocolo.msg_entrar_sala("jogos"),
        protocolo.msg_sair_sala(),
        protocolo.msg_sair(),
        protocolo.msg_login_ok("alice"),
        protocolo.msg_login_erro("motivo"),
        protocolo.msg_mensagem_geral_repassar("alice", "oi"),
        protocolo.msg_mensagem_privada_repassar("alice", "oi"),
        protocolo.msg_notificacao("texto"),
        protocolo.msg_lista_usuarios([("alice", "geral")]),
        protocolo.msg_erro("motivo"),
        protocolo.msg_historico(),
        protocolo.msg_historico(10),
        protocolo.msg_historico_resposta("geral", [{"remetente": "alice", "texto": "oi", "hora": "14:32:05"}]),
    ]
    for mensagem in construidas:
        protocolo.serializar(mensagem)  # não deve levantar exceção