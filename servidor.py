"""
servidor.py — Servidor do chat (tcpapo-chat-message)

Dono: DEV A. Não editado por outra pessoa.

Responsabilidade:
    Thread principal em loop de accept() na porta configurada, escutando em
    0.0.0.0 (todas as interfaces). Para cada conexão aceita, dispara uma
    thread dedicada que processa o login e depois roteia as mensagens
    daquele cliente (geral, privada, salas, listagem) até a desconexão
    (limpa ou abrupta).

Referência: seções 2, 3, 5, 6, 7, 8 e 9 da Especificação de Arquitetura.

Uso:
    python servidor.py [--porta PORTA]

--------------------------------------------------------------------------
Estado desta implementação (etapas do plano de divisão de trabalho):
--------------------------------------------------------------------------

IMPLEMENTADO:
    1-2. Leitura de porta via argparse + loop de accept em 0.0.0.0.
    3.   Loop de leitura por cliente (buffer + protocolo.extrair_mensagens).
    4.   Login (nome único, login_ok/login_erro, conexão permanece aberta
         em caso de erro).
    5.   Mensagem geral / broadcast restrito à sala do remetente.
    9-11 (adiantadas): a estrutura try/finally do loop de leitura já
         garante remoção única do cliente (seção 9) tanto na saída limpa
         (comando 'sair') quanto na desconexão abrupta (recv retorna
         vazio, ou qualquer OSError) — não fazia sentido escrever um loop
         de leitura "incompleto" que ignorasse esses casos, então eles
         saíram prontos junto com o loop desde o início, em vez de
         ficarem para depois. O broadcast (_broadcast_sala) já isola
         falha de envio por destinatário (etapa 11/12).

    >>> CHECKPOINT DE INTEGRAÇÃO ANTECIPADO <<<
    Login + mensagem_geral estão prontos. A partir daqui, rodar
    cliente_app.py real (Dev B, etapa 3) contra este servidor, antes de
    prosseguir para as etapas abaixo.

AINDA NÃO IMPLEMENTADO (respondem com tipo 'erro', "ainda não
implementado nesta etapa" — placeholder explícito, não falha silenciosa):
    6. Mensagem privada.
    7. Salas (entrar_sala / sair_sala).
    8. Listagem de usuários (listar_usuarios).

Decisão de design tomada aqui (vale registrar no relatório): o remetente
de uma mensagem_geral NÃO recebe de volta a própria mensagem no
broadcast — ele já vê o que digitou no próprio terminal (input()), então
ecoar de volta seria duplicado. Só os DEMAIS membros da sala recebem.
"""

import argparse
import socket
import sys
import threading
from typing import Optional

import protocolo
from modelos import Cliente, RegistroClientes

HOST_PADRAO = "0.0.0.0"
PORTA_PADRAO = 5000
TAMANHO_BUFFER_RECV = 4096

# Timeout do accept() do socket de escuta — não tem relação com timeout de
# leitura de clientes conectados (esses continuam bloqueantes, sem prazo).
# Existe só para o loop principal "acordar" periodicamente e checar se deve
# parar, em vez de ficar bloqueado indefinidamente em accept(). Sem isso,
# Ctrl+C podia demorar muito para ser percebido (sobretudo no Windows, onde
# um KeyboardInterrupt só é entregue de forma confiável quando o processo
# está executando bytecode Python, não quando está preso numa chamada
# bloqueante de baixo nível) — o servidor só "acordava" quando uma conexão
# nova chegava e liberava o accept().
TIMEOUT_ACCEPT_SEGUNDOS = 0.5

# Tipos de mensagem cujo roteamento ainda não foi implementado nesta etapa
# (etapas 6-8 do plano) — respondem com erro explícito em vez de serem
# ignorados silenciosamente ou derrubarem a conexão.
_TIPOS_AINDA_NAO_IMPLEMENTADOS = frozenset({
    protocolo.TIPO_MENSAGEM_PRIVADA,
    protocolo.TIPO_ENTRAR_SALA,
    protocolo.TIPO_SAIR_SALA,
    protocolo.TIPO_LISTAR_USUARIOS,
})


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------

def _enviar(sock: socket.socket, mensagem: dict) -> None:
    sock.sendall(protocolo.serializar(mensagem))


def _enviar_erro_seguro(sock: socket.socket, motivo: str) -> None:
    """
    Tenta avisar o cliente de um erro sem propagar exceção — se o envio
    falhar, o socket provavelmente já está quebrado por outro motivo, e
    quem chama vai descobrir isso do jeito normal (recv retornando vazio
    ou lançando OSError) no próximo ciclo do loop.
    """
    try:
        _enviar(sock, protocolo.msg_erro(motivo))
    except OSError:
        pass


def _extrair_mensagens_tolerante(buffer: bytes, sock_cliente: socket.socket) -> tuple:
    """
    Chama protocolo.extrair_mensagens em loop, recuperando as mensagens
    válidas mesmo quando uma linha no meio do lote é malformada.

    Sem isso, uma única linha ruim faria a MESMA linha ser reprocessada
    (e falhar) para sempre a cada novo dado recebido, travando a conexão
    num loop infinito de erro — bug real encontrado via teste de
    integração (ver ErroProtocolo em protocolo.py). Cada linha malformada
    gera um aviso 'erro' ao cliente; as mensagens válidas antes e depois
    dela no mesmo lote são preservadas e processadas normalmente.
    """
    todas_mensagens = []
    resto = buffer
    while True:
        try:
            novas, resto = protocolo.extrair_mensagens(resto)
            todas_mensagens.extend(novas)
            return todas_mensagens, resto
        except protocolo.ErroProtocolo as erro:
            todas_mensagens.extend(erro.mensagens_processadas)
            resto = erro.buffer_restante
            _enviar_erro_seguro(sock_cliente, str(erro))


def _broadcast_sala(
    registro: RegistroClientes,
    sala: str,
    mensagem: dict,
    excluir_nome: Optional[str] = None,
) -> None:
    """
    Envia `mensagem` a todos os clientes atualmente na sala `sala`.

    Segue a regra da seção 3 da Especificação: RegistroClientes.
    listar_por_sala já devolve uma cópia com o lock liberado — o lock do
    registro nunca fica retido durante o envio de rede abaixo.

    Uma falha de envio para um destinatário específico é isolada (etapa
    11/12): não interrompe o envio aos demais. O cliente cujo envio
    falhou será removido pela SUA PRÓPRIA thread na próxima vez que o
    recv/send dela falhar — não removemos por aqui (ponto único de
    remoção, seção 9 da Especificação).
    """
    destinatarios = registro.listar_por_sala(sala)
    linha = protocolo.serializar(mensagem)
    for cliente in destinatarios:
        if excluir_nome is not None and cliente.nome == excluir_nome:
            continue
        try:
            cliente.socket.sendall(linha)
        except OSError:
            continue  # falha isolada — ver docstring acima


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def _processar_login(
    sock_cliente: socket.socket,
    endereco,
    registro: RegistroClientes,
    buffer: bytes,
) -> tuple:
    """
    Loop de login: espera a primeira mensagem válida de tipo 'login' e
    tenta registrar o nome. Se o nome já estiver em uso (ou for
    inválido), responde login_erro/erro e CONTINUA esperando — permite
    nova tentativa sem reconectar (Especificação, seção 5).

    Qualquer outro tipo de mensagem recebido antes do login é rejeitado
    com erro, mas não derruba a conexão — o cliente pode tentar de novo.

    Retorna (cliente, buffer_restante). `cliente` é None se a conexão foi
    encerrada (recv vazio) antes de um login bem-sucedido.
    """
    while True:
        dados = sock_cliente.recv(TAMANHO_BUFFER_RECV)
        if not dados:
            return None, buffer  # desconectou antes de logar

        buffer += dados
        mensagens, buffer = _extrair_mensagens_tolerante(buffer, sock_cliente)

        for msg in mensagens:
            if msg["tipo"] != protocolo.TIPO_LOGIN:
                _enviar_erro_seguro(sock_cliente, "primeira mensagem deve ser 'login'")
                continue

            nome = msg.get("nome")
            if not isinstance(nome, str) or not nome.strip():
                _enviar_erro_seguro(sock_cliente, "campo 'nome' invalido")
                continue

            cliente = Cliente(nome=nome, sock=sock_cliente, endereco=endereco)
            if registro.adicionar(cliente):
                _enviar(sock_cliente, protocolo.msg_login_ok(nome))
                return cliente, buffer

            _enviar(sock_cliente, protocolo.msg_login_erro("nome ja em uso"))
            # conexão continua aberta — cliente pode tentar outro nome


# --------------------------------------------------------------------------
# Roteamento de mensagens já autenticadas
# --------------------------------------------------------------------------

def _rotear_mensagem(registro: RegistroClientes, cliente: Cliente, msg: dict) -> bool:
    """
    Processa uma mensagem de um cliente já autenticado.
    Retorna True se o loop de leitura deve continuar, False se deve
    encerrar (comando 'sair').
    """
    tipo = msg["tipo"]

    if tipo == protocolo.TIPO_SAIR:
        return False

    if tipo == protocolo.TIPO_MENSAGEM_GERAL:
        texto = msg.get("texto", "")
        mensagem_repasse = protocolo.msg_mensagem_geral_repassar(cliente.nome, texto)
        _broadcast_sala(registro, cliente.sala_atual, mensagem_repasse, excluir_nome=cliente.nome)
        return True

    if tipo in _TIPOS_AINDA_NAO_IMPLEMENTADOS:
        _enviar_erro_seguro(
            cliente.socket,
            f"recurso '{tipo}' ainda nao implementado nesta etapa do desenvolvimento",
        )
        return True

    _enviar_erro_seguro(cliente.socket, f"tipo de mensagem desconhecido: {tipo!r}")
    return True


# --------------------------------------------------------------------------
# Ciclo de vida de uma conexão
# --------------------------------------------------------------------------

def tratar_cliente(sock_cliente: socket.socket, endereco, registro: RegistroClientes) -> None:
    """
    Ciclo de vida completo de uma conexão: login, notificação de entrada,
    loop de roteamento, e remoção garantida (saída limpa ou abrupta) via
    try/finally.

    Ponto único de remoção (seção 9 da Especificação): só esta thread
    remove este cliente do registro, e só neste bloco finally — nunca em
    outro lugar do código.
    """
    buffer = b""
    cliente: Optional[Cliente] = None

    try:
        cliente, buffer = _processar_login(sock_cliente, endereco, registro, buffer)
        if cliente is None:
            return  # desconectou antes de completar o login

        _broadcast_sala(
            registro,
            cliente.sala_atual,
            protocolo.msg_notificacao(f"{cliente.nome} entrou no chat"),
            excluir_nome=cliente.nome,
        )

        while True:
            dados = sock_cliente.recv(TAMANHO_BUFFER_RECV)
            if not dados:
                break  # desconexão abrupta

            buffer += dados
            mensagens, buffer = _extrair_mensagens_tolerante(buffer, sock_cliente)

            continuar = True
            for msg in mensagens:
                continuar = _rotear_mensagem(registro, cliente, msg)
                if not continuar:
                    break
            if not continuar:
                break

    except OSError:
        pass  # conexão quebrou de forma inesperada — tratado no finally abaixo

    finally:
        if cliente is not None:
            sala_no_momento_da_saida = cliente.sala_atual
            registro.remover(cliente.nome)
            try:
                sock_cliente.close()
            except OSError:
                pass
            _broadcast_sala(
                registro,
                sala_no_momento_da_saida,
                protocolo.msg_notificacao(f"{cliente.nome} saiu do chat"),
            )
        else:
            try:
                sock_cliente.close()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Bootstrap do servidor
# --------------------------------------------------------------------------

def criar_socket_servidor(host: str, porta: int) -> socket.socket:
    """
    Cria, faz bind e coloca em modo de escuta o socket do servidor.
    Separado de main() para permitir testes de integração reais: os
    testes chamam isso com porta=0 (o SO escolhe uma porta livre) e
    inspecionam sock.getsockname()[1] para saber qual foi escolhida.

    O timeout de accept() (TIMEOUT_ACCEPT_SEGUNDOS) é configurado aqui,
    não nos sockets de cliente aceitos depois — o Python já garante isso
    automaticamente: um socket retornado por accept() nasce em modo
    bloqueante (sem timeout) mesmo que o socket de escuta tenha um
    configurado, então tratar_cliente() continua com recv() bloqueante
    normalmente, sem nenhuma mudança de comportamento ali.

    A opção de reuso de endereço é escolhida por plataforma, de propósito
    (bug real encontrado em teste manual no Windows): SO_REUSEADDR no
    Linux/Mac só permite reaproveitar uma porta presa em TIME_WAIT depois
    de uma conexão fechada — nunca deixa dois processos escutarem a
    mesma porta ativa ao mesmo tempo (confirmado em teste: o segundo
    bind falha com "Address already in use", como esperado). No Windows,
    porém, SO_REUSEADDR tem uma semântica bem mais permissiva: ele
    permite que um SEGUNDO processo faça bind na MESMA porta que já está
    sendo escutada por outro, sem erro nenhum — os dois "servidores"
    coexistem silenciosamente, cada conexão nova indo para um ou outro
    de forma imprevisível, sem nenhum aviso. É uma peculiaridade
    documentada do WinSock (às vezes chamada de "port hijacking"), bem
    diferente do Linux. SO_EXCLUSIVEADDRUSE é a opção correta no Windows
    para esse caso: garante exclusividade de verdade (o segundo bind
    falha, como deveria), e ainda permite reiniciar rápido o servidor
    depois que o processo anterior fechou a porta de fato.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind((host, porta))
    sock.listen()
    sock.settimeout(TIMEOUT_ACCEPT_SEGUNDOS)
    return sock


def loop_accept(socket_servidor: socket.socket, registro: RegistroClientes) -> None:
    """
    Loop principal: aceita conexões e dispara uma thread dedicada para
    cada uma. Nunca bloqueia por mais que TIMEOUT_ACCEPT_SEGUNDOS de cada
    vez — o timeout do socket de escuta faz accept() retornar
    periodicamente mesmo sem conexão nenhuma, para que Ctrl+C (ou outro
    sinal de encerramento) seja percebido rápido, em vez de só quando uma
    conexão nova chegar. Retorna quando socket_servidor é fechado por
    fora (ex: encerramento do servidor).
    """
    while True:
        try:
            sock_cliente, endereco = socket_servidor.accept()
        except socket.timeout:
            continue  # só um "despertar" periódico — nada de errado aconteceu
        except OSError:
            return  # socket_servidor foi fechado — encerramento normal

        print(f"Nova conexao de {endereco}")
        thread = threading.Thread(
            target=tratar_cliente,
            args=(sock_cliente, endereco, registro),
            daemon=True,
        )
        thread.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor do chat tcpapo-chat-message")
    parser.add_argument(
        "--porta",
        type=int,
        default=PORTA_PADRAO,
        help=f"Porta de escuta (padrao: {PORTA_PADRAO})",
    )
    args = parser.parse_args()

    registro = RegistroClientes()

    try:
        socket_servidor = criar_socket_servidor(HOST_PADRAO, args.porta)
    except OSError as erro:
        # Caso mais comum aqui: porta já em uso (outro servidor.py já
        # rodando nela, ou outro processo qualquer). Sem este tratamento,
        # o usuário via um traceback cru — inconsistente com o padrão de
        # mensagens amigáveis já usado em cliente_app.py para erros
        # equivalentes do lado do cliente.
        print(f"[erro] não foi possível iniciar o servidor na porta {args.porta}: {erro}")
        print(
            "[dica] a porta provavelmente já está em uso (outro servidor.py "
            "já rodando nela?) — tente outra porta com --porta, ou encerre "
            "o processo que já está usando essa."
        )
        sys.exit(1)

    print(f"Servidor escutando em {HOST_PADRAO}:{args.porta} (Ctrl+C para encerrar)")

    try:
        loop_accept(socket_servidor, registro)
    except KeyboardInterrupt:
        print("\nEncerrando servidor...")
    finally:
        socket_servidor.close()


if __name__ == "__main__":  # pragma: no cover
    main()