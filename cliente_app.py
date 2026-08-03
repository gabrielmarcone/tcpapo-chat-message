"""
cliente_app.py — Cliente de chat (tcpapo-chat-message)

Dono: Desenvolvedor B (ver Plano de Divisão de Trabalho, seção 9).
NÃO editado por outra pessoa sem revisão via Pull Request.

Escopo implementado neste arquivo (etapas 1 a 4 da tabela da seção 9):

    Etapa 1 — Leitura de IP/porta via linha de comando (IP obrigatório,
              sem valor padrão; porta com valor padrão) + conexão TCP,
              com tratamento organizado de erro de conexão.
    Etapa 2 — Login: envia 'login', trata 'login_ok'/'login_erro',
              permitindo nova tentativa de nome em caso de erro.
    Etapa 3 — Concorrência: thread dedicada exclusivamente à recepção
              (recv + desserializa + imprime); a thread principal só lê
              input() e envia.
    Etapa 4 — Parsing de comandos digitados pelo usuário (texto comum,
              /priv, /lista, /entrar, /sair_sala, /sair), isolado em
              parse_comando() para não espalhar ifs pelo main().

NÃO implementado ainda (fica para as próximas etapas do Dev B):
    - tratamento de erros mais robusto de conexão em tempo de execução
      (etapa 5);
    - tests/test_cliente.py (etapa 6).

Todo texto digitado que não seja um comando reconhecido (não começa com
"/", ou é um "/" desconhecido/malformado) é tratado como mensagem geral
(broadcast na sala atual) ou como comando inválido — ver parse_comando().
"""

import argparse
import os
import socket
import sys
import threading
from typing import Optional, Tuple

import protocolo


# --------------------------------------------------------------------------
# Etapa 1 — conexão
# --------------------------------------------------------------------------

def conectar(ip: str, porta: int) -> socket.socket:
    """
    Cria um socket TCP e conecta a (ip, porta).

    Trata os erros de conexão mais comuns de forma organizada, com
    mensagem amigável para o usuário, e encerra o programa se a conexão
    não puder ser estabelecida (sem conexão, não há mais nada a fazer
    nas próximas etapas).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
    try:
        sock.connect((ip, porta))
    except ConnectionRefusedError:
        print(f"[erro] conexão recusada por {ip}:{porta} "
              f"(servidor não está escutando nesse endereço/porta?)")
        sock.close()
        sys.exit(1)
    except socket.gaierror:
        print(f"[erro] não foi possível resolver o endereço '{ip}'.")
        sock.close()
        sys.exit(1)
    except socket.timeout:
        print(f"[erro] tempo esgotado ao tentar conectar a {ip}:{porta}.")
        sock.close()
        sys.exit(1)
    except OSError as erro:
        print(f"[erro] falha de rede ao conectar a {ip}:{porta}: {erro}")
        sock.close()
        sys.exit(1)

    print(f"[ok] conectado a {ip}:{porta}")
    return sock


# --------------------------------------------------------------------------
# Impressão de mensagens recebidas do servidor
# --------------------------------------------------------------------------
# Função compartilhada entre realizar_login() (mensagens que cheguem antes
# da resposta de login) e thread_recepcao() (todo o resto), para o
# usuário ver a mensagem formatada da mesma forma nos dois casos.

def imprimir_mensagem(msg: dict) -> None:
    """Formata e imprime na tela uma mensagem já desserializada do servidor."""
    tipo = msg.get("tipo")

    if tipo == protocolo.TIPO_MENSAGEM_GERAL:
        print(f"[geral] {msg.get('remetente', '?')}: {msg.get('texto', '')}")
    elif tipo == protocolo.TIPO_MENSAGEM_PRIVADA:
        print(f"[privado de {msg.get('remetente', '?')}] {msg.get('texto', '')}")
    elif tipo == protocolo.TIPO_NOTIFICACAO:
        print(f"* {msg.get('texto', '')}")
    elif tipo == protocolo.TIPO_ERRO:
        print(f"[erro do servidor] {msg.get('motivo', '')}")
    elif tipo == protocolo.TIPO_LISTA_USUARIOS:
        # Adicionado na etapa 4: sem isso, a resposta de /lista caía no
        # "else" genérico abaixo e mostrava o dict cru na tela.
        usuarios = msg.get("usuarios", [])
        if not usuarios:
            print("[lista] nenhum usuário conectado.")
        else:
            print("[lista] usuários conectados:")
            for usuario in usuarios:
                nome = usuario.get("nome", "?")
                sala = usuario.get("sala", "?")
                print(f"    - {nome} (sala: {sala})")
    else:
        # Tipo não tratado ainda (não deveria acontecer, dado o contrato
        # fechado de protocolo.py). Não inventamos formatação para campos
        # que não conhecemos — só exibimos bruto.
        print(f"[{tipo}] {msg}")


# --------------------------------------------------------------------------
# Etapa 2 — login
# --------------------------------------------------------------------------

def realizar_login(sock: socket.socket) -> Tuple[str, bytes]:
    """
    Pede um apelido ao usuário, envia 'login' (via protocolo.msg_login) e
    espera a resposta do servidor.

    - Se vier login_ok: retorna (nome_confirmado, buffer_restante).
      buffer_restante contém quaisquer bytes já recebidos além da
      resposta de login (ex: se o servidor emendou uma notificação no
      mesmo pacote) — repassado para thread_recepcao() não perder nada.
    - Se vier login_erro: mostra o motivo e pede outro apelido,
      reenviando a mensagem (laço externo).

    Não modifica protocolo.py; usa só as funções já prontas de lá.
    """
    buffer = b""

    while True:
        nome = input("Escolha um apelido: ").strip()
        if not nome:
            print("[aviso] o apelido não pode ser vazio.")
            continue

        try:
            sock.sendall(protocolo.serializar(protocolo.msg_login(nome)))
        except OSError as erro:
            print(f"[erro] falha ao enviar login: {erro}")
            sock.close()
            sys.exit(1)

        resposta = None
        while resposta is None:
            try:
                dados = sock.recv(4096)
            except OSError as erro:
                print(f"[erro] conexão perdida durante o login: {erro}")
                sock.close()
                sys.exit(1)

            if not dados:
                print("[erro] servidor fechou a conexão durante o login.")
                sock.close()
                sys.exit(1)

            buffer += dados
            try:
                mensagens, buffer = protocolo.extrair_mensagens(buffer)
            except protocolo.ErroProtocolo as erro:
                print(f"[erro de protocolo] {erro}")
                continue

            for msg in mensagens:
                if msg["tipo"] in (protocolo.TIPO_LOGIN_OK, protocolo.TIPO_LOGIN_ERRO):
                    resposta = msg
                    break
                # Mensagem que não é resposta de login (situação incomum,
                # mas o framing permite): só exibimos e seguimos esperando.
                imprimir_mensagem(msg)

        if resposta["tipo"] == protocolo.TIPO_LOGIN_OK:
            print(f"[ok] login bem-sucedido como '{resposta['nome']}'.")
            return resposta["nome"], buffer

        print(f"[login recusado] {resposta['motivo']}")
        # volta ao topo do laço externo para pedir outro apelido


# --------------------------------------------------------------------------
# Etapa 3 — thread de recepção
# --------------------------------------------------------------------------

def thread_recepcao(
    sock: socket.socket,
    buffer_inicial: bytes,
    evento_encerrando: threading.Event,
) -> None:
    """
    Roda em thread separada. Só faz três coisas, nesta ordem, em loop:
    recebe bytes do socket, desserializa via protocolo.extrair_mensagens,
    imprime cada mensagem completa. Nunca lê input() do usuário.

    Encerra silenciosamente quando evento_encerrando é sinalizado (pela
    thread principal, em encerrar()) ou quando o servidor fecha a conexão.
    """
    buffer = buffer_inicial

    while not evento_encerrando.is_set():
        try:
            dados = sock.recv(4096)
        except OSError:
            # Socket foi fechado (por encerrar(), na thread principal) ou
            # caiu — nos dois casos, não há mais nada a receber.
            break

        if not dados:
            print("\n[servidor] a conexão foi encerrada pelo servidor.")
            evento_encerrando.set()
            os._exit(0)

        buffer += dados
        try:
            mensagens, buffer = protocolo.extrair_mensagens(buffer)
        except protocolo.ErroProtocolo as erro:
            print(f"\n[erro de protocolo] {erro}")
            continue

        for msg in mensagens:
            imprimir_mensagem(msg)

    evento_encerrando.set()


# --------------------------------------------------------------------------
# Envio (usado pela thread principal)
# --------------------------------------------------------------------------

def enviar(sock: socket.socket, mensagem: dict) -> bool:
    """
    Serializa `mensagem` (via protocolo.serializar) e envia pelo socket.
    Retorna True se enviou com sucesso, False se houve falha de rede
    (nesse caso já imprime um erro amigável; quem chama decide o que
    fazer a seguir).
    """
    try:
        sock.sendall(protocolo.serializar(mensagem))
        return True
    except protocolo.ErroProtocolo as erro:
        # Só ocorreria por engano de programação local (dict fora do
        # formato) — nunca por causa do que o usuário digitou.
        print(f"[erro interno] mensagem malformada não enviada: {erro}")
        return False
    except OSError as erro:
        print(f"[erro] falha ao enviar mensagem: {erro}")
        return False


# --------------------------------------------------------------------------
# Etapa 4 — parsing de comandos
# --------------------------------------------------------------------------
# Todas as ações possíveis do que o usuário digita, decididas em um único
# lugar (parse_comando) em vez de espalhadas como ifs pelo main().

ACAO_ENVIAR = "enviar"      # mensagem pronta (dict) para enviar ao servidor
ACAO_SAIR = "sair"          # usuário pediu /sair: quem chama deve encerrar
ACAO_INVALIDO = "invalido"  # comando malformado/desconhecido; ajuda já impressa
ACAO_VAZIO = "vazio"        # linha vazia/só espaço; nada a fazer

COMANDO_PRIV = "/priv"
COMANDO_LISTA = "/lista"
COMANDO_ENTRAR = "/entrar"
COMANDO_SAIR_SALA = "/sair_sala"
COMANDO_SAIR = "/sair"

TEXTO_AJUDA_COMANDOS = (
    "Comandos disponíveis:\n"
    f"  {COMANDO_PRIV} <usuario> <mensagem>  - envia mensagem privada\n"
    f"  {COMANDO_LISTA}                      - lista usuários conectados\n"
    f"  {COMANDO_ENTRAR} <sala>              - entra em uma sala\n"
    f"  {COMANDO_SAIR_SALA}                  - volta para a sala geral\n"
    f"  {COMANDO_SAIR}                       - encerra a conexão\n"
    "Qualquer outro texto (que não comece com '/') é enviado como mensagem geral."
)


def _comando_invalido(uso: str) -> Tuple[str, None]:
    """Imprime a mensagem de ajuda para um comando malformado e devolve o
    par (ACAO_INVALIDO, None) que parse_comando() deve retornar. Nenhuma
    mensagem é enviada ao servidor nesse caso."""
    print(f"[uso] {uso}")
    print(TEXTO_AJUDA_COMANDOS)
    return ACAO_INVALIDO, None


def parse_comando(texto: str) -> Tuple[str, Optional[dict]]:
    """
    Interpreta uma linha digitada pelo usuário e decide o que fazer com
    ela, sem nunca levantar exceção — qualquer entrada do usuário
    (incluindo lixo) é tratada aqui dentro.

    Retorna (acao, mensagem):
        (ACAO_ENVIAR, dict)   -> dict é uma mensagem pronta, já montada
                                 com uma função protocolo.msg_*, para
                                 enviar ao servidor.
        (ACAO_SAIR, None)     -> usuário digitou '/sair'; quem chama deve
                                 encerrar a conexão (não há nada a enviar
                                 ao servidor: o encerramento é local,
                                 conforme decisão já tomada em
                                 encerrar()/main() para esta etapa).
        (ACAO_INVALIDO, None) -> a linha começa com '/' mas não é um
                                 comando reconhecido, ou está mal
                                 formada (faltam argumentos obrigatórios,
                                 ou há argumentos onde não deveria).
                                 A mensagem de ajuda já foi impressa;
                                 nada deve ser enviado ao servidor.
        (ACAO_VAZIO, None)    -> linha vazia ou só espaços em branco;
                                 nada a fazer.

    Texto comum (que não começa com '/') vira sempre mensagem geral, via
    protocolo.msg_mensagem_geral_enviar() — igual ao comportamento das
    etapas 1-3, sem mudança para quem só digita texto normal.
    """
    texto = texto.strip()

    if not texto:
        return ACAO_VAZIO, None

    if not texto.startswith("/"):
        return ACAO_ENVIAR, protocolo.msg_mensagem_geral_enviar(texto)

    partes = texto.split(maxsplit=1)
    comando = partes[0].lower()
    resto = partes[1] if len(partes) > 1 else ""

    if comando == COMANDO_PRIV:
        sub_partes = resto.split(maxsplit=1)
        if len(sub_partes) < 2 or not sub_partes[1].strip():
            return _comando_invalido(f"{COMANDO_PRIV} <usuario> <mensagem>")
        destinatario, texto_msg = sub_partes[0], sub_partes[1]
        return ACAO_ENVIAR, protocolo.msg_mensagem_privada_enviar(destinatario, texto_msg)

    if comando == COMANDO_LISTA:
        if resto:
            return _comando_invalido(f"{COMANDO_LISTA} não aceita argumentos")
        return ACAO_ENVIAR, protocolo.msg_listar_usuarios()

    if comando == COMANDO_ENTRAR:
        sala = resto.strip()
        if not sala:
            return _comando_invalido(f"{COMANDO_ENTRAR} <sala>")
        return ACAO_ENVIAR, protocolo.msg_entrar_sala(sala)

    if comando == COMANDO_SAIR_SALA:
        if resto:
            return _comando_invalido(f"{COMANDO_SAIR_SALA} não aceita argumentos")
        return ACAO_ENVIAR, protocolo.msg_sair_sala()

    if comando == COMANDO_SAIR:
        if resto:
            return _comando_invalido(f"{COMANDO_SAIR} não aceita argumentos")
        return ACAO_SAIR, None

    return _comando_invalido(f"comando desconhecido '{comando}'")


# --------------------------------------------------------------------------
# Encerramento
# --------------------------------------------------------------------------

def encerrar(sock: socket.socket, evento_encerrando: threading.Event) -> None:
    """
    Fecha a conexão de forma organizada.

    O comando /sair (via parse_comando) apenas sinaliza para o main()
    encerrar localmente — não enviamos protocolo.msg_sair() pela rede de
    propósito nesta etapa, já que o servidor já trata desconexão abrupta
    como caminho normal (ver plano, tarefa 11 do Dev A), então
    simplesmente fechar o socket é suficiente e correto para o escopo
    atual.
    """
    evento_encerrando.set()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # já pode estar fechado do outro lado; não é um erro real aqui
    sock.close()
    print("[ok] conexão encerrada.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cliente de chat (protocolo tcpapo-chat-message)"
    )
    parser.add_argument(
        "--ip", required=True,
        help="IP do servidor (obrigatório; não use localhost/127.0.0.1 fixo no código)",
    )
    parser.add_argument(
        "--porta", type=int, default=5000,
        help="Porta do servidor (padrão: 5000)",
    )
    args = parser.parse_args()

    sock = conectar(args.ip, args.porta)
    nome, buffer_inicial = realizar_login(sock)

    evento_encerrando = threading.Event()
    thread = threading.Thread(
        target=thread_recepcao,
        args=(sock, buffer_inicial, evento_encerrando),
        daemon=True,
    )
    thread.start()

    print(f"Bem-vindo(a), {nome}. Digite uma mensagem e pressione Enter "
          f"para enviar ao chat geral, ou {COMANDO_SAIR} para encerrar. "
          f"Ctrl+C também encerra.")
    print(TEXTO_AJUDA_COMANDOS)

    try:
        while not evento_encerrando.is_set():
            try:
                texto = input()
            except EOFError:
                # entrada padrão fechada (ex: `< /dev/null` ou pipe encerrado)
                break

            if evento_encerrando.is_set():
                # servidor pode ter caído enquanto o usuário digitava
                break

            acao, mensagem = parse_comando(texto)

            if acao in (ACAO_VAZIO, ACAO_INVALIDO):
                continue

            if acao == ACAO_SAIR:
                print(f"[info] encerrando ({COMANDO_SAIR})...")
                break

            # acao == ACAO_ENVIAR
            enviar(sock, mensagem)
    except KeyboardInterrupt:
        print("\n[info] encerrando (Ctrl+C)...")
    finally:
        encerrar(sock, evento_encerrando)
        thread.join(timeout=1)


if __name__ == "__main__":
    main()