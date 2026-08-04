"""
dev_tools/servidor_stub.py — Servidor simulado, para o Dev B testar o
cliente_app.py isoladamente, sem depender do servidor.py real.

Dono: DEV B.

Faz parte do repositório (não é descartável) — é evidência, para o
relatório, de que o cliente foi testado de ponta a ponta antes da
integração real com o servidor do Dev A.

Regra importante (seção 6 do Plano de Divisão): este script usa
protocolo.py de verdade (serializar / extrair_mensagens) para montar e
interpretar mensagens — NUNCA constrói ou lê JSON manualmente. Isso
garante que o framing real está sendo exercitado desde o primeiro teste
isolado do cliente, não só na integração final com o servidor do Dev A.

--------------------------------------------------------------------------
Comportamento implementado (os 4 passos do TODO original):
--------------------------------------------------------------------------
1. Aceita uma única conexão, bind em 0.0.0.0 (para funcionar também se
   testado a partir de outra máquina do laboratório) na porta passada
   por --porta (padrão: 5000).
2. Lê a mensagem de login do cliente e responde login_ok, sempre aceitando
   — o objetivo aqui é exercitar o cliente_app.py, não testar lógica de
   autenticação (isso é responsabilidade do servidor.py real, do Dev A).
3. Para cada tipo de mensagem que o cliente pode enviar depois do login,
   responde com uma mensagem fixa e plausível do protocolo, para
   exercitar cada comportamento de cliente_app.py sem depender do
   servidor real:
       - mensagem_geral   -> ecoa de volta como mensagem_geral (repassar),
                             simulando o broadcast que o servidor real
                             faria de volta para a sala.
       - mensagem_privada -> simula o destinatário respondendo com um
                             eco, para exercitar o caminho de "mensagem
                             privada recebida" sem precisar de um segundo
                             cliente real conectado.
       - listar_usuarios  -> responde lista_usuarios fixa (o próprio
                             usuário logado + dois usuários inventados).
       - entrar_sala      -> responde notificacao confirmando a entrada.
       - sair_sala        -> responde notificacao confirmando a volta
                             para a sala geral.
       - sair             -> encerra a conexão do lado do servidor (o
                             cliente real, nesta etapa, não chega a
                             enviar isso — ver encerrar() em
                             cliente_app.py — mas o stub trata mesmo
                             assim, por completude do contrato).
       - qualquer outro tipo -> responde com uma mensagem de erro
                             (protocolo.msg_erro), já que não deveria
                             acontecer dado o contrato fechado.
4. Simulação de queda de conexão (para testar a etapa 5 do
   cliente_app.py): se o texto de uma mensagem_geral for exatamente
   "!crash", o stub fecha o socket abruptamente, SEM responder — assim
   o cliente recebe um recv() vazio (ou ConnectionResetError,
   dependendo do sistema operacional) no meio da sessão, exatamente o
   cenário que thread_recepcao()/_encerrar_conexao_forcado() tratam.

Uso:
    python dev_tools/servidor_stub.py --porta 5000

Depois, em outro terminal:
    python cliente_app.py --ip 127.0.0.1 --porta 5000
"""

import argparse
import os
import socket
import sys

# Este script fica em dev_tools/, mas protocolo.py está na raiz do
# repositório. Sem isso, 'python dev_tools/servidor_stub.py' (execução
# direta, como documentado no uso abaixo) falharia com
# ModuleNotFoundError, já que o Python só coloca automaticamente no
# sys.path o diretório do próprio script (dev_tools/), não a raiz do
# projeto.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocolo

# Texto especial reconhecido só por este stub (não faz parte do
# protocolo real) para o Dev B forçar uma queda de conexão em qualquer
# momento da sessão, e assim testar manualmente os caminhos de erro da
# etapa 5 de cliente_app.py sem precisar desligar o processo do stub.
GATILHO_CRASH = "!crash"


def _receber_mensagem(conexao: socket.socket, buffer: bytes) -> tuple:
    """
    Bloco de leitura repetido em alguns pontos deste script: recebe bytes
    até ter ao menos uma mensagem completa (via protocolo.extrair_mensagens)
    ou a conexão cair. Retorna (mensagem_ou_None, buffer_restante).

    mensagem_ou_None é None quando a conexão foi encerrada do lado do
    cliente (recv retornou vazio) — quem chama decide o que fazer.
    """
    mensagens = []
    while not mensagens:
        dados = conexao.recv(4096)
        if not dados:
            return None, buffer
        buffer += dados
        try:
            mensagens, buffer = protocolo.extrair_mensagens(buffer)
        except protocolo.ErroProtocolo as erro:
            # Decisão deste stub (documentada no docstring de
            # extrair_mensagens): logar e ignorar a linha malformada,
            # sem derrubar a conexão de teste por causa disso.
            print(f"[stub] mensagem malformada ignorada: {erro}")
            buffer = erro.buffer_restante
    return mensagens[0], buffer


def _enviar(conexao: socket.socket, mensagem: dict) -> bool:
    """Serializa e envia uma mensagem via protocolo.py. Retorna False (em
    vez de propagar a exceção) se a conexão já tiver caído, para o loop
    principal poder encerrar a sessão sem traceback."""
    try:
        conexao.sendall(protocolo.serializar(mensagem))
        return True
    except OSError as erro:
        print(f"[stub] falha ao enviar resposta: {erro}")
        return False


def _realizar_login(conexao: socket.socket) -> tuple:
    """
    Passo 2 do TODO: lê a primeira mensagem (deve ser 'login') e responde
    login_ok, sempre aceitando o nome recebido.

    Retorna (nome, buffer_restante), ou (None, b"") se a conexão caiu
    antes do login completar.
    """
    msg, buffer = _receber_mensagem(conexao, b"")
    if msg is None:
        print("[stub] conexão encerrada pelo cliente antes do login.")
        return None, b""

    nome = msg.get("nome", "desconhecido") if msg.get("tipo") == protocolo.TIPO_LOGIN else "desconhecido"
    print(f"[stub] login recebido: '{nome}' -> respondendo login_ok (fixo)")
    _enviar(conexao, protocolo.msg_login_ok(nome))
    return nome, buffer


def _tratar_mensagem(conexao: socket.socket, nome: str, msg: dict) -> bool:
    """
    Passo 3 (+ passo 4) do TODO: para cada tipo de mensagem pós-login,
    monta e envia a resposta fixa correspondente.

    Retorna False quando a sessão deve terminar do lado do stub (recebeu
    'sair', ou o gatilho de crash foi acionado) — nesse caso quem chama
    encerra o loop. Retorna True para seguir recebendo mensagens.
    """
    tipo = msg.get("tipo")

    if tipo == protocolo.TIPO_MENSAGEM_GERAL:
        texto = msg.get("texto", "")
        if texto.strip() == GATILHO_CRASH:
            print(f"[stub] gatilho '{GATILHO_CRASH}' recebido — simulando "
                  f"queda de conexão (fechando o socket sem responder).")
            return False
        print(f"[stub] mensagem_geral de '{nome}': {texto!r} -> ecoando (repassar)")
        _enviar(conexao, protocolo.msg_mensagem_geral_repassar(nome, texto))

    elif tipo == protocolo.TIPO_MENSAGEM_PRIVADA:
        destinatario = msg.get("destinatario", "?")
        texto = msg.get("texto", "")
        print(f"[stub] mensagem_privada de '{nome}' para '{destinatario}': "
              f"{texto!r} -> simulando resposta de '{destinatario}'")
        _enviar(
            conexao,
            protocolo.msg_mensagem_privada_repassar(
                destinatario, f"[eco automático do stub] {texto}"
            ),
        )

    elif tipo == protocolo.TIPO_LISTAR_USUARIOS:
        print("[stub] listar_usuarios recebido -> respondendo lista fixa")
        _enviar(
            conexao,
            protocolo.msg_lista_usuarios(
                [(nome, "geral"), ("usuario_fake_1", "geral"), ("usuario_fake_2", "jogos")]
            ),
        )

    elif tipo == protocolo.TIPO_ENTRAR_SALA:
        sala = msg.get("sala", "?")
        print(f"[stub] entrar_sala recebido: '{sala}' -> notificando entrada")
        _enviar(conexao, protocolo.msg_notificacao(f"você entrou na sala '{sala}'."))

    elif tipo == protocolo.TIPO_SAIR_SALA:
        print("[stub] sair_sala recebido -> notificando volta à sala geral")
        _enviar(conexao, protocolo.msg_notificacao("você voltou para a sala geral."))

    elif tipo == protocolo.TIPO_SAIR:
        print("[stub] sair recebido -> encerrando a sessão do lado do stub")
        return False

    else:
        print(f"[stub] tipo não tratado pelo stub: '{tipo}' -> respondendo erro")
        _enviar(conexao, protocolo.msg_erro(f"tipo '{tipo}' não é tratado por este stub de teste."))

    return True


def atender_cliente(conexao: socket.socket, endereco) -> None:
    """
    Conduz uma sessão inteira com um único cliente: login, depois loop de
    mensagens até a conexão cair, o cliente enviar 'sair', ou o gatilho
    de crash (passo 4 do TODO) ser usado.
    """
    print(f"[stub] cliente conectado: {endereco}")
    try:
        nome, buffer = _realizar_login(conexao)
        if nome is None:
            return

        continuar = True
        while continuar:
            msg, buffer = _receber_mensagem(conexao, buffer)
            if msg is None:
                print(f"[stub] '{nome}' encerrou a conexão.")
                break
            continuar = _tratar_mensagem(conexao, nome, msg)
    except OSError as erro:
        print(f"[stub] conexão com '{endereco}' caiu: {erro}")
    finally:
        conexao.close()
        print(f"[stub] conexão com {endereco} fechada.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Servidor simulado (stub) para o Dev B testar cliente_app.py isoladamente."
    )
    parser.add_argument(
        "--porta", type=int, default=5000,
        help="Porta em que o stub vai escutar (padrão: 5000)",
    )
    args = parser.parse_args()

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        servidor.bind(("0.0.0.0", args.porta))
    except OSError as erro:
        print(f"[erro] não foi possível escutar na porta {args.porta}: {erro}")
        servidor.close()
        sys.exit(1)

    servidor.listen(1)  # passo 1 do TODO: uma única conexão por vez, suficiente para teste manual
    print(f"[stub] escutando em 0.0.0.0:{args.porta} (Ctrl+C para encerrar)")
    print(f"[stub] dica: envie '{GATILHO_CRASH}' como mensagem geral para simular queda de conexão.")

    try:
        while True:
            conexao, endereco = servidor.accept()
            atender_cliente(conexao, endereco)
            print("[stub] pronto para a próxima conexão de teste.\n")
    except KeyboardInterrupt:
        print("\n[stub] encerrando (Ctrl+C)...")
    finally:
        servidor.close()


if __name__ == "__main__":
    main()