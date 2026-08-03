"""
dev_tools/cliente_stub.py — Cliente simulado, para o Dev A testar o
servidor.py isoladamente, sem depender do cliente_app.py real.

Dono: DEV A.

Faz parte do repositório (não é descartável) — é evidência, para o
relatório, de que o servidor foi testado de ponta a ponta antes da
integração real com o cliente do Dev B.

Regra importante: este script usa protocolo.py de verdade (serializar /
extrair_mensagens) para montar e interpretar mensagens — NUNCA constrói ou
lê JSON manualmente. Isso garante que o framing real está sendo exercitado
desde o primeiro teste isolado do servidor, não só na integração final.

--------------------------------------------------------------------------
TODO (Dev A):
--------------------------------------------------------------------------
1. Conectar no servidor real (IP/porta via argumento simples ou constante
   de topo do arquivo — não precisa ser tão rigoroso quanto cliente_app.py
   aqui, é ferramenta de desenvolvimento).
2. Enviar, em sequência (usando protocolo.py), pelo menos:
       - login
       - mensagem_geral
       - mensagem_privada
       - listar_usuarios
       - entrar_sala
       - sair_sala
       - sair
3. Imprimir cada resposta recebida do servidor para inspeção manual durante
   o desenvolvimento das etapas do servidor.py.
4. Considerar aceitar argumentos de linha de comando para rodar múltiplas
   instâncias deste stub ao mesmo tempo (simular vários clientes) — útil
   para testar concorrência e broadcast antes do cliente_app.py existir.
"""

import socket  # noqa: F401

import protocolo  # noqa: F401


def main():
    """TODO (Dev A): implementar conforme os passos acima."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
