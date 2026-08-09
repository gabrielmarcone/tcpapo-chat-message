"""
protocolo.py — Protocolo de aplicação do chat (tcpapo-chat-message)

Define o conjunto de mensagens trocadas entre cliente e servidor, sua
serialização em JSON e o framing usado para transmiti-las por um socket
TCP, que não preserva limites de mensagem.

Responsabilidades:
    - Definir os tipos de mensagem e os campos que cada um exige,
      conforme a Especificação de Arquitetura do projeto.
    - Serializar mensagens: dict -> uma linha de texto JSON terminada em "\n".
    - Desserializar mensagens a partir de um buffer de bytes acumulado,
      validando a forma de cada uma.

Nenhuma outra parte do projeto deve montar ou interpretar JSON
manualmente — sempre usar as funções deste módulo.

Uso típico do framing, dentro do loop de leitura de uma conexão:

    buffer = b""
    while True:
        dados = sock.recv(4096)
        if not dados:
            break  # conexão fechada do outro lado
        buffer += dados
        try:
            mensagens, buffer = extrair_mensagens(buffer)
        except ErroProtocolo:
            ...  # decisão de quem chama: ignorar a linha ou encerrar a conexão
        for msg in mensagens:
            processar(msg)
"""

import json
from typing import Any, Optional

ENCODING = "utf-8"

# Porta UDP padrão usada pela descoberta automática de servidor (ver
# TIPO_DESCOBRIR_SERVIDOR / TIPO_SERVIDOR_AQUI mais abaixo). Fica aqui,
# não em servidor.py nem cliente_app.py, porque — diferente da porta
# TCP do chat, que cada lado escolhe livremente e nem precisa combinar
# com antecedência — o valor da porta de descoberta é, por natureza,
# um acordo de protocolo: os dois lados só se encontram se concordarem
# de antemão em qual porta procurar, então faz sentido ela morar junto
# com o resto do vocabulário compartilhado.
PORTA_DESCOBERTA_PADRAO = 5001


class ErroProtocolo(Exception):
    """
    Levantada quando uma mensagem não segue o formato mínimo do
    protocolo: não é um objeto JSON, ou não tem um campo 'tipo' (string
    não vazia). Cobre tanto o uso local incorreto (serializar() chamado
    com algo inválido) quanto dado malformado vindo da rede
    (extrair_mensagens()).

    Quando levantada por extrair_mensagens() no meio do processamento de
    um buffer com mais de uma mensagem, carrega o progresso já feito,
    para que a linha malformada possa ser descartada sem impedir o
    processamento das mensagens válidas antes e depois dela:

        mensagens_processadas: mensagens válidas já extraídas antes da
            linha que causou o erro.
        buffer_restante: o que sobra do buffer logo após a linha
            malformada (já descartada), pronto para uma nova chamada a
            extrair_mensagens sem reprocessar a linha ruim.

    Quando levantada por serializar() — que não tem conceito de buffer
    — esses atributos ficam com seus valores padrão e podem ser
    ignorados.
    """

    def __init__(self, mensagem: str, mensagens_processadas=None, buffer_restante: bytes = b""):
        super().__init__(mensagem)
        self.mensagens_processadas = mensagens_processadas if mensagens_processadas is not None else []
        self.buffer_restante = buffer_restante


# --------------------------------------------------------------------------
# Tipos de mensagem
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

# Descoberta automática de servidor — via UDP, nunca pela conexão TCP
# do chat em si (que exige já saber o endereço de antemão). Mensagens
# soltas, sem relação de sessão/login com o resto do protocolo: cada
# datagrama UDP já é uma mensagem completa por si só, sem precisar do
# framing por "\n" que o fluxo contínuo do TCP exige — ainda assim,
# reaproveitam serializar()/extrair_mensagens() normalmente, já que um
# único datagrama satisfaz sozinho o formato "uma linha JSON terminada
# em \n" que essas funções esperam.
TIPO_DESCOBRIR_SERVIDOR = "descobrir_servidor"  # Cliente -> Servidor (broadcast)
TIPO_SERVIDOR_AQUI = "servidor_aqui"  # Servidor -> Cliente (resposta direta)

# TIPO_MENSAGEM_GERAL e TIPO_MENSAGEM_PRIVADA são reaproveitados nos dois
# sentidos — o que muda é o conjunto de campos presentes, não o nome do
# tipo. Por isso existe uma função construtora para cada direção, em vez
# de uma função única com campos opcionais.


# --------------------------------------------------------------------------
# Validação de forma (compartilhada entre serializar e extrair_mensagens)
# --------------------------------------------------------------------------

def _validar_mensagem(mensagem: Any, origem: str) -> None:
    """
    Valida que `mensagem` tem o formato mínimo exigido pelo protocolo:
    um objeto (dict) com um campo 'tipo' que seja uma string não vazia.
    `origem` identifica, na mensagem de erro, onde a violação foi
    detectada (serializar ou extrair_mensagens).
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
    válido — é melhor falhar de forma clara no ponto de montagem da
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
            # _validar_mensagem não conhece o conceito de buffer (é usada
            # também por serializar) — reempacota aqui com o progresso já
            # feito, sem duplicar a lógica de validação.
            raise ErroProtocolo(
                str(erro), mensagens_processadas=mensagens, buffer_restante=buffer
            ) from erro

        mensagens.append(mensagem)

    return mensagens, buffer


# --------------------------------------------------------------------------
# Funções auxiliares de construção de mensagem
# --------------------------------------------------------------------------
# Cada função monta exatamente os campos exigidos para aquele tipo e
# direção — reduz erro de digitação de chave em cada ponto de chamada e
# documenta, pelo próprio nome, a direção e os campos esperados.

# --- Cliente -> Servidor ---

def msg_login(nome: str, senha: str) -> dict:
    return {"tipo": TIPO_LOGIN, "nome": nome, "senha": senha}


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
    Pede o histórico recente de mensagens gerais da sala atual do
    remetente — mesmo princípio de mensagem_geral: quem decide o escopo
    é o servidor, a partir do estado interno dele, não um dado que o
    cliente escolhe e manda junto.

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

    Serializado como lista de objetos {"nome": ..., "sala": ...} — mais
    verboso que uma lista de pares posicionais, mas auto-descritivo e
    resistente a mudança futura de ordem ou adição de campo.
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


# --- Descoberta automática de servidor (UDP) ---

def msg_descobrir_servidor() -> dict:
    """
    Cliente -> Servidor, enviada por UDP broadcast (nunca por TCP).
    Pede que qualquer servidor do tcpapo-chat-message escutando na
    rede local se identifique. Não carrega nenhum campo além do tipo —
    não há nada que o cliente precise informar para perguntar "quem
    está aí?".
    """
    return {"tipo": TIPO_DESCOBRIR_SERVIDOR}


def msg_servidor_aqui(porta_tcp: int) -> dict:
    """
    Servidor -> Cliente, resposta direta (unicast) a um pedido de
    descoberta — nunca também por broadcast, já que só quem perguntou
    precisa saber a resposta.

    Carrega só a porta TCP do chat de verdade. O IP do servidor não
    entra no corpo da mensagem porque o cliente já o descobre de graça,
    pelo endereço de origem do datagrama UDP recebido (ver
    socket.recvfrom(), que devolve (dados, (ip_origem, porta_origem))).
    """
    return {"tipo": TIPO_SERVIDOR_AQUI, "porta_tcp": porta_tcp}