"""
cliente_app.py — Cliente de chat (tcpapo-chat-message)

Responsabilidade:
    - Ler IP/porta via linha de comando e conectar ao servidor por TCP.
    - Login: envia 'login', trata 'login_ok'/'login_erro', permitindo
      nova tentativa de nome/senha em caso de erro.
    - Concorrência: thread dedicada exclusivamente à recepção (recv +
      desserializa + imprime); a thread principal só lê input() e envia.
    - Reconexão automática: se a conexão cair de forma inesperada (não
      solicitada pelo usuário), tenta reconectar sozinho, com espera
      exponencial entre tentativas, reautenticando com as mesmas
      credenciais e restaurando a sala em que o usuário estava (ver
      supervisionar_conexao() e _tentar_reconectar()).
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
import time
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
# Reconexão automática
# --------------------------------------------------------------------------
# Estratégia: espera exponencial entre tentativas (backoff), o mesmo
# princípio usado pelo próprio TCP para retransmissão e por praticamente
# todo cliente de rede real (apps de chat, dispositivos IoT, etc.) —
# começa com uma espera curta e vai dobrando a cada tentativa malsucedida,
# até um teto máximo, em vez de martelar o servidor em intervalos fixos
# ou desistir de imediato. Depois de um tempo total gasto sem sucesso,
# desiste de vez: continuar tentando para sempre, sem nenhum limite, não
# é realista nem desejável — se o servidor não voltou em alguns minutos,
# o mais provável é que o problema exija intervenção manual (reconfigurar
# rede, reiniciar o cliente com outro endereço, etc.), não mais uma
# tentativa automática.
RECONEXAO_ESPERA_INICIAL_SEGUNDOS = 2.0
RECONEXAO_ESPERA_MAXIMA_SEGUNDOS = 30.0
RECONEXAO_DESISTIR_APOS_SEGUNDOS = 300.0  # 5 minutos de tentativas seguidas


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

def _tentar_conectar_uma_vez(ip: str, porta: int) -> Tuple[Optional[socket.socket], Optional[str]]:
    """
    Uma única tentativa de conexão TCP, sem decidir nada sobre encerrar
    o programa — devolve (socket, None) em caso de sucesso, ou
    (None, motivo_do_erro) em caso de falha, sempre fechando o socket
    antes de devolver None.

    Fatorado à parte de conectar() para ser reaproveitado também pela
    reconexão automática (_tentar_reconectar()), que precisa tentar
    repetidas vezes sem que uma falha isolada encerre o processo — só
    conectar() (usada na primeira conexão da sessão) decide encerrar o
    programa se falhar.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_CONEXAO)
    try:
        sock.connect((ip, porta))
    except ConnectionRefusedError:
        sock.close()
        return None, f"conexão recusada por {ip}:{porta} (servidor não está escutando aí?)"
    except socket.gaierror:
        sock.close()
        return None, f"não foi possível resolver o endereço '{ip}' (IP ou host inválido)."
    except socket.timeout:
        sock.close()
        return None, (
            f"tempo esgotado ({TIMEOUT_CONEXAO:.0f}s) ao conectar a {ip}:{porta} "
            f"— servidor indisponível ou inacessível na rede."
        )
    except KeyboardInterrupt:
        sock.close()
        raise
    except OSError as erro:
        sock.close()
        return None, f"falha de rede ao conectar a {ip}:{porta} ({erro})."

    sock.settimeout(None)
    return sock, None


def conectar(ip: str, porta: int) -> socket.socket:
    """
    Cria um socket TCP e conecta a (ip, porta), encerrando o programa
    com mensagem amigável se a primeira conexão da sessão falhar (sem
    conexão nenhuma, não há mais nada a fazer nesse momento).

    KeyboardInterrupt é tratado explicitamente porque a tentativa de
    connect() fica bloqueada por até TIMEOUT_CONEXAO segundos — sem
    isso, um Ctrl+C durante essa espera subiria como traceback em vez
    de um encerramento limpo.
    """
    try:
        sock, motivo = _tentar_conectar_uma_vez(ip, porta)
    except KeyboardInterrupt:
        _info("conexão cancelada pelo usuário.")
        sys.exit(0)

    if sock is None:
        _erro(motivo)
        sys.exit(1)

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
    envia comandos) e a thread de recepção/reconexão (que exibe
    mensagens e, se necessário, reconecta sozinha).

    sala_atual: necessário porque o protocolo não inclui o nome da sala
    na mensagem mensagem_geral — o servidor decide o escopo do broadcast
    a partir do estado interno dele, sem expor isso no dado da mensagem
    — então, sem rastrear isso aqui, o cliente não teria como saber de
    qual sala veio uma mensagem geral recebida, e sempre mostraria
    "[geral]" mesmo depois de /entrar em outra sala. Atualizado de forma
    otimista em main(), logo após enviar /entrar ou /sair_sala com
    sucesso — sem esperar confirmação do servidor. Isso é seguro porque
    o servidor sempre aceita esses comandos quando o campo já foi
    validado no cliente (a única exceção — pedir para entrar na sala em
    que já está — ainda deixa o cliente na mesma sala, então a
    atualização otimista continua correta nesse caso também).

    ip / porta / nome / senha: preenchidos por main() logo após o login
    inicial, e usados só pela reconexão automática (_tentar_reconectar)
    para saber para onde reconectar e com quais credenciais reautenticar
    sozinha, sem precisar perguntar nada ao usuário de novo. A senha
    fica em memória — nunca é gravada em disco nem logada — pelo tempo
    de vida do processo; é o mesmo tipo de trade-off que qualquer
    aplicativo com "continuar conectado" faz, aqui limitado à duração de
    uma única sessão.

    sock / lock: o socket ativo agora, e um lock que protege a troca
    dele. A thread principal (main()) sempre lê estado.sock sob o lock,
    logo antes de cada envio — nunca guarda uma cópia da referência por
    muito tempo — porque uma reconexão pode trocar o socket a qualquer
    momento, em background, sem a thread principal saber previamente.
    """

    def __init__(self):
        self.sala_atual = "geral"
        self.ip: Optional[str] = None
        self.porta: Optional[int] = None
        self.nome: Optional[str] = None
        self.senha: Optional[str] = None
        self.sock: Optional[socket.socket] = None
        self.lock = threading.Lock()


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

def realizar_login(sock: socket.socket) -> Tuple[str, str, bytes]:
    """
    Pede um apelido e senha ao usuário, envia 'login' (via
    protocolo.msg_login) e espera a resposta do servidor.

    - Se vier login_ok: retorna (nome_confirmado, senha, buffer_restante).
      A senha é devolvida junto (e não descartada) porque main() precisa
      guardá-la em EstadoCliente para a reconexão automática poder
      reautenticar sozinha mais tarde, sem perguntar de novo ao usuário.
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
                return resposta["nome"], senha, buffer

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

def _encerrar_processo_final() -> None:
    """
    Usado quando a reconexão automática esgota todas as tentativas (ou
    é cancelada) e não há mais nada a fazer. Nesse momento a thread
    principal quase certamente está bloqueada em input(), esperando o
    usuário digitar algo — e input() é uma chamada bloqueante que não
    escuta eventos (threading.Event) nem sockets, só o teclado. Não
    existe forma portátil e simples (sem depender de bibliotecas
    extras) de "acordar" educadamente essa chamada.

    Por isso, em vez de deixar o programa preso esperando o usuário
    apertar Enter para só então perceber que a sessão acabou, o processo
    encerra sozinho aqui. os._exit() (em vez de sys.exit()) é necessário
    porque sys.exit() apenas levanta SystemExit, que uma thread
    secundária não consegue propagar para a thread principal bloqueada
    em input().
    """
    os._exit(1)


def _dormir_interrompivel(segundos: float, evento_encerrando: threading.Event) -> None:
    """
    Equivalente a time.sleep(segundos), mas verificando
    evento_encerrando a cada fração de segundo — permite que /sair ou
    Ctrl+C interrompam a espera entre tentativas de reconexão na hora,
    em vez de precisar esperar o intervalo inteiro (que pode chegar a
    RECONEXAO_ESPERA_MAXIMA_SEGUNDOS) para perceber o pedido de saída.
    """
    fim = time.time() + segundos
    while not evento_encerrando.is_set():
        restante = fim - time.time()
        if restante <= 0:
            return
        time.sleep(min(0.2, restante))


def _relogar_automaticamente(
    sock: socket.socket, nome: str, senha: str
) -> Tuple[bool, bytes, Optional[str]]:
    """
    Reenvia login e senha automaticamente logo após uma reconexão, sem
    perguntar nada ao usuário — as credenciais já foram fornecidas no
    login original desta sessão (ver EstadoCliente).

    Diferente de realizar_login(), não pede um nome novo se a
    autenticação falhar: se o mesmo nome não puder ser reutilizado (por
    exemplo, se outra pessoa o registrou nesse meio-tempo em que a
    conexão esteve caída), a reconexão automática é abandonada —
    repetir a mesma tentativa não resolveria isso sozinha, e pedir um
    nome diferente no meio de uma reconexão automática, sem garantia de
    que o usuário esteja olhando pra tela nesse momento, criaria mais
    confusão do que ajuda.

    Retorna (True, buffer_restante, None) em caso de sucesso, ou
    (False, b"", motivo) em caso de falha.
    """
    buffer = b""
    try:
        sock.sendall(protocolo.serializar(protocolo.msg_login(nome, senha)))
    except OSError as erro:
        return False, b"", f"falha ao enviar login ({erro})"

    sock.settimeout(TIMEOUT_CONEXAO)
    try:
        while True:
            try:
                dados = sock.recv(4096)
            except socket.timeout:
                return False, b"", f"tempo esgotado ({TIMEOUT_CONEXAO:.0f}s) esperando resposta de login"
            except OSError as erro:
                return False, b"", f"conexão perdida durante nova autenticação ({erro})"

            if not dados:
                return False, b"", "servidor fechou a conexão durante nova autenticação"

            buffer += dados
            try:
                mensagens, buffer = protocolo.extrair_mensagens(buffer)
            except protocolo.ErroProtocolo as erro:
                return False, b"", f"erro de protocolo durante nova autenticação ({erro})"

            for msg in mensagens:
                if msg["tipo"] == protocolo.TIPO_LOGIN_OK:
                    return True, buffer, None
                if msg["tipo"] == protocolo.TIPO_LOGIN_ERRO:
                    return False, b"", msg.get("motivo", "login recusado")
    finally:
        try:
            sock.settimeout(None)
        except OSError:
            pass


def _tentar_reconectar(estado: "EstadoCliente", evento_encerrando: threading.Event) -> Tuple[bool, bytes]:
    """
    Chamada quando a conexão cai de forma inesperada (não solicitada
    pelo usuário). Tenta reconectar ao mesmo endereço, com espera
    exponencial entre tentativas (RECONEXAO_ESPERA_INICIAL_SEGUNDOS,
    dobrando a cada falha até o teto de RECONEXAO_ESPERA_MAXIMA_SEGUNDOS),
    até um total de RECONEXAO_DESISTIR_APOS_SEGUNDOS gasto em tentativas
    malsucedidas seguidas.

    Ao reconectar com sucesso:
        1. reautentica sozinha, com o nome e a senha usados no login
           original (_relogar_automaticamente);
        2. se o usuário estava numa sala diferente de "geral", pede
           para entrar nela de novo (todo login novo começa em "geral"
           do lado do servidor, então isso precisa ser refeito
           manualmente aqui);
        3. pede o histórico recente da sala, para o usuário recuperar o
           que foi trocado por outras pessoas enquanto a conexão estava
           caída.

    Retorna (True, buffer_restante) se reconectou e reautenticou; ou
    (False, b"") se desistiu — por esgotar o tempo total, por Ctrl+C,
    ou porque a reautenticação com as mesmas credenciais foi recusada.
    """
    print()
    _aviso("conexão perdida com o servidor. Tentando reconectar automaticamente...")

    espera = RECONEXAO_ESPERA_INICIAL_SEGUNDOS
    tempo_gasto = 0.0
    tentativa = 0

    try:
        while tempo_gasto < RECONEXAO_DESISTIR_APOS_SEGUNDOS:
            if evento_encerrando.is_set():
                return False, b""

            tentativa += 1
            _info(f"nova tentativa em {espera:.0f}s (tentativa {tentativa})...")
            _dormir_interrompivel(espera, evento_encerrando)
            tempo_gasto += espera

            if evento_encerrando.is_set():
                return False, b""

            sock_novo, motivo = _tentar_conectar_uma_vez(estado.ip, estado.porta)
            if sock_novo is None:
                _aviso(f"tentativa {tentativa} falhou ({motivo})")
                espera = min(espera * 2, RECONEXAO_ESPERA_MAXIMA_SEGUNDOS)
                continue

            sucesso_login, buffer_novo, motivo_login = _relogar_automaticamente(
                sock_novo, estado.nome, estado.senha
            )
            if not sucesso_login:
                _erro(f"reconectou, mas não foi possível autenticar de novo: {motivo_login}. Encerrando.")
                try:
                    sock_novo.close()
                except OSError:
                    pass
                return False, b""

            with estado.lock:
                estado.sock = sock_novo
            _ok(f"reconectado a {estado.ip}:{estado.porta} como '{estado.nome}'.")

            if estado.sala_atual != "geral":
                try:
                    sock_novo.sendall(protocolo.serializar(protocolo.msg_entrar_sala(estado.sala_atual)))
                except OSError:
                    pass

            try:
                sock_novo.sendall(protocolo.serializar(protocolo.msg_historico()))
            except OSError:
                pass

            return True, buffer_novo
    except KeyboardInterrupt:
        _info("reconexão cancelada pelo usuário.")
        return False, b""

    _erro(f"não foi possível reconectar após {RECONEXAO_DESISTIR_APOS_SEGUNDOS / 60:.0f} minutos tentando. Encerrando.")
    return False, b""


def _receber_ate_cair(
    sock: socket.socket,
    buffer_inicial: bytes,
    evento_encerrando: threading.Event,
    estado: "EstadoCliente",
) -> None:
    """
    Núcleo de recepção de mensagens para UMA conexão específica. Só faz
    três coisas, nesta ordem, em loop: recebe bytes do socket,
    desserializa via protocolo.extrair_mensagens, imprime cada mensagem
    completa. Nunca lê input() do usuário.

    Sempre retorna (nunca encerra o processo diretamente) quando a
    conexão cai ou evento_encerrando é sinalizado — quem chama
    (supervisionar_conexao) decide, olhando para evento_encerrando, se
    isso foi um pedido de saída do usuário ou uma queda inesperada que
    deve disparar uma tentativa de reconexão.
    """
    buffer = buffer_inicial

    while not evento_encerrando.is_set():
        try:
            dados = sock.recv(4096)
        except OSError:
            return  # conexão caiu ou foi fechada localmente -- quem chama decide o que fazer

        if not dados:
            return  # servidor fechou a conexão -- idem

        buffer += dados
        try:
            mensagens, buffer = protocolo.extrair_mensagens(buffer)
        except protocolo.ErroProtocolo as erro:
            _erro(f"erro de protocolo: {erro}")
            continue

        for msg in mensagens:
            imprimir_mensagem(msg, estado)


def supervisionar_conexao(
    estado: "EstadoCliente",
    evento_encerrando: threading.Event,
    buffer_inicial: bytes,
) -> None:
    """
    Roda em thread separada durante toda a sessão, alternando entre dois
    papéis:
        1. recepção normal (_receber_ate_cair), enquanto a conexão atual
           estiver de pé;
        2. reconexão automática com espera exponencial
           (_tentar_reconectar), sempre que a conexão cair de forma
           inesperada (não solicitada pelo usuário).

    Ao reconectar com sucesso, volta ao papel 1 usando o novo socket
    (guardado em estado.sock); ao desistir de vez, encerra o processo
    (_encerrar_processo_final) — não há como continuar a sessão sem
    conexão nenhuma e sem expectativa razoável de recuperá-la.
    """
    buffer = buffer_inicial

    while not evento_encerrando.is_set():
        with estado.lock:
            sock_atual = estado.sock

        _receber_ate_cair(sock_atual, buffer, evento_encerrando, estado)
        buffer = b""

        if evento_encerrando.is_set():
            return  # pedido de saída do usuário (/sair ou Ctrl+C) -- não tenta reconectar

        # A esta altura a conexão caiu de fato -- o socket antigo já não
        # serve para nada, mas ainda precisa ser fechado explicitamente
        # para liberar o descritor de arquivo antes de abrir um novo.
        try:
            sock_atual.close()
        except OSError:
            pass

        sucesso, buffer = _tentar_reconectar(estado, evento_encerrando)
        if not sucesso:
            if evento_encerrando.is_set():
                # evento_encerrando já estava setado (usuário pediu
                # /sair ou Ctrl+C durante a tentativa de reconexão) --
                # a própria main() já está fazendo o encerramento normal
                # nesse caso; não é uma desistência de verdade, então
                # NÃO deve encerrar o processo à força aqui.
                return
            # Desistência de verdade: esgotou o tempo total tentando, ou
            # a reautenticação foi recusada pelo servidor. Aí sim não há
            # mais nada a fazer sozinho.
            evento_encerrando.set()
            _encerrar_processo_final()
            return


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

def encerrar(estado: "EstadoCliente", evento_encerrando: threading.Event) -> None:
    """
    Fecha a conexão de forma organizada.

    Recebe `estado` (não um socket direto) porque o socket ativo pode
    ter sido trocado por uma reconexão automática em algum momento da
    sessão — o socket correto a fechar é sempre o mais atual
    (estado.sock), nunca o que existia quando a sessão começou.

    O comando /sair (via parse_comando) apenas sinaliza para o main()
    encerrar localmente — não enviamos protocolo.msg_sair() pela rede,
    já que o servidor já trata desconexão abrupta como caminho normal,
    então simplesmente fechar o socket é suficiente e correto.

    evento_encerrando é sinalizado ANTES de fechar o socket: é esse
    sinal que faz supervisionar_conexao() (rodando em outra thread)
    entender que este fechamento foi pedido pelo usuário, e não tentar
    reconectar por conta própria.
    """
    evento_encerrando.set()
    with estado.lock:
        sock_atual = estado.sock
    try:
        sock_atual.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # já pode estar fechado do outro lado; não é um erro real aqui
    sock_atual.close()
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

    # sock, estado, thread começam como None: em caso de erro/Ctrl+C bem
    # no início (antes de existirem), o bloco finally abaixo sabe o que
    # ainda precisa (ou não) ser limpo, sem depender de variáveis
    # inexistentes.
    sock: Optional[socket.socket] = None
    estado: Optional[EstadoCliente] = None
    thread: Optional[threading.Thread] = None
    evento_encerrando: Optional[threading.Event] = None

    try:
        sock = conectar(ip, args.porta)
        nome, senha, buffer_inicial = realizar_login(sock)

        evento_encerrando = threading.Event()
        estado = EstadoCliente()
        estado.ip = ip
        estado.porta = args.porta
        estado.nome = nome
        estado.senha = senha
        estado.sock = sock

        thread = threading.Thread(
            target=supervisionar_conexao,
            args=(estado, evento_encerrando, buffer_inicial),
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
                # servidor pode ter caído (e a reconexão desistido) enquanto o usuário digitava
                break

            acao, mensagem = parse_comando(texto)

            if acao in (ACAO_VAZIO, ACAO_INVALIDO):
                continue

            if acao == ACAO_SAIR:
                _info(f"encerrando ({COMANDO_SAIR})...")
                break

            # acao == ACAO_ENVIAR — lê o socket ATUAL (pode ter sido
            # trocado por uma reconexão automática em segundo plano)
            with estado.lock:
                sock_atual = estado.sock
            if not enviar(sock_atual, mensagem):
                # Não encerra a sessão por causa disso: se a queda foi
                # inesperada, supervisionar_conexao() já está cuidando
                # de reconectar em background (ou já desistiu e vai
                # encerrar o processo sozinha) — só avisamos e seguimos.
                continue

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
        if estado is not None and thread is not None and evento_encerrando is not None:
            # Sessão completa: login concluído, thread de supervisão rodando.
            encerrar(estado, evento_encerrando)
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