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

IMPLEMENTADO (todas as etapas 1-11 do plano):
    1-2. Leitura de porta via argparse + loop de accept em 0.0.0.0, com
         timeout curto no accept() para Ctrl+C responder rápido (não
         confiável em todas as plataformas sem isso, sobretudo Windows).
    3.   Loop de leitura por cliente (buffer + protocolo.extrair_mensagens,
         tolerante a linha malformada isolada — ver _extrair_mensagens_
         tolerante).
    4.   Login (nome único, login_ok/login_erro, conexão permanece aberta
         em caso de erro, permitindo nova tentativa).
    5.   Mensagem geral / broadcast restrito à sala do remetente.
    6.   Mensagem privada (destinatário inexistente -> erro; falha de
         entrega isolada, não afeta o remetente).
    7.   Salas: entrar_sala e sair_sala compartilhando o MESMO mecanismo
         (_mudar_sala_do_cliente) — sair_sala é só entrar_sala("geral").
    8.   Listagem de usuários (todos os conectados, com a sala de cada um
         — não só os da sala de quem perguntou).
    9-11. Remoção única do cliente (saída limpa ou abrupta) via
         try/finally, robustez de broadcast (falha isolada por
         destinatário), e — acrescentado depois de um teste de estresse
         real que encontrou corrupção de framing — lock de envio por
         cliente (Cliente.lock_envio, em modelos.py), necessário porque
         mensagem privada faz threads DIFERENTES escreverem no socket de
         OUTRO cliente, e socket.sendall() não é atômico entre chamadas
         concorrentes de threads distintas para o mesmo socket.

Decisões de design tomadas aqui (vale registrar no relatório):
    - O remetente de uma mensagem_geral NÃO recebe de volta a própria
      mensagem no broadcast — ele já vê o que digitou no próprio
      terminal (input()), então ecoar de volta seria duplicado. Só os
      DEMAIS membros da sala recebem.
    - Em _mudar_sala_do_cliente, a mutação de sala_atual acontece ANTES
      dos broadcasts — isso faz o cliente se excluir naturalmente do
      broadcast da sala antiga (já não está mais listado nela), sem
      precisar de parâmetro extra para isso.
"""

import argparse
import os
import socket
import sys
import threading
from datetime import datetime
from typing import Optional

import protocolo
from modelos import Cliente, RegistroClientes
from persistencia import CAMINHO_BANCO_PADRAO, Historico
from usuarios import CAMINHO_BANCO_USUARIOS_PADRAO, Usuarios

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

# Limite de tamanho para nome de usuário e nome de sala — nenhum dos dois
# tinha limite antes (testado: um nome de 150 caracteres era aceito sem
# erro), o que não quebra nada tecnicamente, mas distorce a exibição na
# tela de todo mundo no chat sem necessidade real. Números escolhidos
# generosos o suficiente para qualquer nome/apelido real, sem serem
# ilimitados.
TAMANHO_MAXIMO_NOME = 30
TAMANHO_MAXIMO_SALA = 30

# Lock que serializa a sequência "mudar estado visível a outros clientes
# (registrar login, ou trocar de sala) + anunciar via broadcast" em
# relação a OUTRAS sequências do mesmo tipo rodando em paralelo.
#
# Necessário para que a ORDEM dos avisos que um cliente recebe corresponda
# à ordem real dos eventos — bug real encontrado via teste de estresse
# (600 logins em sequência rápida): socket.sendall() libera o GIL durante
# a chamada de rede, então era possível um cliente que logou DEPOIS
# terminar seu próprio ciclo de login+anúncio ANTES de um cliente que
# logou ANTES dele terminar o dele, fazendo o primeiro receber um aviso
# de entrada de alguém que, do ponto de vista dele, já devia estar lá.
#
# Cobre só login e troca de sala — não mensagem_geral/privada, que não
# têm essa exigência de ordem entre remetentes diferentes (mensagens de
# chat naturalmente intercalam por ordem de chegada, isso é esperado).
#
# Importante: cobre só a "janela de commit" (registrar + enviar +
# anunciar, tudo rápido), nunca a espera por input do usuário durante o
# login (que pode demorar indefinidamente) — travar nisso prenderia todo
# mundo atrás de quem está mais lento para digitar o nome.
_lock_anuncio = threading.Lock()


# --------------------------------------------------------------------------
# Saída no terminal — só cosmético (seção nova, a pedido, para deixar o
# console do servidor mais legível). NÃO afeta protocolo, rede, nem os
# testes: as cores só são aplicadas quando a saída é um terminal de
# verdade (sys.stdout.isatty()) — capturada por teste ou redirecionada
# para arquivo, sai como texto puro, sem nenhuma sequência ANSI no meio.
# --------------------------------------------------------------------------

class _Cor:
    RESET = "\033[0m"
    CINZA = "\033[90m"
    VERDE = "\033[92m"
    AMARELO = "\033[93m"
    VERMELHO = "\033[91m"
    CIANO = "\033[96m"


_USAR_COR = sys.stdout.isatty()

if sys.platform == "win32" and _USAR_COR:  # pragma: no cover
    # Só executa em Windows COM terminal de verdade — combinação de
    # condições que exigiria recarregar o módulo inteiro com
    # sys.platform e isatty() falsificados para testar de forma limpa.
    # Truque de compatibilidade de uma linha, de baixo risco; a
    # cobertura de linha não vale a complexidade de um teste artificial
    # aqui.
    os.system("")


def _c(texto: str, cor: str) -> str:
    """Aplica `cor` a `texto` só se a saída for um terminal de verdade."""
    if not _USAR_COR:
        return texto
    return f"{cor}{texto}{_Cor.RESET}"


def _hora() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _prefixo_hora() -> str:
    return _c(f"[{_hora()}]", _Cor.CINZA)


def _formatar_endereco(endereco) -> str:
    """('127.0.0.1', 53472) -> '127.0.0.1:53472' — mais legível que o
    repr de tupla cru (que ficava tipo '(('127.0.0.1', 53472))' quando
    combinado com outro parêntese ao redor)."""
    try:
        return f"{endereco[0]}:{endereco[1]}"
    except (TypeError, IndexError):
        return str(endereco)


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------
# Duas famílias de função aqui, de propósito:
#
#   _enviar / _enviar_erro_seguro (recebem socket.socket cru): usadas
#   SÓ durante a fase de login (_processar_login), antes de o cliente
#   existir no RegistroClientes. Nesse momento, nenhuma outra thread tem
#   referência a esse socket ainda — só a própria thread desta conexão —
#   então não há risco de escrita concorrente, e não precisa de lock.
#
#   _enviar_para_cliente / _enviar_seguro_para_cliente /
#   _enviar_erro_seguro_para_cliente (recebem um Cliente): usadas para
#   QUALQUER envio depois que o cliente está registrado — respostas
#   diretas (lista de usuários, confirmação de sala, erros) E broadcasts/
#   mensagens privadas vindas de outras threads. A partir desse ponto,
#   múltiplas threads podem ter referência ao mesmo socket ao mesmo
#   tempo, e socket.sendall() NÃO é atômico entre chamadas concorrentes
#   de threads diferentes para o MESMO socket — confirmado por teste de
#   estresse (mensagens grandes o suficiente para exigir mais de um
#   send() interno tinham bytes de threads diferentes intercalados,
#   corrompendo o framing). Por isso essas funções sempre passam pelo
#   cliente.lock_envio antes de escrever.

def _enviar(sock: socket.socket, mensagem: dict) -> None:
    sock.sendall(protocolo.serializar(mensagem))


def _enviar_erro_seguro(sock: socket.socket, motivo: str) -> None:
    """
    Tenta avisar o cliente de um erro sem propagar exceção — se o envio
    falhar, o socket provavelmente já está quebrado por outro motivo, e
    quem chama vai descobrir isso do jeito normal (recv retornando vazio
    ou lançando OSError) no próximo ciclo do loop.

    Usar SÓ durante a fase de login (ver nota da seção acima) — depois
    que o cliente está registrado, usar a variante _..._para_cliente.
    """
    try:
        _enviar(sock, protocolo.msg_erro(motivo))
    except OSError:
        pass


def _enviar_para_cliente(cliente: Cliente, mensagem: dict) -> None:
    """Envia `mensagem` para `cliente`, sob o lock de envio dele (ver nota da seção acima)."""
    _enviar_bytes_para_cliente(cliente, protocolo.serializar(mensagem))


def _enviar_bytes_para_cliente(cliente: Cliente, linha: bytes) -> None:
    """
    Variante de baixo nível de _enviar_para_cliente, recebendo bytes já
    serializados em vez de um dict — usada por _broadcast_sala para
    serializar a mensagem UMA VEZ e reenviar os mesmos bytes a vários
    destinatários, em vez de serializar de novo a cada um.
    """
    with cliente.lock_envio:
        cliente.socket.sendall(linha)


def _enviar_seguro_para_cliente(cliente: Cliente, mensagem: dict) -> None:
    """Como _enviar_para_cliente, mas não propaga OSError (mesma lógica de _enviar_erro_seguro)."""
    try:
        _enviar_para_cliente(cliente, mensagem)
    except OSError:
        pass


def _enviar_erro_seguro_para_cliente(cliente: Cliente, motivo: str) -> None:
    _enviar_seguro_para_cliente(cliente, protocolo.msg_erro(motivo))


def _extrair_mensagens_tolerante(buffer: bytes, enviar_erro) -> tuple:
    """
    Chama protocolo.extrair_mensagens em loop, recuperando as mensagens
    válidas mesmo quando uma linha no meio do lote é malformada.

    Sem isso, uma única linha ruim faria a MESMA linha ser reprocessada
    (e falhar) para sempre a cada novo dado recebido, travando a conexão
    num loop infinito de erro — bug real encontrado via teste de
    integração (ver ErroProtocolo em protocolo.py). Cada linha malformada
    gera um aviso 'erro' ao cliente; as mensagens válidas antes e depois
    dela no mesmo lote são preservadas e processadas normalmente.

    `enviar_erro` é uma função (motivo: str) -> None, para que quem chama
    escolha o mecanismo de envio certo: socket cru durante o login, ou
    _enviar_erro_seguro_para_cliente depois que o cliente já existe.
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
            enviar_erro(str(erro))


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
            _enviar_bytes_para_cliente(cliente, linha)
        except OSError:
            continue  # falha isolada — ver docstring acima


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def _processar_login(
    sock_cliente: socket.socket,
    endereco,
    registro: RegistroClientes,
    usuarios: Usuarios,
    buffer: bytes,
) -> tuple:
    """
    Loop de login: espera a primeira mensagem válida de tipo 'login' e
    tenta registrar o nome. Se o nome já estiver em uso, a senha for
    inválida, ou a autenticação falhar, responde login_erro/erro e
    CONTINUA esperando — permite nova tentativa sem reconectar
    (Especificação, seção 5).

    Qualquer outro tipo de mensagem recebido antes do login é rejeitado
    com erro, mas não derruba a conexão — o cliente pode tentar de novo.

    Autenticação por senha (nova): depois de validado o formato do nome
    e da senha, e checado que o nome não está ONLINE agora, a dupla
    (nome, senha) é conferida em usuarios.autenticar() — que também é
    responsável por CRIAR o cadastro automaticamente no primeiro login
    desse nome (não há etapa separada de "criar conta"; decisão de
    design documentada em usuarios.py).

    O registro (RegistroClientes.adicionar), o envio de login_ok, e o
    broadcast de "entrou no chat" acontecem juntos, sob _lock_anuncio —
    ver o comentário da constante para o porquê (bug de ordem de eventos
    encontrado via teste de estresse). A checagem de "nome já está
    online" e a autenticação de senha (usuarios.autenticar) também
    passaram a acontecer dentro do MESMO _lock_anuncio: sem isso, duas
    conexões logando com o mesmo nome nunca visto antes, ao mesmo tempo,
    poderiam as duas cair no ramo "cria cadastro" de
    usuarios.autenticar() sem nunca colidir entre si — o lock serializa
    a sequência inteira (peek online -> autenticar -> registrar) por
    conexão.

    Retorna (cliente, buffer_restante). `cliente` é None se a conexão foi
    encerrada (recv vazio) antes de um login bem-sucedido.
    """
    while True:
        dados = sock_cliente.recv(TAMANHO_BUFFER_RECV)
        if not dados:
            return None, buffer  # desconectou antes de logar

        buffer += dados
        mensagens, buffer = _extrair_mensagens_tolerante(
            buffer, lambda motivo: _enviar_erro_seguro(sock_cliente, motivo)
        )

        for msg in mensagens:
            if msg["tipo"] != protocolo.TIPO_LOGIN:
                _enviar_erro_seguro(sock_cliente, "primeira mensagem deve ser 'login'")
                continue

            nome = msg.get("nome")
            if not isinstance(nome, str) or not nome.strip():
                # login_erro (não um 'erro' genérico) de propósito: é o
                # único tipo de resposta que o loop de login do cliente
                # sabe tratar re-perguntando o apelido automaticamente
                # (ver realizar_login em cliente_app.py) — um 'erro'
                # genérico aqui deixaria o cliente preso esperando
                # login_ok/login_erro que nunca chegaria (bug real
                # encontrado ao testar as validações abaixo de ponta a
                # ponta com o cliente de verdade, não só com sockets
                # crus simulando o protocolo).
                _enviar(sock_cliente, protocolo.msg_login_erro("campo 'nome' invalido"))
                continue

            nome = nome.strip()

            # Bug real encontrado em teste manual: um nome com espaço
            # (ex: "Joao Pedro") é ambíguo para /priv, que espera
            # exatamente dois argumentos (destinatário e texto) separados
            # por espaço — "/priv Joao Pedro oi" era interpretado como
            # destinatário="Joao", texto="Pedro oi", e "Joao" (sozinho)
            # nunca existe no registro. Em vez de complicar o parsing do
            # cliente com aspas/escape, a Especificação implícita mais
            # simples e robusta é: apelido é sempre uma única palavra.
            if any(c.isspace() for c in nome):
                _enviar(sock_cliente, protocolo.msg_login_erro(
                    "nome nao pode conter espacos (mensagem privada usa o "
                    "nome como uma unica palavra) — tente algo como 'joao_pedro'"
                ))
                continue

            if len(nome) > TAMANHO_MAXIMO_NOME:
                _enviar(sock_cliente, protocolo.msg_login_erro(
                    f"nome muito longo (maximo {TAMANHO_MAXIMO_NOME} caracteres)"
                ))
                continue

            # --- NOVO: validação de formato da senha ---
            # Mesmo padrão de robustez do campo 'nome' logo acima: campo
            # ausente/tipo errado não pode nunca chegar a travar o
            # servidor, e a resposta é sempre login_erro (não 'erro'
            # genérico) pelo mesmo motivo já documentado para 'nome':
            # é o único tipo que o loop de retry do cliente sabe tratar.
            senha = msg.get("senha")
            if not isinstance(senha, str) or not senha:
                _enviar(sock_cliente, protocolo.msg_login_erro("campo 'senha' invalido"))
                continue

            cliente = Cliente(nome=nome, sock=sock_cliente, endereco=endereco)

            with _lock_anuncio:
                # 1. nome já está ONLINE agora? (JÁ EXISTE, sem mudança
                #    de comportamento — só adiantado para ANTES da
                #    checagem de senha, como "peek" sem efeito colateral,
                #    pra não vazar "essa conta existe/não existe" pra
                #    quem só está testando se o nome está ocupado)
                if registro.buscar(nome) is not None:
                    _enviar(sock_cliente, protocolo.msg_login_erro("nome ja em uso"))
                    continue

                # 2. NOVO: nome existe no banco de usuários?
                #    - não existe -> cria o cadastro com essa senha, ok
                #    - existe, senha bate -> ok
                #    - existe, senha não bate -> login_erro "senha incorreta"
                autenticado, motivo = usuarios.autenticar(nome, senha)
                if not autenticado:
                    _enviar(sock_cliente, protocolo.msg_login_erro(motivo))
                    continue

                # 3. registra de fato (JÁ EXISTE, sem mudança). Continua
                #    dentro do MESMO _lock_anuncio do passo 1 acima —
                #    por isso registro.adicionar() aqui sempre deveria
                #    dar True: nenhuma outra thread pôde ter registrado
                #    esse nome entre o passo 1 e aqui.
                if not registro.adicionar(cliente):
                    # defensivo — não deveria acontecer dado o passo 1,
                    # mas mantém o protocolo coerente se acontecer.
                    _enviar(sock_cliente, protocolo.msg_login_erro("nome ja em uso"))
                    continue

                _enviar(sock_cliente, protocolo.msg_login_ok(nome))
                _broadcast_sala(
                    registro,
                    cliente.sala_atual,
                    protocolo.msg_notificacao(f"{nome} entrou no chat"),
                    excluir_nome=nome,
                )

            return cliente, buffer


# --------------------------------------------------------------------------
# Roteamento de mensagens já autenticadas
# --------------------------------------------------------------------------

def _mudar_sala_do_cliente(registro: RegistroClientes, cliente: Cliente, nova_sala: str) -> None:
    """
    Implementa entrar_sala E sair_sala com o MESMO mecanismo (sair_sala é
    só uma chamada com nova_sala="geral") — seção 7 da Especificação:
    nenhum caminho de código separado para as duas operações.

    Ordem de operações importante: a mutação de sala_atual (via
    RegistroClientes.mudar_sala, sob lock) acontece ANTES dos
    broadcasts. Isso faz o cliente se excluir naturalmente do broadcast
    da sala antiga (listar_por_sala já não o encontra mais lá — não
    precisa de excluir_nome ali). Já no broadcast da sala nova, ele
    PRECISA de excluir_nome, porque a essa altura o cliente já aparece
    listado nela e receberia a própria notificação de volta.

    Tudo isso acontece sob _lock_anuncio — mesma razão do login: sem
    isso, duas trocas de sala concorrentes poderiam anunciar seus
    eventos fora de ordem (ver comentário da constante).
    """
    sala_antiga = cliente.sala_atual
    if nova_sala == sala_antiga:
        _enviar_para_cliente(cliente, protocolo.msg_notificacao(f"voce ja esta na sala '{nova_sala}'"))
        return

    with _lock_anuncio:
        registro.mudar_sala(cliente.nome, nova_sala)
        _broadcast_sala(registro, sala_antiga, protocolo.msg_notificacao(f"{cliente.nome} saiu da sala"))
        _broadcast_sala(
            registro, nova_sala,
            protocolo.msg_notificacao(f"{cliente.nome} entrou na sala"),
            excluir_nome=cliente.nome,
        )
        _enviar_para_cliente(cliente, protocolo.msg_notificacao(f"voce entrou na sala '{nova_sala}'"))


def _rotear_mensagem(
    registro: RegistroClientes, cliente: Cliente, msg: dict, historico: Historico
) -> bool:
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
        # Persistir ANTES do broadcast, não depois — bug real encontrado
        # via teste de estresse (mesma classe do bug de ordem de anúncio
        # de login, corrigido antes com _lock_anuncio): como cada cliente
        # roda numa thread própria, se o remetente A registra DEPOIS de
        # fazer o broadcast, é possível que o remetente B (respondendo
        # logo em seguida, já depois de RECEBER a mensagem de A) registre
        # a mensagem DELE no banco antes de A terminar de registrar a
        # sua — invertendo a ordem cronológica no histórico, mesmo a
        # ordem de ENVIO tendo sido correta. Persistir antes do broadcast
        # garante que, no momento em que o destinatário recebe a
        # mensagem (e pode decidir responder), a gravação já aconteceu.
        # Envolvido em try/except: uma falha ao persistir (ex: disco
        # cheio) não deve impedir a entrega da mensagem ao vivo — só o
        # histórico fica incompleto, o chat continua funcionando.
        try:
            historico.registrar(cliente.sala_atual, cliente.nome, texto)
        except Exception:
            pass
        mensagem_repasse = protocolo.msg_mensagem_geral_repassar(cliente.nome, texto)
        _broadcast_sala(registro, cliente.sala_atual, mensagem_repasse, excluir_nome=cliente.nome)
        return True

    if tipo == protocolo.TIPO_MENSAGEM_PRIVADA:
        destinatario_nome = msg.get("destinatario")
        texto = msg.get("texto", "")
        if not isinstance(destinatario_nome, str) or not destinatario_nome.strip():
            _enviar_erro_seguro_para_cliente(cliente, "campo 'destinatario' invalido ou ausente")
            return True
        destinatario = registro.buscar(destinatario_nome)
        if destinatario is None:
            _enviar_erro_seguro_para_cliente(cliente, f"destinatario '{destinatario_nome}' nao encontrado")
            return True
        mensagem_repasse = protocolo.msg_mensagem_privada_repassar(cliente.nome, texto)
        # falha isolada: se o destinatário desconectou bem nesse instante,
        # a remoção dele é feita pela própria thread dele (seção 9) — não
        # é responsabilidade de quem está mandando a mensagem privada.
        _enviar_seguro_para_cliente(destinatario, mensagem_repasse)
        return True

    if tipo == protocolo.TIPO_ENTRAR_SALA:
        sala_pedida = msg.get("sala")
        if not isinstance(sala_pedida, str) or not sala_pedida.strip():
            _enviar_erro_seguro_para_cliente(cliente, "campo 'sala' invalido ou ausente")
            return True
        sala_normalizada = sala_pedida.strip().casefold()
        if len(sala_normalizada) > TAMANHO_MAXIMO_SALA:
            _enviar_erro_seguro_para_cliente(
                cliente, f"nome de sala muito longo (maximo {TAMANHO_MAXIMO_SALA} caracteres)"
            )
            return True
        _mudar_sala_do_cliente(registro, cliente, sala_normalizada)
        return True

    if tipo == protocolo.TIPO_SAIR_SALA:
        _mudar_sala_do_cliente(registro, cliente, "geral")
        return True

    if tipo == protocolo.TIPO_LISTAR_USUARIOS:
        usuarios = registro.listar_todos()
        _enviar_seguro_para_cliente(cliente, protocolo.msg_lista_usuarios(usuarios))
        return True

    if tipo == protocolo.TIPO_HISTORICO:
        limite = msg.get("limite")  # Historico.buscar_recentes normaliza valor invalido/ausente
        mensagens = historico.buscar_recentes(cliente.sala_atual, limite)
        _enviar_seguro_para_cliente(cliente, protocolo.msg_historico_resposta(cliente.sala_atual, mensagens))
        return True

    _enviar_erro_seguro_para_cliente(cliente, f"tipo de mensagem desconhecido: {tipo!r}")
    return True


# --------------------------------------------------------------------------
# Ciclo de vida de uma conexão
# --------------------------------------------------------------------------

def tratar_cliente(
    sock_cliente: socket.socket, endereco, registro: RegistroClientes,
    historico: Historico, usuarios: Usuarios,
) -> None:
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
        cliente, buffer = _processar_login(sock_cliente, endereco, registro, usuarios, buffer)
        if cliente is None:
            return  # desconectou antes de completar o login

        # O broadcast de "entrou no chat" já acontece dentro de
        # _processar_login (sob _lock_anuncio, junto com o registro e o
        # login_ok) — ver docstring lá para o porquê.

        while True:
            dados = sock_cliente.recv(TAMANHO_BUFFER_RECV)
            if not dados:
                break  # desconexão abrupta

            buffer += dados
            mensagens, buffer = _extrair_mensagens_tolerante(
                buffer, lambda motivo: _enviar_erro_seguro_para_cliente(cliente, motivo)
            )

            continuar = True
            for msg in mensagens:
                continuar = _rotear_mensagem(registro, cliente, msg, historico)
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
            print(f"{_prefixo_hora()} {_c('Conexao encerrada', _Cor.VERMELHO)}: {cliente.nome} ({_formatar_endereco(endereco)})")
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
            print(f"{_prefixo_hora()} {_c('Conexao encerrada antes do login', _Cor.CINZA)}: {_formatar_endereco(endereco)}")


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


def loop_accept(
    socket_servidor: socket.socket, registro: RegistroClientes,
    historico: Historico, usuarios: Usuarios,
) -> None:
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

        print(f"{_prefixo_hora()} {_c('Nova conexao', _Cor.CIANO)}: {_formatar_endereco(endereco)}")
        thread = threading.Thread(
            target=tratar_cliente,
            args=(sock_cliente, endereco, registro, historico, usuarios),
            daemon=True,
        )
        thread.start()


def _resolver_caminho_banco(banco_explicito: Optional[str], porta_real: int) -> str:
    """
    Decide o caminho do arquivo SQLite do histórico de mensagens.

    Se o usuário passou --banco explicitamente, usa exatamente esse
    valor — de propósito, isso permite até compartilhar histórico entre
    execuções diferentes, se alguém quiser fazer isso por escolha.

    Caso contrário, isola automaticamente por porta (chat_historico_
    <porta>.db). Bug real encontrado: duas instâncias do servidor em
    portas DIFERENTES, nenhuma delas usando --banco, acabavam lendo e
    escrevendo no MESMO arquivo (o nome padrão era fixo, sem relação
    nenhuma com a porta) — mensagens de um servidor apareciam no
    /historico do outro, mesmo sendo processos e portas completamente
    diferentes. Isolar por porta por padrão elimina essa surpresa sem
    tirar a flexibilidade de quem quiser um arquivo específico.
    """
    if banco_explicito is not None:
        return banco_explicito
    return f"chat_historico_{porta_real}.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor do chat tcpapo-chat-message")
    parser.add_argument(
        "--porta",
        type=int,
        default=PORTA_PADRAO,
        help=f"Porta de escuta (padrao: {PORTA_PADRAO})",
    )
    parser.add_argument(
        "--banco",
        type=str,
        default=None,
        help=(
            "Arquivo SQLite para o historico de mensagens (padrao: "
            "isolado automaticamente por porta, chat_historico_<porta>.db)"
        ),
    )
    parser.add_argument(
        "--banco-usuarios",
        type=str,
        default=CAMINHO_BANCO_USUARIOS_PADRAO,
        help=f"Arquivo SQLite para o cadastro de usuarios (padrao: {CAMINHO_BANCO_USUARIOS_PADRAO})",
    )
    args = parser.parse_args()

    registro = RegistroClientes()
    historico = Historico(args.banco)
    usuarios = Usuarios(args.banco_usuarios)

    try:
        socket_servidor = criar_socket_servidor(HOST_PADRAO, args.porta)
    except OSError as erro:
        # Caso mais comum aqui: porta já em uso (outro servidor.py já
        # rodando nela, ou outro processo qualquer). Sem este tratamento,
        # o usuário via um traceback cru — inconsistente com o padrão de
        # mensagens amigáveis já usado em cliente_app.py para erros
        # equivalentes do lado do cliente. Mensagem encurtada de
        # propósito: o texto bruto do sistema operacional (ex: "[WinError
        # 10048] Normalmente é permitida apenas uma utilização de cada
        # endereço de soquete...") não ajuda o usuário e só polui a tela
        # — a dica logo abaixo já diz o que fazer.
        print(f"{_c('[erro]', _Cor.VERMELHO)} não foi possível iniciar o servidor na porta {args.porta} — já está em uso.")
        print(f"{_c('[dica]', _Cor.CINZA)} tente outra porta (--porta) ou encerre o processo que já está usando essa.")        
        usuarios.fechar()
        sys.exit(1)

    # A porta REAL (não a pedida) — importante quando --porta 0 é usado
    # (deixa o SO escolher): sem isso, tanto a mensagem de "escutando em"
    # quanto o nome do arquivo de histórico isolado por porta mostrariam
    # "0" em vez da porta de verdade que o SO escolheu.
    porta_real = socket_servidor.getsockname()[1]

    historico = Historico(_resolver_caminho_banco(args.banco, porta_real))

    print(f"{_prefixo_hora()} {_c('Servidor escutando em', _Cor.VERDE)} {HOST_PADRAO}:{porta_real} (Ctrl+C para encerrar)")

    try:
        loop_accept(socket_servidor, registro, historico, usuarios)
    except KeyboardInterrupt:
        print(f"\n{_c('Encerrando servidor...', _Cor.AMARELO)}")
    finally:
        socket_servidor.close()
        historico.fechar()
        usuarios.fechar()


if __name__ == "__main__":  # pragma: no cover
    main()