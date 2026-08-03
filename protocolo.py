"""
protocolo.py — Protocolo de aplicação do chat (tcpapo-chat-message)

Dono: CONJUNTO (Dev A e Dev B) — feito juntos, uma vez, no início.
Depois de pronto e validado por tests/test_protocolo.py, este arquivo fica
CONGELADO: qualquer alteração exige acordo explícito entre os dois.

Responsabilidade:
    - Definir os tipos de mensagem trocados entre cliente e servidor,
      conforme a tabela da seção 8.3 da Especificação de Arquitetura.
    - Serializar mensagens: dict -> uma linha de texto JSON terminada em "\n".
    - Desserializar mensagens a partir de um buffer de bytes acumulado
      (framing por delimitador de linha): extrair todas as mensagens
      completas presentes no buffer e preservar o restante (mensagem
      parcial) para a próxima chamada.

Usado por:
    - servidor.py e dev_tools/cliente_stub.py (Dev A)
    - cliente_app.py e dev_tools/servidor_stub.py (Dev B)

Todos os quatro pontos acima DEVEM importar e usar as funções deste módulo
— nunca montar ou interpretar JSON manualmente em outro lugar do projeto.
Isso garante que o framing real é exercitado desde o primeiro dia de
desenvolvimento isolado, não só na integração final.

--------------------------------------------------------------------------
TODO (Conjunto) — nesta ordem:
--------------------------------------------------------------------------

1. Constantes de tipo (cliente -> servidor):
       TIPO_LOGIN = "login"
       TIPO_MENSAGEM_GERAL = "mensagem_geral"
       TIPO_MENSAGEM_PRIVADA = "mensagem_privada"
       TIPO_LISTAR_USUARIOS = "listar_usuarios"
       TIPO_ENTRAR_SALA = "entrar_sala"
       TIPO_SAIR_SALA = "sair_sala"
       TIPO_SAIR = "sair"

   Constantes de tipo (servidor -> cliente):
       TIPO_LOGIN_OK = "login_ok"
       TIPO_LOGIN_ERRO = "login_erro"
       TIPO_LISTA_USUARIOS = "lista_usuarios"
       TIPO_NOTIFICACAO = "notificacao"
       TIPO_ERRO = "erro"

   (mensagem_geral e mensagem_privada são reutilizados nos dois sentidos,
   conforme a observação de design da seção 8.3 — o campo que muda é o
   conjunto de campos, não o nome do tipo.)

2. def serializar(mensagem: dict) -> bytes
       Recebe um dict com pelo menos a chave "tipo".
       Retorna json.dumps(mensagem) + "\n", codificado em utf-8.

3. def extrair_mensagens(buffer: bytes) -> tuple[list[dict], bytes]
       Recebe o buffer acumulado (bytes já recebidos via socket.recv, ainda
       não processados).
       Divide por "\n", desserializa cada linha completa com json.loads,
       e retorna (lista_de_mensagens, resto_do_buffer_sem_newline_final).
       Uma linha incompleta (sem "\n" ainda) NUNCA é descartada — ela deve
       voltar no resto_do_buffer para ser completada na próxima chamada.

4. Funções auxiliares de construção (opcional, mas recomendado para reduzir
   erro de digitação de chave em cada chamador):
       def msg_login(nome: str) -> dict
       def msg_login_ok(nome: str) -> dict
       def msg_login_erro(motivo: str) -> dict
       def msg_mensagem_geral(remetente: str | None, texto: str) -> dict
       def msg_mensagem_privada(remetente: str | None, destinatario: str | None, texto: str) -> dict
       def msg_listar_usuarios() -> dict
       def msg_lista_usuarios(usuarios: list[tuple[str, str]]) -> dict
       def msg_entrar_sala(sala: str) -> dict
       def msg_sair_sala() -> dict
       def msg_sair() -> dict
       def msg_notificacao(texto: str) -> dict
       def msg_erro(motivo: str) -> dict
"""

import json  # noqa: F401  (será usado em serializar/extrair_mensagens)

# --- Constantes de tipo de mensagem (seção 8.3) ---
# TODO (Conjunto): preencher conforme o item 1 acima.


# --- Serialização ---
def serializar(mensagem: dict) -> bytes:
    """TODO (Conjunto): implementar conforme o item 2 acima."""
    raise NotImplementedError


# --- Framing / desserialização ---
def extrair_mensagens(buffer: bytes) -> tuple[list[dict], bytes]:
    """TODO (Conjunto): implementar conforme o item 3 acima."""
    raise NotImplementedError


# --- Funções auxiliares de construção de mensagem (opcional, item 4) ---
# TODO (Conjunto): preencher conforme necessidade, à medida que Dev A e
# Dev B forem implementando servidor.py e cliente_app.py.
