"""
dev_tools/servidor_stub.py — Servidor simulado, para o Dev B testar o
cliente_app.py isoladamente, sem depender do servidor.py real.

Dono: DEV B.

Faz parte do repositório (não é descartável) — é evidência, para o
relatório, de que o cliente foi testado de ponta a ponta antes da
integração real com o servidor do Dev A.

Regra importante: este script usa protocolo.py de verdade (serializar /
extrair_mensagens) para montar e interpretar mensagens — NUNCA constrói ou
lê JSON manualmente. Isso garante que o framing real está sendo exercitado
desde o primeiro teste isolado do cliente, não só na integração final.

--------------------------------------------------------------------------
TODO (Dev B):
--------------------------------------------------------------------------
1. Aceitar uma única conexão (bind simples em 0.0.0.0, porta fixa de
   desenvolvimento ou por argumento).
2. Ler a mensagem de login do cliente e responder login_ok (fixo, sempre
   aceitar, já que o objetivo aqui é exercitar o cliente_app.py, não testar
   lógica de servidor).
3. Ao receber cada tipo de mensagem do cliente, responder com uma mensagem
   fixa e plausível do protocolo, para exercitar cada comportamento do
   cliente_app.py sem depender do servidor real:
       - mensagem_geral recebida -> responder com uma notificacao ou eco
       - listar_usuarios recebida -> responder lista_usuarios fixa
       - entrar_sala recebida -> responder notificacao de entrada
       - etc.
4. Considerar simular também uma queda de conexão (fechar o socket
   abruptamente) para testar a etapa 5 do cliente_app.py (tratamento de
   erro de conexão perdida durante a sessão).
"""

import socket  # noqa: F401

import protocolo  # noqa: F401


def main():
    """TODO (Dev B): implementar conforme os passos acima."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
