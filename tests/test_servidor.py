"""
tests/test_servidor.py — Testes do servidor (modelos.py e servidor.py)

Dono: DEV A. Não editado por outra pessoa.

--------------------------------------------------------------------------
TODO (Dev A) — casos mínimos a cobrir, na ordem em que as etapas de
servidor.py forem sendo implementadas:
--------------------------------------------------------------------------

1. test_registro_sob_concorrencia
       Várias threads chamando RegistroClientes.adicionar()/remover() ao
       mesmo tempo não corrompem o dicionário nem lançam exceção
       inesperada (ex: adicionar 20 clientes com nomes distintos a partir
       de 20 threads simultâneas, e conferir que listar_todos() retorna
       exatamente 20 depois).

2. test_login_nome_duplicado_retorna_login_erro
       Duas tentativas de adicionar o mesmo nome: a segunda deve falhar
       (RegistroClientes.adicionar retorna False), e o servidor deve
       responder login_erro sem remover o cliente já existente.

3. test_broadcast_restrito_a_sala_do_remetente
       Clientes em salas diferentes: uma mensagem_geral enviada por um
       cliente da sala "geral" não deve chegar a um cliente que está em
       outra sala, e vice-versa.

4. test_mensagem_privada_independe_de_sala
       Dois clientes em salas diferentes ainda conseguem trocar mensagem
       privada entre si.

5. test_entrar_sala_e_sair_sala_usam_mesmo_mecanismo
       Verificar que sair_sala produz o mesmo estado (sala_atual ==
       "geral" e mesmas notificações) que entrar_sala("geral") produziria
       — evidenciando que não há caminho de código duplicado.

6. test_listagem_mostra_todos_os_usuarios_com_sala_atual
       Clientes em salas diferentes: listar_usuarios deve retornar TODOS,
       cada um com sua sala correta — não só os da sala do solicitante.

7. test_remocao_em_saida_limpa
       Cliente envia "sair": deve ser removido do RegistroClientes, o
       socket deve ser fechado, e os demais devem receber notificacao.

8. test_remocao_em_desconexao_abrupta
       Simular recv() retornando vazio (ou o socket sendo fechado do
       outro lado sem aviso): o cliente deve ser removido pelo mesmo
       caminho do item 7.

9. test_falha_de_envio_isolada_nao_interrompe_broadcast
       Um destinatário cujo send() falha (simular socket quebrado) não
       deve impedir que os demais destinatários do broadcast recebam a
       mensagem.
"""

import modelos  # noqa: F401
import protocolo  # noqa: F401


def test_registro_sob_concorrencia():
    raise NotImplementedError


def test_login_nome_duplicado_retorna_login_erro():
    raise NotImplementedError


def test_broadcast_restrito_a_sala_do_remetente():
    raise NotImplementedError


def test_mensagem_privada_independe_de_sala():
    raise NotImplementedError


def test_entrar_sala_e_sair_sala_usam_mesmo_mecanismo():
    raise NotImplementedError


def test_listagem_mostra_todos_os_usuarios_com_sala_atual():
    raise NotImplementedError


def test_remocao_em_saida_limpa():
    raise NotImplementedError


def test_remocao_em_desconexao_abrupta():
    raise NotImplementedError


def test_falha_de_envio_isolada_nao_interrompe_broadcast():
    raise NotImplementedError
