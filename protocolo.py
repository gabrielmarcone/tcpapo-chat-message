"""
protocolo.py — Protocolo de aplicação do chat (tcpapo-chat-message)

Dono: CONJUNTO (Dev A e Dev B) — feito juntos, uma vez, no início.
Depois de pronto e validado por tests/test_protocolo.py, este arquivo fica
CONGELADO: qualquer alteração exige acordo explícito entre os dois.

Versão MESCLADA a partir de duas implementações independentes (Dev A e
Dev B), reconciliando as diferenças entre elas. Decisões tomadas na fusão,
para referência de ambos:

    1. Exceção própria `ErroProtocolo` (ideia do Dev B) para qualquer
       violação de forma da mensagem — seja ela originada localmente
       (serializar() chamado com algo que não é uma mensagem válida) ou
       vinda da rede (extrair_mensagens() recebendo uma linha que não é
       um objeto JSON com 'tipo'). Um único tipo de exceção para "isto
       não é uma mensagem de protocolo válida" simplifica quem consome
       este módulo: um `except ErroProtocolo` cobre os dois sentidos.
       `TypeError` é reservado só para engano de tipo Python na própria
       chamada da função (ex: passar uma string em vez de bytes para
       extrair_mensagens) — isso nunca pode vir da rede, só de uso
       incorreto da API por quem programa.

    2. `extrair_mensagens()` agora valida que toda mensagem extraída é um
       dict com campo 'tipo' (string não vazia) — antes, uma das duas
       versões deixava passar silenciosamente uma mensagem malformada
       (ex: JSON sem 'tipo', ou um array JSON solto), que só ia quebrar
       mais adiante, longe da causa real. A validação usa a mesma função
       interna que serializar() usa, então a regra é idêntica nos dois
       sentidos.

    3. Nomes das funções construtoras de mensagem_geral/mensagem_privada
       adotados do Dev B (`_enviar` / `_repassar`) — descrevem a ação
       (o que a função faz) em vez de só o ator (quem chama), o que lê
       melhor no ponto de uso.

    4. Formato de `msg_lista_usuarios` adotado do Dev A: lista de objetos
       {"nome": ..., "sala": ...} em vez de pares posicionais
       [nome, sala]. Um pouco mais verboso no fio, mas auto-descritivo e
       resistente a mudança futura de ordem ou adição de campo — quem
       consome não precisa saber de cor que o índice 0 é nome.

    5. Tolerância a "\\r\\n" (residual de CRLF) mantida explicitamente
       (Dev A), mesmo que testes tenham confirmado que json.loads já
       ignora esse "\\r" como espaço em branco final por conta própria —
       é uma linha de código a mais, mas remove qualquer dependência
       desse comportamento não documentado do parser.

Responsabilidade:
    - Definir os tipos de mensagem trocados entre cliente e servidor,
      conforme a tabela da seção 8.3 da Especificação de Arquitetura.
    - Serializar mensagens: dict -> uma linha de texto JSON terminada em "\n".
    - Desserializar mensagens a partir de um buffer de bytes acumulado
      (framing por delimitador de linha), validando a forma de cada uma.

Usado por:
    - servidor.py e dev_tools/cliente_stub.py (Dev A)
    - cliente_app.py e dev_tools/servidor_stub.py (Dev B)

Todos os quatro pontos acima DEVEM importar e usar as funções deste módulo
— nunca montar ou interpretar JSON manualmente em outro lugar do projeto.

Uso típico do framing, dentro do loop de leitura de uma conexão:

    buffer = b""
    while True:
        dados = sock.recv(4096)
        if not dados:
            break  # conexão fechada do outro lado
        buffer += dados
        try:
            mensagens, buffer = extrair_mensagens(buffer)
        except ErroProtocolo as erro:
            # decisão de quem chama: logar e ignorar, ou encerrar a
            # conexão — ver servidor.py / cliente_app.py para a escolha
            # feita em cada caso.
            ...
        for msg in mensagens:
            processar(msg)
"""

import json
from typing import Any, Optional

ENCODING = "utf-8"


class ErroProtocolo(Exception):
    """
    Mensagem que não segue o formato mínimo do protocolo: não é um objeto
    JSON, ou é um objeto sem o campo obrigatório 'tipo' (string não
    vazia). Levantada tanto por serializar() (uso local incorreto) quanto
    por extrair_mensagens() (dado malformado vindo da rede) — ver decisão
    1 no docstring do módulo.

    Quando levantada por extrair_mensagens() no meio do processamento de
    um buffer com mais de uma mensagem, carrega dois atributos extras
    para que quem chama não perca o progresso já feito (bug encontrado
    via teste de integração: sem isso, a linha malformada nunca era
    consumida do buffer, e o servidor ficava preso reprocessando a mesma
    linha ruim para sempre, nunca alcançando mensagens válidas seguintes):

        mensagens_processadas: lista de mensagens válidas já extraídas
            ANTES da linha que causou o erro — podem ser processadas
            normalmente por quem chama.
        buffer_restante: o que sobra do buffer imediatamente APÓS a linha
            malformada (que já foi descartada) — pronto para uma nova
            chamada a extrair_mensagens, sem reprocessar a linha ruim.

    Quando levantada por serializar() (que não tem conceito de "buffer"),
    esses atributos ficam com seus valores padrão (lista vazia / bytes
    vazios) e podem ser ignorados.
    """

    def __init__(self, mensagem: str, mensagens_processadas=None, buffer_restante: bytes = b""):
        super().__init__(mensagem)
        self.mensagens_processadas = mensagens_processadas if mensagens_processadas is not None else []
        self.buffer_restante = buffer_restante


# --------------------------------------------------------------------------
# Constantes de tipo de mensagem (seção 8.3 da Especificação de Arquitetura)
# --------------------------------------------------------------------------

# Cliente -> Servidor
TIPO_LOGIN = "login"
TIPO_MENSAGEM_GERAL = "mensagem_geral"
TIPO_MENSAGEM_PRIVADA = "mensagem_privada"
TIPO_LISTAR_USUARIOS = "listar_usuarios"
TIPO_ENTRAR_SALA = "entrar_sala"
TIPO_SAIR_SALA = "sair_sala"
TIPO_SAIR = "sair"
TIPO_HISTORICO = "historico"

# Servidor -> Cliente
TIPO_LOGIN_OK = "login_ok"
TIPO_LOGIN_ERRO = "login_erro"
TIPO_LISTA_USUARIOS = "lista_usuarios"
TIPO_NOTIFICACAO = "notificacao"
TIPO_ERRO = "erro"
TIPO_HISTORICO_RESPOSTA = "historico_resposta"

# TIPO_MENSAGEM_GERAL e TIPO_MENSAGEM_PRIVADA são reutilizados nos dois
# sentidos (seção 8.3, observação de design) — o que muda é o conjunto de
# campos presentes, não o nome do tipo. Por isso existem duas funções
# construtoras para cada um (uma por direção), em vez de uma função única
# com campo opcional — evita esquecer um campo obrigatório de um lado.


# --------------------------------------------------------------------------
# Validação de forma (compartilhada entre serializar e extrair_mensagens)
# --------------------------------------------------------------------------

def _validar_mensagem(mensagem: Any, origem: str) -> None:
    """
    Valida que `mensagem` tem o formato mínimo exigido por todo o
    protocolo: um objeto (dict) com um campo 'tipo' que seja uma string
    não vazia. `origem` é só para a mensagem de erro indicar onde a
    violação foi detectada (serializar ou extrair_mensagens).
    """
    if not isinstance(mensagem, dict):
        raise ErroProtocolo(
            f"{origem}: mensagem deve ser um objeto JSON (dict), recebido: {mensagem!r}"
        )
    tipo = mensagem.get("tipo")
    if not isinstance(tipo, str) or not tipo:
        raise ErroProtocolo(
            f"{origem}: mensagem sem campo 'tipo' válido (string não vazia): {mensagem!r}"
        )


# --------------------------------------------------------------------------
# Serialização
# --------------------------------------------------------------------------

def serializar(mensagem: dict) -> bytes:
    """
    Converte um dicionário em uma linha JSON pronta para envio pela rede,
    já codificada em bytes e terminada por '\n'.

    Levanta ErroProtocolo se a mensagem não for um dict com campo 'tipo'
    válido — é melhor falhar alto e claro no ponto de montagem da
    mensagem do que enviar algo inválido pela rede.
    """
    _validar_mensagem(mensagem, origem="serializar")
    linha = json.dumps(mensagem, ensure_ascii=False) + "\n"
    return linha.encode(ENCODING)


# --------------------------------------------------------------------------
# Framing / desserialização
# --------------------------------------------------------------------------

def extrair_mensagens(buffer: bytes) -> tuple[list, bytes]:
    """
    Extrai todas as mensagens JSON completas presentes em `buffer`
    (delimitadas por "\n"), retornando (lista_de_mensagens, resto_do_buffer).

    O resto_do_buffer contém qualquer mensagem parcial (ainda sem "\n"
    recebido) e deve ser preservado pelo chamador para ser concatenado com
    os próximos bytes recebidos via socket.recv() na chamada seguinte.

    Trata corretamente:
        - Nenhuma mensagem completa ainda -> retorna ([], buffer inteiro).
        - Uma ou mais mensagens completas grudadas no mesmo buffer
          (recv() concatenando dois envios) -> retorna todas, na ordem.
        - "\r\n" em vez de "\n" -> o "\r" residual é removido antes do parse.
        - Linhas vazias ou só com espaço em branco -> ignoradas, sem erro.

    Levanta ErroProtocolo se uma linha completa não for um JSON válido,
    não for um objeto, ou não tiver um campo 'tipo' válido — nesses casos
    é uma violação real de protocolo, não uma condição esperada.
    Levanta TypeError se `buffer` não for bytes/bytearray (erro de uso da
    API, nunca pode vir de dado recebido pela rede).
    """
    if not isinstance(buffer, (bytes, bytearray)):
        raise TypeError(f"buffer deve ser bytes, recebido {type(buffer).__name__}")

    mensagens = []
    buffer = bytes(buffer)

    while b"\n" in buffer:
        linha_bytes, buffer = buffer.split(b"\n", 1)
        linha_bytes = linha_bytes.rstrip(b"\r")

        if not linha_bytes.strip():
            continue  # linha vazia/só espaço em branco — ignorada, sem erro

        try:
            texto = linha_bytes.decode(ENCODING)
        except UnicodeDecodeError as exc:
            raise ErroProtocolo(
                f"linha recebida não é utf-8 válido: {linha_bytes!r}",
                mensagens_processadas=mensagens,
                buffer_restante=buffer,
            ) from exc

        try:
            mensagem = json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ErroProtocolo(
                f"linha recebida não é um JSON válido: {texto!r}",
                mensagens_processadas=mensagens,
                buffer_restante=buffer,
            ) from exc

        try:
            _validar_mensagem(mensagem, origem="extrair_mensagens")
        except ErroProtocolo as erro:
            # _validar_mensagem não sabe de buffer/progresso (é usada
            # também por serializar) — reempacota aqui com o contexto de
            # recuperação, sem duplicar a lógica de validação.
            raise ErroProtocolo(
                str(erro), mensagens_processadas=mensagens, buffer_restante=buffer
            ) from erro

        mensagens.append(mensagem)

    return mensagens, buffer


# --------------------------------------------------------------------------
# Funções auxiliares de construção de mensagem
# --------------------------------------------------------------------------
# Cada função monta exatamente os campos que a seção 8.3 define para
# aquele tipo/direção — reduz erro de digitação de chave em cada ponto de
# chamada e documenta, pelo próprio nome, a direção e os campos exigidos.

# --- Cliente -> Servidor ---

def msg_login(nome: str) -> dict:
    return {"tipo": TIPO_LOGIN, "nome": nome}


def msg_mensagem_geral_enviar(texto: str) -> dict:
    """Cliente -> Servidor. Sem campo 'remetente': o servidor determina o
    escopo/remetente a partir do estado interno (sala_atual de quem envia)."""
    return {"tipo": TIPO_MENSAGEM_GERAL, "texto": texto}


def msg_mensagem_privada_enviar(destinatario: str, texto: str) -> dict:
    return {"tipo": TIPO_MENSAGEM_PRIVADA, "destinatario": destinatario, "texto": texto}


def msg_listar_usuarios() -> dict:
    return {"tipo": TIPO_LISTAR_USUARIOS}


def msg_entrar_sala(sala: str) -> dict:
    return {"tipo": TIPO_ENTRAR_SALA, "sala": sala}


def msg_sair_sala() -> dict:
    return {"tipo": TIPO_SAIR_SALA}


def msg_sair() -> dict:
    return {"tipo": TIPO_SAIR}


def msg_historico(limite: Optional[int] = None) -> dict:
    """
    Pede o histórico recente de mensagens gerais da SALA ATUAL do
    remetente — mesmo princípio de mensagem_geral (seção 8.3): quem
    decide o escopo é o servidor, a partir do estado interno dele, não
    um dado que o cliente escolhe e manda junto.

    `limite` é opcional; se omitido, o servidor aplica um padrão
    razoável (e também um teto máximo, para não permitir pedir a tabela
    inteira de uma vez).
    """
    mensagem = {"tipo": TIPO_HISTORICO}
    if limite is not None:
        mensagem["limite"] = limite
    return mensagem


# --- Servidor -> Cliente ---

def msg_login_ok(nome: str) -> dict:
    return {"tipo": TIPO_LOGIN_OK, "nome": nome}


def msg_login_erro(motivo: str) -> dict:
    return {"tipo": TIPO_LOGIN_ERRO, "motivo": motivo}


def msg_mensagem_geral_repassar(remetente: str, texto: str) -> dict:
    """Servidor -> Cliente. Broadcast repassado aos membros da sala."""
    return {"tipo": TIPO_MENSAGEM_GERAL, "remetente": remetente, "texto": texto}


def msg_mensagem_privada_repassar(remetente: str, texto: str) -> dict:
    """Servidor -> Cliente (destinatário). Mensagem privada repassada."""
    return {"tipo": TIPO_MENSAGEM_PRIVADA, "remetente": remetente, "texto": texto}


def msg_notificacao(texto: str) -> dict:
    return {"tipo": TIPO_NOTIFICACAO, "texto": texto}


def msg_lista_usuarios(usuarios: list) -> dict:
    """
    usuarios: lista de pares (nome, sala), ex: [("alice", "geral"), ("bob", "jogos")]

    Serializado como lista de objetos {"nome": ..., "sala": ...} — decisão
    tomada na fusão das duas versões (ver item 4 no docstring do módulo).
    """
    return {
        "tipo": TIPO_LISTA_USUARIOS,
        "usuarios": [{"nome": nome, "sala": sala} for nome, sala in usuarios],
    }


def msg_historico_resposta(sala: str, mensagens: list) -> dict:
    """
    `mensagens`: lista de dicts já no formato final de exibição, cada um
    com "remetente", "texto" e "hora" (string já formatada, ex:
    "14:32:05") — o servidor formata a hora, o cliente só exibe, mesmo
    princípio de "servidor decide, cliente exibe" usado no resto do
    protocolo. Vem em ordem cronológica (mais antiga primeiro).
    """
    return {"tipo": TIPO_HISTORICO_RESPOSTA, "sala": sala, "mensagens": mensagens}


def msg_erro(motivo: str) -> dict:
    return {"tipo": TIPO_ERRO, "motivo": motivo}