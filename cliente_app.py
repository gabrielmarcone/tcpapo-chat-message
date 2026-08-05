"""
cliente_app.py — Cliente de chat (tcpapo-chat-message)

Responsabilidade:
    - Ler IP/porta via linha de comando e conectar ao servidor por TCP.
    - Login: envia 'login', trata 'login_ok'/'login_erro', permitindo
      nova tentativa de nome/senha em caso de erro.
    - Concorrência: thread dedicada exclusivamente à recepção (recv +
      desserializa + imprime); a thread principal só lê input() e envia.
    - Parsing de comandos digitados pelo usuário (texto comum, /priv,
      /lista, /entrar, /sair_sala, /historico, /sair), isolado em
      parse_comando() para não espalhar ifs pelo main().
    - Tratamento de todos os cenários de erro de rede e de entrada
      (IP/porta inválidos, servidor indisponível, timeout, conexão
      derrubada em qualquer momento da sessão, Ctrl+C) sem tracebacks e
      com encerramento limpo dos recursos (socket, thread).

Todo texto digitado que não seja um comando reconhecido (não começa com
"/", ou é um "/" desconhecido/malformado) é tratado como mensagem geral
(broadcast na sala atual) ou como comando inválido — ver parse_comando().
"""

import argparse
import os
import socket
import sys
import threading
from datetime import datetime
from getpass import getpass
from typing import Optional, Tuple

import protocolo


# --------------------------------------------------------------------------
# Saída no terminal — cosmético: não afeta protocolo nem lógica de rede.
# Cores só aparecem quando a saída é um terminal de verdade
# (sys.stdout.isatty()); redirecionada para arquivo ou capturada por
# teste, sai como texto puro.
# --------------------------------------------------------------------------

class _Cor:
    RESET = "\033[0m"
    CINZA = "\033[90m"
    VERDE = "\033[92m"
    AMARELO = "\033[93m"
    VERMELHO = "\033[91m"
    AZUL = "\033[94m"
    MAGENTA = "\033[95m"
    CIANO = "\033[96m"
    NEGRITO = "\033[1m"
    ITALICO = "\033[3m"
    SUBLINHADO = "\033[4m"

    # Paleta reservada só para identidade de usuário (hash do nome -> cor),
    # separada da paleta semântica acima (que já significa uma coisa
    # fixa: vermelho = erro, amarelo = aviso/notificação, verde = ok,
    # ciano = rótulo de lista/histórico). São as cores ANSI normais (não
    # as "brilhantes" 90-97 usadas acima), para nunca ficar visualmente
    # igual a nenhuma cor semântica, mesmo lado a lado.
    USUARIO = [
        "\033[31m",  # vermelho
        "\033[32m",  # verde
        "\033[33m",  # amarelo/oliva
        "\033[34m",  # azul
        "\033[35m",  # magenta
        "\033[36m",  # ciano
    ]

    # Cor fixa e reservada só para "você" — nunca sorteada pelo hash (ver
    # _cor_do_usuario), para sua própria mensagem ser sempre reconhecível
    # de cara, em qualquer sala, mesmo se por coincidência ela combinasse
    # com a cor de outro usuário.
    VOCE = "\033[97m"  # branco brilhante


_USAR_COR = sys.stdout.isatty()

if sys.platform == "win32" and _USAR_COR:
    # Em terminais Windows mais antigos (fora do Windows Terminal), o
    # processamento de sequências ANSI vem desligado por padrão.
    # os.system("") liga isso pro resto do processo, sem depender de
    # nenhuma biblioteca externa.
    os.system("")


def _c(texto: str, cor: str) -> str:
    """Aplica `cor` a `texto` só se a saída for um terminal de verdade."""
    if not _USAR_COR:
        return texto
    return f"{cor}{texto}{_Cor.RESET}"


def _hora() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _cor_do_usuario(nome: str) -> str:
    """
    Sempre a mesma cor para o mesmo nome (estilo IRC/Discord antigo) —
    faz uma sala cheia ficar muito mais fácil de acompanhar visualmente,
    porque cada pessoa "tem uma cor" consistente do início ao fim da
    conversa, em vez de tudo sair só em negrito branco.

    casefold() de propósito: mesma convenção usada no resto do sistema
    (RegistroClientes, usuarios.py) — "Alice" e "alice" são a mesma
    pessoa, então têm que sair com a mesma cor.

    Soma dos códigos dos caracteres (não hash() nativo do Python): hash()
    de string muda a cada execução do processo por segurança
    (PYTHONHASHSEED aleatório), e a cor precisa ser estável entre
    sessões diferentes, não só dentro de uma.
    """
    indice = sum(ord(caractere) for caractere in nome.casefold()) % len(_Cor.USUARIO)
    return _Cor.USUARIO[indice]


def _estilo_do_usuario(nome: str) -> str:
    """
    Estilo completo (cor + negrito, e às vezes + sublinhado) para o nome
    de um remetente. Com só 6 cores na paleta de identidade, uma sala
    com mais de 6 pessoas ativas — ou até menos, por coincidência —
    inevitavelmente repete cor entre duas pessoas diferentes. Sublinhado
    como segunda dimensão dobra o espaço de combinações distintas (6
    cores × 2) sem sair de atributos ANSI básicos (negrito=1,
    sublinhado=4), os mais universalmente suportados — mais seguro do
    que arriscar cores de 256 cores, que nem todo terminal mais antigo
    entende direito.
    """
    espaco_total = len(_Cor.USUARIO) * 2
    indice = sum(ord(caractere) for caractere in nome.casefold()) % espaco_total
    cor = _Cor.USUARIO[indice % len(_Cor.USUARIO)]
    sublinhado = _Cor.SUBLINHADO if indice >= len(_Cor.USUARIO) else ""
    return _Cor.NEGRITO + cor + sublinhado


def _c_nome(nome: str) -> str:
    """Nome de um remetente, com o estilo consistente daquele nome."""
    return _c(nome, _estilo_do_usuario(nome))


def _ok(texto: str) -> None:
    print(f"{_c('✓', _Cor.VERDE)} {texto}")


def _erro(texto: str) -> None:
    print(f"{_c('✗', _Cor.VERMELHO)} {texto}")


def _aviso(texto: str) -> None:
    print(f"{_c('⚠', _Cor.AMARELO)} {texto}")


def _info(texto: str) -> None:
    print(f"{_c('[info]', _Cor.CIANO)} {texto}")


# --------------------------------------------------------------------------
# Constantes de robustez
# --------------------------------------------------------------------------

# Timeout aplicado só durante a tentativa de connect(). Depois de
# conectado, o socket volta ao modo bloqueante padrão (sock.settimeout
# (None) em conectar()), já que o restante da sessão depende de recv()
# bloqueante rodando em thread própria — não faria sentido um timeout de
# inatividade na conversa.
TIMEOUT_CONEXAO = 5.0  # segundos


# --------------------------------------------------------------------------
# Validação de argumentos de linha de comando
# --------------------------------------------------------------------------
# Usada como `type=` no argparse para --porta. Sem isso, uma porta como
# "abc" já falha no argparse com uma mensagem razoável (sem traceback),
# mas uma porta fora da faixa válida de portas TCP (ex: 0 ou 99999) seria
# aceita sem erro e só explodiria mais tarde, de forma confusa, dentro de
# socket.connect(). Centralizamos a checagem aqui para falhar cedo e com
# mensagem clara, no mesmo padrão de erro do argparse (sem traceback).

def validar_porta(valor: str) -> int:
    try:
        porta = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"porta deve ser um número inteiro, recebido: '{valor}'"
        )
    if not (1 <= porta <= 65535):
        raise argparse.ArgumentTypeError(
            f"porta deve estar entre 1 e 65535, recebido: {porta}"
        )
    return porta


# --------------------------------------------------------------------------
# Conexão
# --------------------------------------------------------------------------

def conectar(ip: str, porta: int) -> socket.socket:
    """
    Cria um socket TCP e conecta a (ip, porta).

    Trata os erros de conexão mais comuns de forma organizada, com
    mensagem amigável para o usuário, e encerra o programa se a conexão
    não puder ser estabelecida (sem conexão, não há mais nada a fazer).

    sock.settimeout(TIMEOUT_CONEXAO) é aplicado antes do connect(): sem
    um timeout explícito, uma tentativa de conexão a um IP que existe na
    rede mas não responde (ex: firewall descartando o pacote
    silenciosamente, em vez de recusar a conexão) ficaria travada
    indefinidamente em vez de falhar com uma mensagem clara. Depois de
    conectar com sucesso, o timeout é removido (settimeout(None)) para
    não afetar o recv() bloqueante usado pelo resto da aplicação.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_CONEXAO)
    try:
        sock.connect((ip, porta))
    except ConnectionRefusedError:
        _erro(f"conexão recusada por {ip}:{porta} (servidor não está escutando aí?)")
        sock.close()
        sys.exit(1)
    except socket.gaierror:
        _erro(f"não foi possível resolver o endereço '{ip}' (IP ou host inválido).")
        sock.close()
        sys.exit(1)
    except socket.timeout:
        _erro(f"tempo esgotado ({TIMEOUT_CONEXAO:.0f}s) ao conectar a {ip}:{porta} "
              f"— servidor indisponível ou inacessível na rede.")
        sock.close()
        sys.exit(1)
    except KeyboardInterrupt:
        _info("conexão cancelada pelo usuário.")
        sock.close()
        sys.exit(0)
    except OSError as erro:
        _erro(f"falha de rede ao conectar a {ip}:{porta} ({erro}).")
        sock.close()
        sys.exit(1)

    sock.settimeout(None)
    _ok(f"conectado a {ip}:{porta}")
    return sock


# --------------------------------------------------------------------------
# Impressão de mensagens recebidas do servidor
# --------------------------------------------------------------------------
# Função compartilhada entre realizar_login() (mensagens que cheguem antes
# da resposta de login) e thread_recepcao() (todo o resto), para o
# usuário ver a mensagem formatada da mesma forma nos dois casos.

class EstadoCliente:
    """
    Estado local do cliente, compartilhado entre a thread principal (que
    envia comandos) e a thread de recepção (que exibe mensagens). Hoje
    guarda só a sala atual.

    Necessário porque o protocolo não inclui o nome da sala na mensagem
    mensagem_geral — o servidor decide o escopo do broadcast a partir do
    estado interno dele, sem expor isso no dado da mensagem — então, sem
    rastrear isso aqui, o cliente não teria como saber de qual sala veio
    uma mensagem geral recebida, e sempre mostraria "[geral]" mesmo
    depois de /entrar em outra sala.

    Atualizado de forma otimista em main(), logo após enviar /entrar ou
    /sair_sala com sucesso — sem esperar confirmação do servidor. Isso é
    seguro porque o servidor sempre aceita esses comandos quando o campo
    já foi validado no cliente (a única exceção — pedir para entrar na
    sala em que já está — ainda deixa o cliente na mesma sala, então a
    atualização otimista continua correta nesse caso também).
    """

    def __init__(self):
        self.sala_atual = "geral"


def imprimir_mensagem(msg: dict, estado: EstadoCliente) -> None:
    """Formata e imprime na tela uma mensagem já desserializada do servidor."""
    tipo = msg.get("tipo")
    hora = _c(f"[{_hora()}]", _Cor.CINZA)

    if tipo == protocolo.TIPO_MENSAGEM_GERAL:
        sala = _c(f"[{estado.sala_atual}]", _Cor.AZUL)
        remetente = _c_nome(msg.get("remetente", "?"))
        print(f"{hora} {sala} {remetente}: {msg.get('texto', '')}")
    elif tipo == protocolo.TIPO_MENSAGEM_PRIVADA:
        rotulo = _c("(privado)", _Cor.MAGENTA)
        remetente = _c_nome(msg.get("remetente", "?"))
        print(f"{hora} {rotulo} {remetente}: {msg.get('texto', '')}")
    elif tipo == protocolo.TIPO_NOTIFICACAO:
        print(f"{hora} {_c('»', _Cor.AMARELO)} {_c(msg.get('texto', ''), _Cor.AMARELO)}")
    elif tipo == protocolo.TIPO_ERRO:
        print(f"{hora} {_c('✗ [erro do servidor]', _Cor.VERMELHO)} {msg.get('motivo', '')}")
    elif tipo == protocolo.TIPO_LISTA_USUARIOS:
        usuarios = msg.get("usuarios", [])
        print(f"{hora} {_c('[lista]', _Cor.CIANO)} usuários conectados:")
        if not usuarios:
            print("    nenhum usuário conectado.")
        else:
            for usuario in usuarios:
                nome = _c_nome(usuario.get("nome", "?"))
                sala = usuario.get("sala", "?")
                print(f"    - {nome} ({sala})")
    elif tipo == protocolo.TIPO_HISTORICO_RESPOSTA:
        # Cada item já vem com "hora" formatada pelo servidor (o momento
        # em que a mensagem foi enviada de verdade, não agora) — por
        # isso usamos ela em vez de `hora` (que é o instante em que esta
        # resposta chegou). Indentado com "    " no mesmo padrão do
        # /lista, para ficar visualmente claro que é um bloco só, e não
        # mensagens novas chegando ao vivo.
        sala = msg.get("sala", "?")
        mensagens_historico = msg.get("mensagens", [])
        print(f"{hora} {_c('[histórico]', _Cor.CIANO)} sala '{sala}' — {len(mensagens_historico)} mensagem(ns):")
        if not mensagens_historico:
            print("    nenhuma mensagem no histórico desta sala ainda.")
        else:
            for item in mensagens_historico:
                hora_item = _c(f"[{item.get('hora', '?')}]", _Cor.CINZA)
                remetente_item = _c_nome(item.get("remetente", "?"))
                print(f"    {hora_item} {remetente_item}: {item.get('texto', '')}")
    else:
        # Tipo não tratado (não deveria acontecer, dado o contrato
        # fechado de protocolo.py). Não inventamos formatação para
        # campos que não conhecemos — só exibimos bruto.
        print(f"{hora} [{tipo}] {msg}")


def _imprimir_minha_mensagem_geral(estado: EstadoCliente, texto: str) -> None:
    """
    Confirmação formatada da própria mensagem geral, logo após enviar —
    o servidor nunca ecoa a mensagem de volta para quem mandou, então
    sem isso a única coisa na tela seria o eco cru do terminal (o que
    foi digitado), sem hora, sala ou nenhuma formatação — bem diferente
    de como as mensagens dos outros aparecem. "você" usa sempre a mesma
    cor reservada (_Cor.VOCE), nunca a cor sorteada por hash — assim a
    própria fala é reconhecível de cara, mesmo numa sala cheia de gente
    colorida.
    """
    hora = _c(f"[{_hora()}]", _Cor.CINZA)
    sala = _c(f"[{estado.sala_atual}]", _Cor.AZUL)
    voce = _c("você", _Cor.NEGRITO + _Cor.VOCE)
    print(f"{hora} {sala} {voce}: {texto}")


def _imprimir_minha_mensagem_privada(destinatario: str, texto: str) -> None:
    hora = _c(f"[{_hora()}]", _Cor.CINZA)
    rotulo = _c("(privado)", _Cor.MAGENTA)
    voce = _c("você", _Cor.NEGRITO + _Cor.VOCE)
    alvo = _c_nome(destinatario)
    print(f"{hora} {rotulo} {voce} → {alvo}: {texto}")


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def realizar_login(sock: socket.socket) -> Tuple[str, bytes]:
    """
    Pede um apelido e senha ao usuário, envia 'login' (via
    protocolo.msg_login) e espera a resposta do servidor.

    - Se vier login_ok: retorna (nome_confirmado, buffer_restante).
      buffer_restante contém quaisquer bytes já recebidos além da
      resposta de login (ex: se o servidor emendou uma notificação no
      mesmo pacote) — repassado para thread_recepcao() não perder nada.
    - Se vier login_erro: mostra o motivo e pede outro apelido/senha,
      reenviando a mensagem (laço externo).

    BrokenPipeError e ConnectionResetError são tratados antes do
    `except OSError` genérico (são subclasses), para dar uma mensagem
    mais clara ao usuário sobre o que aconteceu (conexão caiu, não é um
    erro de rede qualquer). KeyboardInterrupt também é tratado
    explicitamente, para que Ctrl+C durante o login (digitando o
    apelido ou esperando resposta) encerre de forma limpa em vez de
    subir como traceback.
    """
    buffer = b""

    try:
        while True:
            nome = input("Escolha um apelido: ").strip()
            if not nome:
                _aviso("o apelido não pode ser vazio.")
                continue

            # getpass() não ecoa o que é digitado.
            senha = getpass("Senha: ")
            if not senha:
                _aviso("a senha não pode ser vazia.")
                continue

            try:
                sock.sendall(protocolo.serializar(protocolo.msg_login(nome, senha)))
            except (BrokenPipeError, ConnectionResetError):
                _erro("conexão perdida ao enviar login.")
                sock.close()
                sys.exit(1)
            except OSError as erro:
                _erro(f"falha ao enviar login ({erro}).")
                sock.close()
                sys.exit(1)

            resposta = None
            while resposta is None:
                try:
                    dados = sock.recv(4096)
                except (BrokenPipeError, ConnectionResetError):
                    _erro("conexão perdida durante o login.")
                    sock.close()
                    sys.exit(1)
                except OSError:
                    _erro("conexão perdida durante o login.")
                    sock.close()
                    sys.exit(1)

                if not dados:
                    _erro("servidor fechou a conexão durante o login.")
                    sock.close()
                    sys.exit(1)

                buffer += dados
                try:
                    mensagens, buffer = protocolo.extrair_mensagens(buffer)
                except protocolo.ErroProtocolo as erro:
                    _erro(f"erro de protocolo: {erro}")
                    continue

                for msg in mensagens:
                    if msg["tipo"] in (protocolo.TIPO_LOGIN_OK, protocolo.TIPO_LOGIN_ERRO):
                        resposta = msg
                        break
                    # Mensagem que não é resposta de login (situação
                    # incomum, mas o framing permite): só exibimos e
                    # seguimos esperando. EstadoCliente() novo aqui (não
                    # o de main()) é correto de propósito: antes do
                    # login terminar, o cliente nunca teve chance de
                    # mudar de sala, então "geral" é sempre a resposta
                    # certa.
                    imprimir_mensagem(msg, EstadoCliente())

            if resposta["tipo"] == protocolo.TIPO_LOGIN_OK:
                _ok(f"login bem-sucedido como '{resposta['nome']}'.")
                return resposta["nome"], buffer

            _aviso(f"login recusado: {resposta['motivo']}")
            # volta ao topo do laço externo para pedir outro apelido
    except KeyboardInterrupt:
        _info("login cancelado pelo usuário.")
        try:
            sock.close()
        except OSError:
            pass
        sys.exit(0)


# --------------------------------------------------------------------------
# Thread de recepção
# --------------------------------------------------------------------------

def _encerrar_conexao_forcado(sock: socket.socket, mensagem: str) -> None:
    """
    Usado pela thread de recepção quando a conexão cai por um motivo que
    não foi a thread principal pedindo para encerrar (ex: servidor caiu,
    cabo de rede foi desconectado). Nesse momento a thread principal
    muito provavelmente está bloqueada em input(), esperando o usuário
    digitar algo — e input() é uma chamada bloqueante que não escuta
    eventos (threading.Event) nem sockets, só o teclado. Não existe
    forma portátil e simples (sem depender de bibliotecas extras) de
    "acordar" educadamente essa chamada.

    Por isso, em vez de deixar o programa preso esperando o usuário
    apertar Enter para só então perceber que a conexão caiu, avisamos o
    usuário, fechamos o socket corretamente e encerramos o processo
    aqui mesmo. os._exit() (em vez de sys.exit()) é necessário porque
    sys.exit() apenas levanta SystemExit, que uma thread secundária não
    consegue propagar para a thread principal bloqueada em input().
    """
    print(f"\n{_c(mensagem, _Cor.VERMELHO)}")
    try:
        sock.close()
    except OSError:
        pass
    os._exit(0)


def thread_recepcao(
    sock: socket.socket,
    buffer_inicial: bytes,
    evento_encerrando: threading.Event,
    estado: EstadoCliente,
) -> None:
    """
    Roda em thread separada. Só faz três coisas, nesta ordem, em loop:
    recebe bytes do socket, desserializa via protocolo.extrair_mensagens,
    imprime cada mensagem completa. Nunca lê input() do usuário.

    Encerra quando evento_encerrando é sinalizado (pela thread principal,
    em encerrar()) ou quando a conexão cai por qualquer motivo.

    Distinção importante entre dois casos de recv()/OSError falhando:
        1. evento_encerrando já estava setado -> foi a thread principal
           que fechou o socket de propósito (usuário digitou /sair ou
           Ctrl+C, via encerrar()). É o caminho normal de desligamento:
           não há erro real, só terminamos o loop em silêncio.
        2. evento_encerrando não estava setado -> a conexão caiu por
           conta própria (servidor encerrou, cabo caiu, etc.) enquanto
           a sessão estava ativa. Aí sim é um erro de verdade, tratado
           por _encerrar_conexao_forcado() (ver docstring acima).
    Sem essa distinção, um /sair normal do usuário (que fecha o socket
    de propósito) seria erroneamente relatado como "conexão perdida".
    """
    buffer = buffer_inicial

    while not evento_encerrando.is_set():
        try:
            dados = sock.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            if evento_encerrando.is_set():
                break
            _encerrar_conexao_forcado(sock, "[erro] conexão perdida com o servidor.")
        except OSError:
            # Caso mais comum aqui: socket fechado localmente por
            # encerrar() (thread principal) — desligamento esperado.
            break

        if not dados:
            if evento_encerrando.is_set():
                break
            _encerrar_conexao_forcado(sock, "[servidor] conexão encerrada pelo servidor.")

        buffer += dados
        try:
            mensagens, buffer = protocolo.extrair_mensagens(buffer)
        except protocolo.ErroProtocolo as erro:
            _erro(f"erro de protocolo: {erro}")
            continue

        for msg in mensagens:
            imprimir_mensagem(msg, estado)

    evento_encerrando.set()


# --------------------------------------------------------------------------
# Envio (usado pela thread principal)
# --------------------------------------------------------------------------

def enviar(sock: socket.socket, mensagem: dict) -> bool:
    """
    Serializa `mensagem` (via protocolo.serializar) e envia pelo socket.
    Retorna True se enviou com sucesso, False se houve falha (nesse caso
    já imprime um erro amigável; quem chama decide o que fazer a seguir
    — ver main(), que encerra a sessão quando enviar() retorna False).

    BrokenPipeError e ConnectionResetError são tratados antes do
    `except OSError` genérico, para dar ao usuário uma mensagem
    específica de "conexão perdida" em vez de "falha ao enviar
    mensagem" — a causa é diferente (rede caiu vs. outro erro de I/O) e
    vale a pena o usuário saber qual foi.
    """
    try:
        sock.sendall(protocolo.serializar(mensagem))
        return True
    except protocolo.ErroProtocolo as erro:
        # Só ocorreria por engano de programação local (dict fora do
        # formato) — nunca por causa do que o usuário digitou.
        _erro(f"mensagem malformada não enviada ({erro}).")
        return False
    except (BrokenPipeError, ConnectionResetError):
        _erro("conexão perdida ao enviar mensagem.")
        return False
    except OSError as erro:
        _erro(f"falha ao enviar mensagem ({erro}).")
        return False


# --------------------------------------------------------------------------
# Parsing de comandos
# --------------------------------------------------------------------------
# Todas as ações possíveis do que o usuário digita, decididas em um único
# lugar (parse_comando) em vez de espalhadas como ifs pelo main().

ACAO_ENVIAR = "enviar"      # mensagem pronta (dict) para enviar ao servidor
ACAO_SAIR = "sair"          # usuário pediu /sair: quem chama deve encerrar
ACAO_INVALIDO = "invalido"  # comando malformado/desconhecido; ajuda já impressa
ACAO_VAZIO = "vazio"        # linha vazia/só espaço; nada a fazer

COMANDO_PRIV = "/priv"
COMANDO_LISTA = "/lista"
COMANDO_ENTRAR = "/entrar"
COMANDO_SAIR_SALA = "/sair_sala"
COMANDO_SAIR = "/sair"
COMANDO_HISTORICO = "/historico"
COMANDO_AJUDA = "/ajuda"
COMANDO_LIMPAR = "/limpar"
COMANDO_CAFE = "/cafe"  # easter egg -- de propósito não entra em TEXTO_AJUDA_COMANDOS
COMANDO_MINECRAFT = "/minecraft"  # idem
COMANDO_BATMAN = "/batman"  # idem

TEXTO_AJUDA_COMANDOS = (
    f"  {COMANDO_PRIV} <usuario> <mensagem>   envia mensagem privada\n"
    f"  {COMANDO_LISTA}                       lista usuários conectados\n"
    f"  {COMANDO_ENTRAR} <sala>               entra em uma sala\n"
    f"  {COMANDO_SAIR_SALA}                   volta para a sala geral\n"
    f"  {COMANDO_HISTORICO} [quantidade]      mostra mensagens recentes da sala atual\n"
    f"  {COMANDO_AJUDA}                       mostra esta lista de comandos novamente\n"
    f"  {COMANDO_LIMPAR}                      limpa a tela (histórico do servidor não é afetado)\n"
    f"  {COMANDO_SAIR}                        encerra a conexão\n"
    "  <texto livre>                  mensagem para o chat geral"
)


def _imprimir_bloco_comandos() -> None:
    """Bloco de ajuda usado tanto na tela de boas-vindas quanto por /ajuda,
    para as duas exibições nunca ficarem dessincronizadas entre si."""
    print(_c(" Comandos:", _Cor.CINZA))
    print(TEXTO_AJUDA_COMANDOS)


def _mostrar_ajuda() -> Tuple[str, None]:
    """/ajuda -- reimprime a lista de comandos, exatamente como aparece
    ao conectar. 100% local, não manda nada ao servidor."""
    _imprimir_bloco_comandos()
    return ACAO_VAZIO, None


def _limpar_tela() -> Tuple[str, None]:
    """
    /limpar -- limpa a tela do terminal. Afeta só o que está visível
    localmente neste momento: o histórico de mensagens continua salvo
    no servidor (SQLite) e pode ser consultado a qualquer momento com
    /historico, mesmo depois de limpar a tela.

    A sequência ANSI usada tem três partes, nesta ordem:
        \\033[2J -> limpa a tela visível
        \\033[3J -> limpa também o buffer de rolagem (scrollback) —
                    sem essa parte, o conteúdo antigo continua existindo
                    e reaparece se o usuário rolar a tela pra cima; é
                    justamente essa parte que faz a limpeza ser de
                    verdade, e não só um "empurrão" visual do conteúdo
                    antigo para fora da área visível.
        \\033[H  -> move o cursor de volta para o topo

    Sequência só é enviada se a saída for um terminal de verdade
    (_USAR_COR) -- em saída redirecionada/capturada por teste, os bytes
    de controle não fariam sentido nenhum e só sujariam o resultado.
    """
    if _USAR_COR:
        print("\033[2J\033[3J\033[H", end="")
        _info("tela limpa — o histórico do servidor continua intacto (use /historico para consultá-lo).")
    else:
        _aviso(f"{COMANDO_LIMPAR} não tem efeito quando a saída não é um terminal.")
    return ACAO_VAZIO, None

_ARTE_CAFE = r"""
          ( (
           ) )
        ........
        |      |]
        \      /
         `----'"""


def _mostrar_easter_egg_cafe() -> Tuple[str, None]:
    """
    Easter egg — /cafe, comando secreto (de propósito fora da lista de
    ajuda). 100% local: não manda nada pro servidor, não depende de
    nenhuma mudança em protocolo.py nem servidor.py. Reaproveita
    ACAO_VAZIO (mesma ação de uma linha em branco: nada a enviar, nada
    mais a fazer).
    """
    print(_c(_ARTE_CAFE, _Cor.AMARELO))
    print(f"  {_c('☕ pausa pro café — volta já!', _Cor.AMARELO + _Cor.NEGRITO)}")
    return ACAO_VAZIO, None


_ARTE_CREEPER = "\n".join([
    "",
    "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
    "▓▓░░░░▓▓▓▓░░░░▓▓",
    "▓▓░░░░▓▓▓▓░░░░▓▓",
    "▓▓▓▓▓▓░░░░▓▓▓▓▓▓",
    "▓▓▓▓░░░░░░░░▓▓▓▓",
    "▓▓▓▓░░░░░░░░▓▓▓▓",
    "▓▓▓▓░░▓▓▓▓░░▓▓▓▓",
    "▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓",
])


def _mostrar_easter_egg_minecraft() -> Tuple[str, None]:
    """Easter egg — /minecraft. Mesmo raciocínio de _mostrar_easter_egg_cafe."""
    print(_c(_ARTE_CREEPER, _Cor.VERDE))
    print(f"  {_c('sssss... BOOM! (era só um creeper, relaxa)', _Cor.VERDE + _Cor.NEGRITO)}")
    return ACAO_VAZIO, None


_ARTE_MORCEGO = "\n".join([
    "",
    "    ⠀⠀⢀⣀⡠⠤⠤⠴⠶⠶⠶⠶⠦⠤⠤⢄⣀⠀⠀⠀⠀⠀⠀⠀⠀",
    "   ⣠⠖⢛⣩⣤⠂⠀⠀⠀⣶⡀⢀⣶⠀⠀⠀⠐⣤⣍⡛⠲⣄⠀⠀⠀⠀",
    "⢀⡴⢋⣴⣾⣿⣿⣿⠀⠀⠀⠀⣿⣿⣿⣿⠀⠀⠀⠀⣿⣿⣿⣷⣦⡙⢦⡀⠀",
    "⡞⢠⣿⣿⣿⣿⣿⣿⣷⣤⣤⣴⣿⣿⣿⣿⣦⣤⣤⣾⣿⣿⣿⣿⣿⣿⡆⢳⠀",
    "⡁⢻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠀⠆",
    "⢧⡈⢿⣿⣿⣿⠿⠿⣿⡿⠿⠿⣿⣿⣿⣿⠿⠿⢿⣿⠿⠿⣿⣿⣿⡿⢁⡼⠀",
    "⠀⠳⢄⡙⠿⣇⠀⠀⠈⠁⠀⠀⠈⢿⡿⠁⠀⠀⠈⠁⠀⠀⣸⠿⢋⡠⠞⠀⠀",
    "⠀⠀⠀⠉⠲⢤⣀⡀⠀⠀⠀⠀⠀⠀⠁⠀⠀⠀⠀⠀⢀⣀⡤⠖⠉⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠈⠉⠉⠐⠒⠒⠒⠒⠒⠒⠒⠒⠒⠉⠉⠁⠀⠀⠀⠀⠀⠀⠀",
])


def _mostrar_easter_egg_batman() -> Tuple[str, None]:
    """Easter egg — /batman. Mesmo raciocínio de _mostrar_easter_egg_cafe."""
    print(_c(_ARTE_MORCEGO, _Cor.CINZA))
    print(f"  {_c('🦇 ele vigia o código, nas sombras da noite.', _Cor.CINZA + _Cor.NEGRITO)}")
    return ACAO_VAZIO, None


def _comando_invalido(uso: str) -> Tuple[str, None]:
    """Imprime a mensagem de ajuda para um comando malformado e devolve o
    par (ACAO_INVALIDO, None) que parse_comando() deve retornar. Nenhuma
    mensagem é enviada ao servidor nesse caso."""
    print(f"{_c('⚠ [uso]', _Cor.AMARELO)} {uso}")
    return ACAO_INVALIDO, None


def parse_comando(texto: str) -> Tuple[str, Optional[dict]]:
    """
    Interpreta uma linha digitada pelo usuário e decide o que fazer com
    ela, sem nunca levantar exceção — qualquer entrada do usuário
    (incluindo lixo) é tratada aqui dentro.

    Retorna (acao, mensagem):
        (ACAO_ENVIAR, dict)   -> dict é uma mensagem pronta, já montada
                                 com uma função protocolo.msg_*, para
                                 enviar ao servidor.
        (ACAO_SAIR, None)     -> usuário digitou '/sair'; quem chama deve
                                 encerrar a conexão localmente (não há
                                 nada a enviar ao servidor).
        (ACAO_INVALIDO, None) -> a linha começa com '/' mas não é um
                                 comando reconhecido, ou está mal
                                 formada (faltam argumentos obrigatórios,
                                 ou há argumentos onde não deveria).
                                 A mensagem de ajuda já foi impressa;
                                 nada deve ser enviado ao servidor.
        (ACAO_VAZIO, None)    -> linha vazia ou só espaços em branco;
                                 nada a fazer.

    Texto comum (que não começa com '/') vira sempre mensagem geral, via
    protocolo.msg_mensagem_geral_enviar().
    """
    texto = texto.strip()

    if not texto:
        return ACAO_VAZIO, None

    if not texto.startswith("/"):
        return ACAO_ENVIAR, protocolo.msg_mensagem_geral_enviar(texto)

    partes = texto.split(maxsplit=1)
    comando = partes[0].lower()
    resto = partes[1] if len(partes) > 1 else ""

    if comando == COMANDO_PRIV:
        sub_partes = resto.split(maxsplit=1)
        if len(sub_partes) < 2 or not sub_partes[1].strip():
            return _comando_invalido(f"{COMANDO_PRIV} <usuario> <mensagem>")
        destinatario, texto_msg = sub_partes[0], sub_partes[1]
        return ACAO_ENVIAR, protocolo.msg_mensagem_privada_enviar(destinatario, texto_msg)

    if comando == COMANDO_LISTA:
        if resto:
            return _comando_invalido(f"{COMANDO_LISTA} não aceita argumentos")
        return ACAO_ENVIAR, protocolo.msg_listar_usuarios()

    if comando == COMANDO_ENTRAR:
        sala = resto.strip()
        if not sala:
            return _comando_invalido(f"{COMANDO_ENTRAR} <sala>")
        return ACAO_ENVIAR, protocolo.msg_entrar_sala(sala)

    if comando == COMANDO_SAIR_SALA:
        if resto:
            return _comando_invalido(f"{COMANDO_SAIR_SALA} não aceita argumentos")
        return ACAO_ENVIAR, protocolo.msg_sair_sala()

    if comando == COMANDO_HISTORICO:
        if not resto:
            return ACAO_ENVIAR, protocolo.msg_historico()
        if not resto.isdigit() or int(resto) <= 0:
            return _comando_invalido(f"{COMANDO_HISTORICO} [quantidade]  (numero inteiro positivo, opcional)")
        return ACAO_ENVIAR, protocolo.msg_historico(int(resto))

    if comando == COMANDO_SAIR:
        if resto:
            return _comando_invalido(f"{COMANDO_SAIR} não aceita argumentos")
        return ACAO_SAIR, None

    if comando == COMANDO_AJUDA:
        if resto:
            return _comando_invalido(f"{COMANDO_AJUDA} não aceita argumentos")
        return _mostrar_ajuda()

    if comando == COMANDO_LIMPAR:
        if resto:
            return _comando_invalido(f"{COMANDO_LIMPAR} não aceita argumentos")
        return _limpar_tela()

    if comando == COMANDO_CAFE:
        return _mostrar_easter_egg_cafe()

    if comando == COMANDO_MINECRAFT:
        return _mostrar_easter_egg_minecraft()

    if comando == COMANDO_BATMAN:
        return _mostrar_easter_egg_batman()

    return _comando_invalido(f"comando desconhecido '{comando}'")


# --------------------------------------------------------------------------
# Encerramento
# --------------------------------------------------------------------------

def encerrar(sock: socket.socket, evento_encerrando: threading.Event) -> None:
    """
    Fecha a conexão de forma organizada.

    O comando /sair (via parse_comando) apenas sinaliza para o main()
    encerrar localmente — não enviamos protocolo.msg_sair() pela rede,
    já que o servidor já trata desconexão abrupta como caminho normal,
    então simplesmente fechar o socket é suficiente e correto.
    """
    evento_encerrando.set()
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # já pode estar fechado do outro lado; não é um erro real aqui
    sock.close()
    _ok("conexão encerrada.")


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
        "--porta", type=validar_porta, default=5000,
        help="Porta do servidor (padrão: 5000)",
    )
    args = parser.parse_args()

    ip = args.ip.strip()
    if not ip:
        _erro("--ip não pode ser vazio.")
        sys.exit(1)

    # sock e thread começam como None: em caso de erro/Ctrl+C bem no
    # início (antes de existirem), o bloco finally abaixo sabe o que
    # ainda precisa (ou não) ser limpo, sem depender de variáveis
    # inexistentes.
    sock: Optional[socket.socket] = None
    thread: Optional[threading.Thread] = None
    evento_encerrando: Optional[threading.Event] = None

    try:
        sock = conectar(ip, args.porta)
        nome, buffer_inicial = realizar_login(sock)

        evento_encerrando = threading.Event()
        estado = EstadoCliente()
        thread = threading.Thread(
            target=thread_recepcao,
            args=(sock, buffer_inicial, evento_encerrando, estado),
            daemon=True,
        )
        thread.start()

        largura = 60
        linha = _c("─" * largura, _Cor.CINZA)
        print(linha)
        print(f" {_c('Bem-vindo(a), ' + nome + '!', _Cor.VERDE + _Cor.NEGRITO)}")
        print(f" Digite uma mensagem e Enter para enviar ao chat geral.")
        print(f" {_c(COMANDO_SAIR, _Cor.NEGRITO)} ou Ctrl+C encerra a qualquer momento.")
        print(linha)
        _imprimir_bloco_comandos()
        print(linha)

        while not evento_encerrando.is_set():
            try:
                texto = input()
            except EOFError:
                # entrada padrão fechada (ex: `< /dev/null` ou pipe encerrado)
                break

            if evento_encerrando.is_set():
                # servidor pode ter caído enquanto o usuário digitava
                break

            acao, mensagem = parse_comando(texto)

            if acao in (ACAO_VAZIO, ACAO_INVALIDO):
                continue

            if acao == ACAO_SAIR:
                _info(f"encerrando ({COMANDO_SAIR})...")
                break

            # acao == ACAO_ENVIAR
            if not enviar(sock, mensagem):
                _info("encerrando devido a falha no envio.")
                break

            # Atualização otimista da sala local (ver EstadoCliente) —
            # feita só depois do envio ter sucesso, e só para os dois
            # comandos que realmente mudam de sala.
            if mensagem["tipo"] == protocolo.TIPO_ENTRAR_SALA:
                estado.sala_atual = mensagem["sala"]
            elif mensagem["tipo"] == protocolo.TIPO_SAIR_SALA:
                estado.sala_atual = "geral"
            elif mensagem["tipo"] == protocolo.TIPO_MENSAGEM_GERAL:
                # O servidor nunca ecoa a mensagem geral de volta pra
                # quem mandou — sem isso, a única coisa na tela seria o
                # eco cru do input(), sem hora/sala/formatação nenhuma,
                # bem diferente de como a mensagem dos outros aparece.
                _imprimir_minha_mensagem_geral(estado, mensagem["texto"])
            elif mensagem["tipo"] == protocolo.TIPO_MENSAGEM_PRIVADA:
                _imprimir_minha_mensagem_privada(mensagem["destinatario"], mensagem["texto"])
    except KeyboardInterrupt:
        _info("encerrando (Ctrl+C)...")
    finally:
        if sock is not None and thread is not None and evento_encerrando is not None:
            # Sessão completa: login concluído, thread de recepção rodando.
            encerrar(sock, evento_encerrando)
            thread.join(timeout=1)
        elif sock is not None:
            # Conectou, mas Ctrl+C interrompeu antes da thread iniciar
            # (durante ou logo após o login) — só o socket precisa fechar.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


if __name__ == "__main__":
    main()