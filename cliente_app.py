"""
cliente_app.py — Cliente de chat (tcpapo-chat-message)

Dono: Desenvolvedor B (ver Plano de Divisão de Trabalho, seção 9).
NÃO editado por outra pessoa sem revisão via Pull Request.

Escopo implementado neste arquivo (etapas 1 a 5 da tabela da seção 9):

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
    Etapa 5 — Robustez: tratamento de todos os cenários de erro de rede
              e de entrada (IP/porta inválidos, servidor indisponível,
              timeout, conexão derrubada em qualquer momento da sessão,
              Ctrl+C) sem tracebacks e com encerramento limpo dos
              recursos (socket, thread). Ver notas de cada função abaixo
              para o que foi acrescentado e por quê.

NÃO implementado ainda (fica para a próxima etapa do Dev B):
    - tests/test_cliente.py (etapa 6) — entregue como arquivo separado.

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
# Constantes de robustez (etapa 5)
# --------------------------------------------------------------------------

# Timeout aplicado SÓ durante a tentativa de connect() (etapa 1/5). Depois
# de conectado, o socket volta ao modo bloqueante padrão (sock.settimeout
# (None) em conectar()) porque o restante da sessão já depende de recv()
# bloqueante rodando em thread própria (etapa 3) — não faria sentido (nem
# foi pedido) um timeout de inatividade na conversa.
TIMEOUT_CONEXAO = 5.0  # segundos


# --------------------------------------------------------------------------
# Etapa 5 — validação de argumentos de linha de comando
# --------------------------------------------------------------------------
# Usada como `type=` no argparse para --porta. Sem isso, uma porta como
# "abc" já falha no argparse com uma mensagem razoável (sem traceback),
# mas uma porta fora da faixa válida de portas TCP (ex: 0 ou 99999) seria
# aceita sem erro e só explodiria mais tarde, de forma confusa, dentro de
# socket.connect(). Centralizamos a checagem aqui para falhar cedo e com
# mensagem clara, no mesmo padrão de erro do argparse (sem traceback).

def validar_porta(valor: str) -> int:
    try:
        porta = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"porta deve ser um número inteiro, recebido: '{valor}'"
        )
    if not (1 <= porta <= 65535):
        raise argparse.ArgumentTypeError(
            f"porta deve estar entre 1 e 65535, recebido: {porta}"
        )
    return porta


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

    Etapa 5: acrescentado sock.settimeout(TIMEOUT_CONEXAO) antes do
    connect() — sem um timeout explícito, socket.timeout nunca era de
    fato levantado (o except já existia, mas era código morto), e uma
    tentativa de conexão a um IP que existe na rede mas não responde
    (ex: firewall descartando o pacote silenciosamente, em vez de
    recusar a conexão) ficava travada indefinidamente em vez de falhar
    com uma mensagem clara. Depois de conectar com sucesso, o timeout é
    removido (settimeout(None)) para não afetar o recv() bloqueante
    usado pelo resto da aplicação (etapa 3).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_CONEXAO)
    try:
        sock.connect((ip, porta))
    except ConnectionRefusedError:
        print(f"[erro] conexão recusada por {ip}:{porta} "
              f"(servidor não está escutando nesse endereço/porta?)")
        sock.close()
        sys.exit(1)
    except socket.gaierror:
        print(f"[erro] não foi possível resolver o endereço '{ip}' "
              f"(IP ou host inválido).")
        sock.close()
        sys.exit(1)
    except socket.timeout:
        print(f"[erro] tempo esgotado ({TIMEOUT_CONEXAO:.0f}s) ao tentar "
              f"conectar a {ip}:{porta} — servidor pode estar "
              f"indisponível ou inacessível na rede.")
        sock.close()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[info] conexão cancelada pelo usuário.")
        sock.close()
        sys.exit(0)
    except OSError as erro:
        print(f"[erro] falha de rede ao conectar a {ip}:{porta}: {erro}")
        sock.close()
        sys.exit(1)

    sock.settimeout(None)
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

    Etapa 5: acrescentados excepts explícitos para BrokenPipeError e
    ConnectionResetError (antes de OSError) tanto no envio quanto na
    espera da resposta — tecnicamente já caíam no `except OSError`
    genérico (são subclasses), mas com mensagem menos clara para o
    usuário sobre o que de fato aconteceu (conexão caiu, não é um erro
    de rede qualquer). Também acrescentado KeyboardInterrupt: antes,
    Ctrl+C durante o login (digitando o apelido ou esperando resposta)
    subia como traceback cru até o topo do programa.
    """
    buffer = b""

    try:
        while True:
            nome = input("Escolha um apelido: ").strip()
            if not nome:
                print("[aviso] o apelido não pode ser vazio.")
                continue

            try:
                sock.sendall(protocolo.serializar(protocolo.msg_login(nome)))
            except (BrokenPipeError, ConnectionResetError) as erro:
                print(f"[erro] conexão perdida ao enviar login: {erro}")
                sock.close()
                sys.exit(1)
            except OSError as erro:
                print(f"[erro] falha ao enviar login: {erro}")
                sock.close()
                sys.exit(1)

            resposta = None
            while resposta is None:
                try:
                    dados = sock.recv(4096)
                except (BrokenPipeError, ConnectionResetError) as erro:
                    print(f"[erro] conexão perdida durante o login: {erro}")
                    sock.close()
                    sys.exit(1)
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
    except KeyboardInterrupt:
        print("\n[info] login cancelado pelo usuário.")
        try:
            sock.close()
        except OSError:
            pass
        sys.exit(0)


# --------------------------------------------------------------------------
# Etapa 3 — thread de recepção
# --------------------------------------------------------------------------

def _encerrar_conexao_forcado(sock: socket.socket, mensagem: str) -> None:
    """
    Usado pela thread de recepção (etapa 5) quando a conexão cai por um
    motivo que não foi a thread principal pedindo para encerrar (ex:
    servidor caiu, cabo de rede foi desconectado). Nesse momento a
    thread principal muito provavelmente está bloqueada em input(),
    esperando o usuário digitar algo — e input() é uma chamada
    bloqueante que não escuta eventos (threading.Event) nem sockets, só
    o teclado. Não existe forma portátil e simples (sem depender de
    bibliotecas extras) de "acordar" educadamente essa chamada.

    Por isso, em vez de deixar o programa preso esperando o usuário
    apertar Enter para só então perceber que a conexão caiu, avisamos o
    usuário, fechamos o socket corretamente e encerramos o processo
    aqui mesmo. os._exit() (em vez de sys.exit()) é necessário porque
    sys.exit() apenas levanta SystemExit, que uma thread secundária não
    consegue propagar para a thread principal bloqueada em input().
    """
    print(f"\n{mensagem}")
    try:
        sock.close()
    except OSError:
        pass
    os._exit(0)


def thread_recepcao(
    sock: socket.socket,
    buffer_inicial: bytes,
    evento_encerrando: threading.Event,
) -> None:
    """
    Roda em thread separada. Só faz três coisas, nesta ordem, em loop:
    recebe bytes do socket, desserializa via protocolo.extrair_mensagens,
    imprime cada mensagem completa. Nunca lê input() do usuário.

    Encerra quando evento_encerrando é sinalizado (pela thread principal,
    em encerrar()) ou quando a conexão cai por qualquer motivo.

    Etapa 5: distinção importante entre dois casos de recv()/OSError
    falhando:
        1. evento_encerrando JÁ estava setado -> foi a thread principal
           que fechou o socket de propósito (usuário digitou /sair ou
           Ctrl+C, via encerrar()). É o caminho normal de desligamento:
           não há erro real, só terminamos o loop em silêncio.
        2. evento_encerrando NÃO estava setado -> a conexão caiu por
           conta própria (servidor encerrou, cabo caiu, etc.) enquanto
           a sessão estava ativa. Aí sim é um erro de verdade, tratado
           por _encerrar_conexao_forcado() (ver docstring acima).
    Sem essa distinção, um /sair normal do usuário (que fecha o socket
    de propósito) seria erroneamente relatado como "conexão perdida".
    """
    buffer = buffer_inicial

    while not evento_encerrando.is_set():
        try:
            dados = sock.recv(4096)
        except (ConnectionResetError, BrokenPipeError) as erro:
            if evento_encerrando.is_set():
                break
            _encerrar_conexao_forcado(
                sock, f"[erro] conexão perdida com o servidor: {erro}"
            )
        except OSError:
            # Caso mais comum aqui: socket fechado localmente por
            # encerrar() (thread principal) — desligamento esperado.
            break

        if not dados:
            if evento_encerrando.is_set():
                break
            _encerrar_conexao_forcado(
                sock, "[servidor] a conexão foi encerrada pelo servidor."
            )

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
    Retorna True se enviou com sucesso, False se houve falha (nesse caso
    já imprime um erro amigável; quem chama decide o que fazer a
    seguir — ver main(), que agora encerra a sessão quando enviar()
    retorna False, etapa 5).

    Etapa 5: BrokenPipeError e ConnectionResetError tratados antes do
    `except OSError` genérico, para dar ao usuário uma mensagem
    específica de "conexão perdida" em vez de "falha ao enviar
    mensagem" — a causa é diferente (rede caiu vs. outro erro de I/O) e
    vale a pena o usuário saber qual foi.
    """
    try:
        sock.sendall(protocolo.serializar(mensagem))
        return True
    except protocolo.ErroProtocolo as erro:
        # Só ocorreria por engano de programação local (dict fora do
        # formato) — nunca por causa do que o usuário digitou.
        print(f"[erro interno] mensagem malformada não enviada: {erro}")
        return False
    except (BrokenPipeError, ConnectionResetError) as erro:
        print(f"[erro] conexão perdida ao enviar mensagem: {erro}")
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
        "--porta", type=validar_porta, default=5000,
        help="Porta do servidor (padrão: 5000)",
    )
    args = parser.parse_args()

    ip = args.ip.strip()
    if not ip:
        print("[erro] --ip não pode ser vazio.")
        sys.exit(1)

    # sock e thread começam como None: em caso de erro/Ctrl+C bem no
    # início (antes de existirem), o bloco finally abaixo sabe o que
    # ainda precisa (ou não) ser limpo, sem depender de variáveis
    # inexistentes.
    sock: Optional[socket.socket] = None
    thread: Optional[threading.Thread] = None
    evento_encerrando: Optional[threading.Event] = None

    try:
        sock = conectar(ip, args.porta)
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
            if not enviar(sock, mensagem):
                # Etapa 5: antes, uma falha de enviar() era só impressa e
                # o loop continuava tentando digitar/enviar normalmente,
                # mesmo com a conexão já morta. Agora encerramos a sessão
                # de forma limpa assim que um envio falha de verdade.
                print("[info] encerrando devido a falha no envio.")
                break
    except KeyboardInterrupt:
        print("\n[info] encerrando (Ctrl+C)...")
    finally:
        if sock is not None and thread is not None and evento_encerrando is not None:
            # Sessão completa: login concluído, thread de recepção rodando.
            encerrar(sock, evento_encerrando)
            thread.join(timeout=1)
        elif sock is not None:
            # Conectou, mas Ctrl+C interrompeu antes da thread iniciar
            # (durante ou logo após o login) — só o socket precisa fechar.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


if __name__ == "__main__":
    main()