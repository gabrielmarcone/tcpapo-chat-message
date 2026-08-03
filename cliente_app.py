"""
cliente_app.py — Cliente de chat (tcpapo-chat-message)

Dono: Desenvolvedor B (ver Plano de Divisão de Trabalho, seção 9).
NÃO editado por outra pessoa sem revisão via Pull Request.

Escopo implementado neste arquivo (etapas 1 a 3 da tabela da seção 9):

    Etapa 1 — Leitura de IP/porta via linha de comando (IP obrigatório,
              sem valor padrão; porta com valor padrão) + conexão TCP,
              com tratamento organizado de erro de conexão.
    Etapa 2 — Login: envia 'login', trata 'login_ok'/'login_erro',
              permitindo nova tentativa de nome em caso de erro.
    Etapa 3 — Concorrência: thread dedicada exclusivamente à recepção
              (recv + desserializa + imprime); a thread principal só lê
              input() e envia.

NÃO implementado ainda (fica para as próximas etapas do Dev B):
    - parsing de comandos (/priv, /lista, /entrar, /sair_sala, /sair);
    - mensagens privadas, listagem de usuários, salas;
    - tests/test_cliente.py.
Por enquanto, todo texto digitado pelo usuário é enviado como mensagem
geral (broadcast na sala atual), usando exclusivamente
protocolo.msg_mensagem_geral_enviar().
"""

import argparse
import socket
import sys
import threading
from typing import Tuple

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
    else:
        # Tipo não tratado ainda nesta etapa (ex: lista_usuarios, que só
        # fará sentido quando /lista for implementado). Não inventamos
        # formatação para campos que não conhecemos — só exibimos bruto.
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
            break

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
# Encerramento
# --------------------------------------------------------------------------

def encerrar(sock: socket.socket, evento_encerrando: threading.Event) -> None:
    """
    Fecha a conexão de forma organizada.

    Nesta etapa não enviamos protocolo.msg_sair() aqui de propósito: o
    comando /sair pertence ao parser de comandos (próxima etapa do Dev
    B). O servidor já trata desconexão abrupta como caminho normal (ver
    plano, tarefa 11 do Dev A), então simplesmente fechar o socket é
    suficiente e correto para o escopo atual.
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
          f"para enviar ao chat geral. Ctrl+C para sair.")

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
            if not texto.strip():
                continue

            enviar(sock, protocolo.msg_mensagem_geral_enviar(texto))
    except KeyboardInterrupt:
        print("\n[info] encerrando (Ctrl+C)...")
    finally:
        encerrar(sock, evento_encerrando)
        thread.join(timeout=1)


if __name__ == "__main__":
    main()