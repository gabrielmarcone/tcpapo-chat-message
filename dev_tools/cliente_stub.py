"""
dev_tools/cliente_stub.py — Cliente simulado para testar servidor.py
manualmente no terminal, sem depender do cliente_app.py real.

Usa protocolo.py de verdade para montar e interpretar mensagens — nunca
constrói ou lê JSON manualmente.

Uso:
    python dev_tools/cliente_stub.py --ip 127.0.0.1 --porta 5000 --nome alice

Roda uma sequência fixa de ações (login, mensagem geral, listagem de
usuários, sair), imprimindo cada resposta recebida do servidor — útil
para uma checagem rápida e manual, sem precisar rodar a suíte de testes
completa nem um cliente_app.py interativo.
"""

import argparse
import socket
import sys

# Permite rodar tanto de dentro de dev_tools/ quanto da raiz do projeto
sys.path.insert(0, ".")
import protocolo


def _imprimir(rotulo: str, mensagem: dict) -> None:
    print(f"[{rotulo}] {mensagem}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cliente-stub para testar servidor.py manualmente")
    parser.add_argument("--ip", required=True, help="IP do servidor")
    parser.add_argument("--porta", type=int, required=True, help="Porta do servidor")
    parser.add_argument("--nome", default="stub", help="Nome de login a usar (padrao: stub)")
    parser.add_argument("--senha", default="stub123", help="Senha de login a usar (padrao: stub123)")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect((args.ip, args.porta))
    print(f"Conectado a {args.ip}:{args.porta}")

    buffer = b""

    def enviar(mensagem: dict) -> None:
        _imprimir("enviando", mensagem)
        sock.sendall(protocolo.serializar(mensagem))

    def receber_uma() -> dict:
        nonlocal buffer
        while True:
            mensagens, buffer = protocolo.extrair_mensagens(buffer)
            if mensagens:
                _imprimir("recebido", mensagens[0])
                return mensagens[0]
            dados = sock.recv(4096)
            if not dados:
                raise ConnectionError("servidor fechou a conexao inesperadamente")
            buffer += dados

    # --- sequência de teste manual ---
    enviar(protocolo.msg_login(args.nome, args.senha))
    resposta = receber_uma()
    if resposta["tipo"] != protocolo.TIPO_LOGIN_OK:
        print("Login falhou, encerrando.")
        sock.close()
        return

    enviar(protocolo.msg_mensagem_geral_enviar("mensagem de teste do cliente-stub"))

    enviar(protocolo.msg_listar_usuarios())
    receber_uma()

    enviar(protocolo.msg_sair())

    dados = sock.recv(4096)
    print("Conexao encerrada pelo servidor." if not dados else f"Dado inesperado apos sair: {dados!r}")

    sock.close()


if __name__ == "__main__":
    main()