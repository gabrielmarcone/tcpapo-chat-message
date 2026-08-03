"""
tests/test_protocolo.py — Testes do módulo protocolo.py

Dono: CONJUNTO (Dev A e Dev B, mantido pelos dois).

Este é o teste mais importante do projeto: valida o framing por
delimitador de linha, que é a base de tudo o resto (servidor, cliente,
stubs). Deve ser escrito e validado ANTES de servidor.py e cliente_app.py
avançarem além do esqueleto.

--------------------------------------------------------------------------
TODO (Conjunto) — casos mínimos a cobrir:
--------------------------------------------------------------------------

1. test_serializar_mensagem_simples
       Uma mensagem dict simples (ex: login) serializa para bytes
       terminados em b"\n", e o conteúdo é um JSON válido.

2. test_extrair_uma_mensagem_completa
       Um buffer contendo exatamente uma mensagem completa (com o \n)
       retorna essa mensagem na lista e buffer restante vazio.

3. test_extrair_duas_mensagens_grudadas_num_unico_buffer
       Simula o caso real de dois recv() concatenados: um buffer com DUAS
       mensagens completas (cada uma terminada em \n) deve retornar as
       duas na lista, na ordem correta, com buffer restante vazio.

4. test_extrair_mensagem_cortada_ao_meio_entre_duas_chamadas
       Um buffer contendo uma mensagem completa + o início de uma segunda
       mensagem (sem o \n final) deve retornar só a primeira mensagem na
       lista, e o início da segunda deve voltar no buffer restante —
       NUNCA deve ser descartado nem quebrar o parsing.

5. test_texto_com_quebra_de_linha_nao_quebra_framing
       Uma mensagem cujo campo de texto contém "\n" literal (ex: usuário
       colou um texto multilinha) deve, ao ser serializada, conter APENAS
       UM "\n" real na linha (o delimitador final) — o \n do texto do
       usuário deve aparecer escapado dentro do JSON (\\n). Ao extrair de
       volta, o texto original (com a quebra de linha) deve ser
       recuperado integralmente.
       -> Este teste substitui a etapa de "sanitização manual de \n"
          do plano original: se ele passar, sanitização adicional é
          desnecessária (ver seção 5 do plano de divisão de trabalho).

6. (Opcional, recomendado) test_extrair_buffer_vazio_nao_quebra
       Um buffer vazio (b"") retorna lista vazia e buffer restante vazio,
       sem lançar exceção — importante porque o loop de leitura do
       servidor/cliente vai chamar extrair_mensagens repetidamente, mesmo
       quando ainda não há mensagem completa.
"""

import protocolo  # noqa: F401


def test_serializar_mensagem_simples():
    raise NotImplementedError


def test_extrair_uma_mensagem_completa():
    raise NotImplementedError


def test_extrair_duas_mensagens_grudadas_num_unico_buffer():
    raise NotImplementedError


def test_extrair_mensagem_cortada_ao_meio_entre_duas_chamadas():
    raise NotImplementedError


def test_texto_com_quebra_de_linha_nao_quebra_framing():
    raise NotImplementedError


def test_extrair_buffer_vazio_nao_quebra():
    raise NotImplementedError
