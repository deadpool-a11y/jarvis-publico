"""
Jarvis - funções principais: microfone, voz, IA (Ollama) e ações no PC.

Coloque este arquivo na MESMA pasta do jarvis_hud.py, jarvis_rotina.py,
jarvis_memoria.py, jarvis_voz_id.py e gerador_comandos.py.

Instalar (uma vez):
    pip install sounddevice SpeechRecognition edge-tts icalendar recurring-ical-events requests
    (as funções novas não pedem nenhum pacote extra)

Ollama (uma vez):
    ollama pull qwen2.5:7b
    (o Ollama precisa estar aberto/rodando)

Agendar por voz: "Jarvis, agendar dentista amanhã às 15 horas".
    - Com o script do Google configurado (veja jarvis_agenda_script.gs), ele grava o compromisso
      direto na agenda, sem você clicar em nada.
    - Sem o script, ele abre o Google Agenda com o evento preenchido para você clicar em Salvar.
    Variáveis (setx): JARVIS_AGENDA_SCRIPT (endereço do script) e JARVIS_AGENDA_SENHA (a senha).

NOVOS COMANDOS (todos começam com "Jarvis, ..."):
    Agenda (precisam do jarvis_agenda_script.gs NOVO publicado):
        "o que eu tenho amanhã" / "meus compromissos da semana" / "compromissos de hoje"
        "cancela o compromisso dentista"
        "muda o dentista para sexta às 10 horas"
        (e, sem pedir nada, ele avisa 10 minutos antes de cada compromisso)
    Dia a dia:
        "timer de 10 minutos" / "timer de uma hora e meia"
        "me acorda às 7 horas" / "alarme às 6 e 30" / "alarme amanhã às 8"
        "quanto falta no timer" / "quais alarmes eu tenho" / "cancela o timer" / "cancela o alarme"
        "anota comprar café" / "lê minhas anotações" / "limpa as anotações"
        "adiciona leite e pão na lista de compras" / "o que tem na lista de compras"
        "tira o leite da lista de compras" / "limpa a lista de compras"
        "boa noite" (para a música, diz os compromissos de amanhã e bloqueia o PC)
    Informações:
        "cotação do dólar" / "quanto está o euro" / "cotação do bitcoin" / "cotações"
        "como está o PC" / "como está a bateria"
        "que horas são" / "que dia é hoje"
    Computador:
        "tira um print da tela"   (salva em Imagens\\Jarvis Prints)
        "modo foco"               (fecha os programas de jarvis_dados\\foco.txt, pedindo confirmação)
    Comandos que o próprio Jarvis cria:
        Peça uma ação que ele ainda não sabe fazer (ex.: "Jarvis, abre a pasta de downloads").
        Ele diz "Criando comando", escreve o código sozinho (pasta comandos_auto) e diz
        "Novo comando criado, senhor". Da próxima vez o comando roda direto.

As anotações, a lista de compras, os alarmes e a lista do modo foco ficam na pasta jarvis_dados,
ao lado deste arquivo.

Trocar de modelo sem mexer no código:
    setx JARVIS_MODELO "llama3.1:8b"

Rodar sem a tela (teste):
    python jarvis_acoes.py

IMPORTANTE: o jarvis_hud.py troca algumas funções daqui (falar, escutar, perguntar,
calibrar, carregar_apps, volume, executar) por versões que avisam a tela. Por isso
tudo aqui dentro chama essas funções pelo nome, sem guardar cópias delas.
"""

import ctypes
from contextlib import contextmanager
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from ctypes import wintypes
from datetime import datetime, timedelta
from urllib.parse import quote, quote_plus, urlencode

import gerador_comandos  # comandos que o próprio Jarvis cria (pasta comandos_auto)

# ---- Só inicia depois que o PC for desbloqueado ----
def _pc_bloqueado() -> bool:
    """True se o Windows está na tela de bloqueio/login."""
    try:
        u = ctypes.windll.user32
        u.OpenInputDesktop.restype = ctypes.c_void_p
        u.SwitchDesktop.argtypes = [ctypes.c_void_p]
        u.CloseDesktop.argtypes = [ctypes.c_void_p]
        hdesk = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not hdesk:
            return True
        ok = u.SwitchDesktop(hdesk)
        u.CloseDesktop(hdesk)
        return not ok
    except Exception:
        return False  # se não conseguir checar, não trava o Jarvis


def esperar_desbloqueio():
    if not _pc_bloqueado():
        return
    print("PC bloqueado. O Jarvis inicia quando você desbloquear...")
    while _pc_bloqueado():
        time.sleep(1)
    time.sleep(3)  # dá tempo da área de trabalho carregar
    print("PC desbloqueado. Iniciando o Jarvis.\n")


esperar_desbloqueio()

# O HUD precisa que o Windows não "estique" a tela (DPI) antes de criar a janela
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------- Configurações (pode ajustar) ----------------
CHAMADO = re.compile(r"j[aá]r(?:vis|ves|vi|vez|bas)", re.I)  # como o reconhecedor costuma ouvir "Jarvis"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELO = os.environ.get("JARVIS_MODELO", "qwen2.5:7b")
MAX_APPS_NO_PROMPT = 250  # se o modelo ficar lento, reduza para 100
ESPERA_COMANDO = 12      # segundos esperando você falar depois do "Sim, senhor"
FIM_SILENCIO = 7        # blocos de 0,1 s de silêncio para considerar que a frase acabou (suba p/ 9-10 se cortar suas frases)
MAX_BLOCOS = 200         # frase mais longa: 20 segundos
LIMIAR_MINIMO = 350      # volume mínimo para considerar que é voz

LEMBRETES_LIGADOS = True   # avisa em voz alta antes dos compromissos
ANTECEDENCIA_LEMBRETE = 10  # minutos antes do compromisso
PLAYERS_PARA_FECHAR = []   # modo "boa noite": nomes de programas de música para fechar, ex.: ["vlc", "wmplayer"]
# --------------------------------------------------------------

TAXA = 16000
BLOCO = 1600             # 0,1 segundo de áudio
SEM_JANELA = 0x08000000  # não abre janela preta ao rodar comandos
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
if not os.path.exists(POWERSHELL):
    POWERSHELL = "powershell"

PASTA_DADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_dados")

ESTADO = {"limiar": float(LIMIAR_MINIMO), "cliente": None, "historico": deque(maxlen=6)}
NAVEGADOR = {"nome": "", "exe": ""}
MONITORES = []   # (esquerda, topo, direita, base) da área útil de cada monitor, da esquerda p/ direita
APPS = {}        # nome normalizado -> (nome, AppID)


class ErroJarvis(Exception):
    """Erro com mensagem amigável para o Jarvis falar."""


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


# ======================= VOZ (Windows) =======================
# Voz de reserva do Windows. O jarvis_hud usa a voz neural da rotina e só cai aqui se ela falhar.

SCRIPT_VOZ = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) {
    if ($v.VoiceInfo.Culture.Name -eq 'pt-BR') { $s.SelectVoice($v.VoiceInfo.Name); break }
}
$s.Rate = 0
$s.Speak($env:JARVIS_TEXTO)
'''


def falar(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return
    print("Jarvis:", texto, "\n")
    try:
        subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", SCRIPT_VOZ],
                       env={**os.environ, "JARVIS_TEXTO": texto},
                       capture_output=True, timeout=180, creationflags=SEM_JANELA)
    except Exception as erro:
        print(f"(Não consegui falar em voz alta: {erro})")


# ======================= NAVEGADOR E MONITORES =======================

def achar_navegador():
    """Descobre o navegador padrão do Windows."""
    exe = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as k:
            comando = winreg.QueryValueEx(k, "")[0]
        m = re.search(r'"([^"]+\.exe)"', comando) or re.match(r"(\S+\.exe)", comando)
        if m and os.path.exists(m.group(1)):
            exe = m.group(1)
    except Exception:
        pass
    base = os.path.basename(exe).lower()
    nome = ""
    for chave, rotulo in (("opera", "Opera"), ("chrome", "Chrome"), ("msedge", "Edge"),
                          ("firefox", "Firefox"), ("brave", "Brave")):
        if chave in base:
            nome = rotulo
            break
    NAVEGADOR["nome"] = nome or "padrão do Windows"
    NAVEGADOR["exe"] = exe
    print(f"Navegador: {NAVEGADOR['nome']}" + (f" ({exe})" if exe else "") + "\n")


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def detectar_monitores():
    MONITORES.clear()
    try:
        u = ctypes.WinDLL("user32")
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc, wintypes.LPARAM]

        def achou(hmon, hdc, rect, lparam):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                MONITORES.append((r.left, r.top, r.right, r.bottom))
            return True

        u.EnumDisplayMonitors(None, None, proc(achou), 0)
        MONITORES.sort(key=lambda m: (m[0], m[1]))
    except Exception as erro:
        print(f"(Não consegui detectar os monitores: {erro})")
    print(f"Monitores detectados: {len(MONITORES)}")
    for i, (l, t, r, b) in enumerate(MONITORES, 1):
        print(f"  Monitor {i}: de ({l}, {t}) até ({r}, {b})")
    print()


def mover_janela_ativa(numero: int) -> bool:
    """Move a janela que está em primeiro plano para o monitor pedido e maximiza."""
    if not 1 <= numero <= len(MONITORES):
        falar(f"Só detectei {len(MONITORES)} monitores, senhor.")
        return False
    l, t, r, b = MONITORES[numero - 1]
    u = ctypes.WinDLL("user32")
    u.GetForegroundWindow.restype = wintypes.HWND
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return False
    u.ShowWindow(hwnd, 9)  # restaurar (sai do maximizado)
    u.SetWindowPos(hwnd, None, l + 40, t + 40, (r - l) - 80, (b - t) - 80, 0x0004 | 0x0010)
    u.ShowWindow(hwnd, 3)  # maximizar já no monitor novo
    print(f"[ação] movendo a janela para o monitor {numero}")
    return True


# ======================= APLICATIVOS INSTALADOS =======================

def carregar_apps():
    print("Carregando a lista de aplicativos instalados...")
    APPS.clear()
    comando = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", comando],
                           capture_output=True, timeout=90, creationflags=SEM_JANELA)
        dados = json.loads(r.stdout.decode("utf-8", errors="ignore") or "[]")
        if isinstance(dados, dict):
            dados = [dados]
        for d in dados:
            nome = (d.get("Name") or "").strip()
            appid = (d.get("AppID") or "").strip()
            if nome and appid:
                APPS.setdefault(normalizar(nome), (nome, appid))
    except Exception as erro:
        print(f"(Não consegui ler a lista de aplicativos: {erro})")
    print(f"{len(APPS)} aplicativos encontrados.\n")


def achar_app(nome: str):
    alvo = normalizar(nome)
    if not alvo or not APPS:
        return None
    if alvo in APPS:
        return APPS[alvo]
    for chave, valor in APPS.items():
        if len(chave) >= 3 and (alvo in chave or chave in alvo):
            return valor
    parecido = difflib.get_close_matches(alvo, list(APPS), n=1, cutoff=0.6)
    return APPS[parecido[0]] if parecido else None


# ======================= MICROFONE =======================

def _sounddevice():
    try:
        import sounddevice
        return sounddevice
    except ImportError:
        raise ErroJarvis("Falta instalar o microfone: pip install sounddevice")


def volume(bloco: bytes) -> float:
    """Volume (RMS) de um pedaço de áudio de 16 bits."""
    from array import array
    amostras = array("h")
    amostras.frombytes(bloco[: len(bloco) // 2 * 2])
    if not amostras:
        return 0.0
    return (sum(a * a for a in amostras) / len(amostras)) ** 0.5


def calibrar() -> float:
    """Mede o ruído do ambiente por 1 segundo e define o limite de voz."""
    print("Calibrando o ruído do ambiente... fique em silêncio por 1 segundo.")
    sd = _sounddevice()
    niveis = []
    with sd.RawInputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO) as mic:
        for _ in range(10):
            dados, _ = mic.read(BLOCO)
            niveis.append(volume(bytes(dados)))
    ruido = sum(niveis) / len(niveis)
    limiar = max(float(LIMIAR_MINIMO), ruido * 3)
    ESTADO["limiar"] = limiar
    print(f"Ruído do ambiente: {int(ruido)} | Limite de voz: {int(limiar)}\n")
    return limiar


def _gravar(limiar: float, espera_max: float) -> bytes:
    """Espera você falar e grava até a frase acabar. Devolve b'' se ninguém falou a tempo."""
    sd = _sounddevice()
    anteriores = deque(maxlen=3)   # guarda 0,3 s antes de você começar, para não cortar a primeira sílaba
    gravado = []
    falando = False
    fortes = 0
    silencio = 0
    inicio = time.time()
    with sd.RawInputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO) as mic:
        while True:
            dados, _ = mic.read(BLOCO)
            bloco = bytes(dados)
            nivel = volume(bloco)  # o HUD troca esta função para animar a tela

            # Alarme/timer/lembrete tocando: ignora o som e recomeça a escuta do zero
            if time.time() < ESTADO.get("alerta_ate", 0):
                anteriores.clear()
                gravado = []
                falando = False
                fortes = 0
                silencio = 0
                continue

            if not falando:
                anteriores.append(bloco)
                fortes = fortes + 1 if nivel > limiar else 0
                if fortes >= 2:  # 0,2 s seguidos de som alto = fala
                    falando = True
                    gravado = list(anteriores)
                    silencio = 0
                elif espera_max and time.time() - inicio > espera_max:
                    return b""
            else:
                gravado.append(bloco)
                silencio = 0 if nivel > limiar * 0.6 else silencio + 1
                if silencio >= FIM_SILENCIO or len(gravado) >= MAX_BLOCOS:
                    return b"".join(gravado)


def _transcrever(audio: bytes) -> str:
    try:
        import speech_recognition as sr
    except ImportError:
        raise ErroJarvis("Falta instalar o reconhecimento de voz: pip install SpeechRecognition")
    reconhecedor = sr.Recognizer()
    reconhecedor.operation_timeout = 12
    try:
        return reconhecedor.recognize_google(sr.AudioData(audio, TAXA, 2), language="pt-BR").strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as erro:
        print(f"(Sem conexão com o reconhecimento de voz: {erro})")
        time.sleep(2)
        return ""


def escutar(limiar: float, espera_max: float = 0) -> str:
    """espera_max = 0: fica em espera para sempre. espera_max > 0: desiste se ninguém falar a tempo."""
    audio = _gravar(limiar, espera_max)
    if not audio:
        return ""
    texto = _transcrever(audio)
    if texto:
        print(f"[ouvi] {texto}")
    return texto


def perguntar_sim_nao(pergunta: str):
    """Pergunta em voz alta. Devolve True (sim), False (não) ou None (não entendi)."""
    falar(pergunta)
    resposta = escutar(ESTADO["limiar"], ESPERA_COMANDO)
    palavras = set(re.findall(r"[a-z]+", normalizar(resposta)))
    if palavras & {"nao", "cancela", "cancelar", "negativo", "deixa", "pare", "para"}:
        return False
    if palavras & {"sim", "pode", "confirmo", "claro", "positivo", "isso", "vai", "continua",
                   "continue", "certo", "ok", "afirmativo"}:
        return True
    return None


# ======================= IA (OLLAMA) =======================

SISTEMA = """Você é o JARVIS, o assistente do filme Homem de Ferro, falando português do Brasil.
Trate o usuário por "senhor". Suas respostas são FALADAS em voz alta: no máximo 2 frases curtas,
sem markdown, sem listas, sem emojis, sem símbolos.
Agora são: {AGORA}.

Responda SEMPRE e SOMENTE com um JSON neste formato:
{"fala": "texto falado", "acoes": [ ... ]}

Se o pedido for só uma conversa ou pergunta, preencha "fala" e deixe "acoes" vazio.
Se o pedido for uma ação no computador, deixe "fala" vazio ("") e preencha "acoes"
(o sistema já avisa "Executado com sucesso" sozinho).

Tipos de ação:
{"tipo": "abrir_site", "valor": "https://www.exemplo.com"}
{"tipo": "pesquisar_google", "valor": "o que pesquisar"}
{"tipo": "pesquisar_youtube", "valor": "o que pesquisar"}
{"tipo": "abrir_app", "valor": "nome do aplicativo"}
{"tipo": "volume", "valor": "subir" | "descer" | "mudo", "vezes": 5}
{"tipo": "midia", "valor": "pausar" | "proxima" | "anterior"}
{"tipo": "agendar", "titulo": "Dentista", "inicio": "AAAA-MM-DD HH:MM", "duracao": 60}
   (cria um compromisso na agenda; duracao em minutos; para o dia inteiro use "inicio": "AAAA-MM-DD"
    sem hora; calcule "amanhã", "sexta" etc. a partir de "Agora são"; "fala" vazio)
{"tipo": "cancelar_compromisso", "titulo": "Dentista", "data": "AAAA-MM-DD"}
   (cancela um compromisso da agenda; "data" é o dia dele e é opcional; "fala" vazio)
{"tipo": "mudar_compromisso", "titulo": "Dentista", "data": "AAAA-MM-DD", "inicio": "AAAA-MM-DD HH:MM", "duracao": 60}
   (remarca um compromisso existente; "data" = dia atual dele (opcional); "inicio" = NOVO dia e hora;
    "duracao" é opcional; calcule datas relativas a partir de "Agora são"; "fala" vazio)
{"tipo": "agenda_dia", "valor": "hoje" | "amanha" | "semana"}   (lê os compromissos)
{"tipo": "timer", "segundos": 600}
{"tipo": "alarme", "hora": "07:00", "amanha": false}
{"tipo": "listar_alarmes"}
{"tipo": "cancelar_alarmes", "valor": "timer" | "alarme" | "todos"}
{"tipo": "anotar", "texto": "comprar café"}
{"tipo": "ler_notas"}
{"tipo": "limpar_notas"}
{"tipo": "compras_add", "itens": ["café", "leite"]}
{"tipo": "compras_remover", "itens": ["leite"]}
{"tipo": "compras_ler"}
{"tipo": "compras_limpar"}
{"tipo": "hora_data"}
{"tipo": "estado_pc"}                  (uso de processador, memória, disco e bateria)
{"tipo": "print"}                      (tira print da tela)
{"tipo": "modo_foco"}                  (fecha navegador e jogos da lista de foco)
{"tipo": "boa_noite"}                  (para a música, diz os compromissos de amanhã e bloqueia o PC)
{"tipo": "cotacao", "valor": "dolar" | "euro" | "bitcoin" | "todas"}
{"tipo": "spotify", "valor": "música ou artista"}   (toca no Spotify do navegador)
{"tipo": "mover_janela", "valor": 2}   (move a janela ativa para o monitor 1 ou 2...)
{"tipo": "encerrar"}                   (só se o senhor mandar desligar/encerrar o Jarvis; "fala" vazio)
{"tipo": "criar_comando", "pedido": "o que o senhor pediu"}   (use SOMENTE para uma ação no computador que NENHUMA outra ação acima consegue fazer; "fala" vazio)
Qualquer ação de abrir pode ter também "monitor": 2 para levar a janela nova para esse monitor.

{"tipo": "ler_mensagens", "valor": "todas" | "whatsapp" | "discord" | "gmail" | "instagram"}
{"tipo": "ligar_whatsapp", "nome": "Maria", "video": false}
{"tipo": "atender_chamada"}
{"tipo": "recusar_chamada"}
{"tipo": "encerrar_chamada"}

Aplicativos instalados (use o nome exato em abrir_app): {APPS}
Monitores disponíveis: {MONITORES}
"""


DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def _sistema() -> str:
    apps = ", ".join(nome for nome, _ in list(APPS.values())[:MAX_APPS_NO_PROMPT]) or "(nenhum)"
    return (SISTEMA.replace("{AGORA}", DIAS_SEMANA[datetime.now().weekday()]
                           + ", " + datetime.now().strftime("%d/%m/%Y, %H:%M"))
            .replace("{APPS}", apps).replace("{MONITORES}", str(max(1, len(MONITORES)))))


def _gerar(prompt: str) -> str:
    corpo = {
        "model": MODELO,
        "messages": [
            {"role": "system", "content": _sistema()},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",          # obriga a resposta a ser JSON
        "keep_alive": "30m",       # mantém o modelo na memória (respostas mais rápidas)
        "options": {"temperature": 0.4, "num_ctx": 8192},
    }
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            raise ErroJarvis(f"O modelo {MODELO} não está instalado no Ollama, senhor. "
                             f"Rode ollama pull {MODELO}.")
        raise
    except urllib.error.URLError:
        raise ErroJarvis("Não consegui falar com o Ollama, senhor. Verifique se ele está aberto.")
    except TimeoutError:
        raise ErroJarvis("O Ollama demorou demais para responder, senhor.")
    return (dados.get("message") or {}).get("content", "")


def _ler_json(bruto: str) -> dict:
    texto = re.sub(r"^```(?:json)?|```$", "", (bruto or "").strip(), flags=re.M).strip()
    try:
        dados = json.loads(texto)
        if isinstance(dados, list):
            dados = dados[0] if dados else {}
        if isinstance(dados, dict):
            return dados
        return {"fala": str(dados), "acoes": []}
    except Exception:
        return {"fala": texto, "acoes": []}


def perguntar(texto: str) -> dict:
    """Manda o pedido ao Ollama e devolve {"fala": ..., "acoes": [...]}.
    Os comandos simples (timer, anotação, hora...) são resolvidos antes, sem usar o Ollama."""
    try:
        local = _comando_local(texto)
    except Exception as erro:
        print(f"[erro] comando local: {erro}")
        local = None
    if local is not None:
        ESTADO["historico"].append((texto, "Feito."))
        return local
    historico = "\n".join(f"Senhor: {p}\nJarvis: {r}" for p, r in ESTADO["historico"])
    prompt = (f"Conversa recente:\n{historico}\n\n" if historico else "") + f"Pedido do senhor: {texto}"
    resposta = _ler_json(_gerar(prompt))
    ESTADO["historico"].append((texto, str(resposta.get("fala") or "")))
    return resposta


# ======================= AÇÕES NO PC =======================

def _abrir_url(url: str):
    if NAVEGADOR["exe"]:
        subprocess.Popen([NAVEGADOR["exe"], url])
    else:
        webbrowser.open(url)


def _tecla(vk: int, vezes: int = 1):
    u = ctypes.windll.user32
    for _ in range(vezes):
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)
        time.sleep(0.02)


def _data_agenda(texto: str):
    """Aceita 'AAAA-MM-DD HH:MM' ou 'AAAA-MM-DD'. Devolve (datetime, dia_inteiro) ou None."""
    texto = str(texto or "").strip().replace("T", " ")
    for formato, inteiro in (("%Y-%m-%d %H:%M", False), ("%Y-%m-%d %H:%M:%S", False),
                             ("%Y-%m-%d", True)):
        try:
            return datetime.strptime(texto, formato), inteiro
        except ValueError:
            continue
    return None


def _variavel(nome: str) -> str:
    """Lê uma variável de ambiente (e, se não achar, direto do registro do Windows, onde o setx guarda)."""
    valor = os.environ.get(nome, "").strip()
    if not valor:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                valor = str(winreg.QueryValueEx(k, nome)[0]).strip()
        except Exception:
            valor = ""
    return valor


def _agendar_direto(titulo: str, inicio: datetime, inteiro: bool, duracao: int):
    """Grava o compromisso na agenda pelo script do Google. Devolve True, False ou None (não configurado)."""
    base = _variavel("JARVIS_AGENDA_SCRIPT")
    senha = _variavel("JARVIS_AGENDA_SENHA")
    if not base or not senha:
        return None
    parametros = urlencode({
        "token": senha,
        "titulo": titulo,
        "inicio": inicio.strftime("%Y-%m-%d") if inteiro else inicio.strftime("%Y-%m-%d %H:%M"),
        "duracao": duracao,
        "dia_todo": "1" if inteiro else "0",
    })
    url = base + ("&" if "?" in base else "?") + parametros
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            dados = json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as erro:
        print(f"[erro] não consegui falar com o script da agenda: {erro}")
        return False
    if dados.get("ok"):
        return True
    print(f"[erro] o script da agenda recusou: {dados.get('erro')}")
    return False


def _agendar(acao: dict) -> bool:
    """Cria o compromisso: direto na agenda (se configurado) ou abrindo o Google Agenda para salvar."""
    titulo = str(acao.get("titulo") or acao.get("valor") or "Compromisso").strip()
    quando = _data_agenda(acao.get("inicio"))
    if not quando:
        falar("Não entendi a data do compromisso, senhor. Diga o dia e a hora de novo.")
        return False
    inicio, inteiro = quando
    try:
        duracao = max(5, min(24 * 60, int(acao.get("duracao") or 60)))
    except (TypeError, ValueError):
        duracao = 60
    quando_fala = inicio.strftime("%d/%m") + ("" if inteiro else inicio.strftime(" às %H:%M"))
    print(f"[ação] agendando: {titulo} em {inicio:%d/%m/%Y %H:%M}" + (" (dia todo)" if inteiro else ""))

    gravado = _agendar_direto(titulo, inicio, inteiro, duracao)
    if gravado:
        EVENTOS["ultima"] = 0  # força o lembrete a reler a agenda
        falar(f"Pronto, senhor. Agendei {titulo} para {quando_fala}.")
        return True

    # Plano B: abre o Google Agenda com tudo preenchido (é só clicar em Salvar)
    if inteiro:
        fim = inicio + timedelta(days=1)
        datas = inicio.strftime("%Y%m%d") + "/" + fim.strftime("%Y%m%d")
    else:
        fim = inicio + timedelta(minutes=duracao)
        datas = inicio.strftime("%Y%m%dT%H%M%S") + "/" + fim.strftime("%Y%m%dT%H%M%S")
    _abrir_url("https://calendar.google.com/calendar/render?action=TEMPLATE"
               f"&text={quote_plus(titulo)}&dates={datas}")
    if gravado is False:
        falar(f"Não consegui gravar direto na agenda, senhor. Abri {titulo} para {quando_fala}; "
              "clique em Salvar.")
    else:
        falar(f"Abri o compromisso {titulo} para {quando_fala} na sua agenda, senhor. "
              "Clique em Salvar para confirmar.")
    return True


# ======================= FUNÇÕES NOVAS: APOIO =======================

def _pasta_dados() -> str:
    os.makedirs(PASTA_DADOS, exist_ok=True)
    return PASTA_DADOS


def _arquivo(nome: str) -> str:
    return os.path.join(_pasta_dados(), nome)


def _juntar(itens) -> str:
    itens = [str(i) for i in itens]
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _fala_hora(dt: datetime) -> str:
    h, m = dt.hour, dt.minute
    if h == 0 and m == 0:
        return "meia-noite"
    if m == 0:
        return f"{h} hora" + ("" if h == 1 else "s")
    return f"{h} e {m}"


def _fala_duracao(seg: float) -> str:
    seg = int(round(seg))
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    partes = []
    if h:
        partes.append(f"{h} hora" + ("s" if h > 1 else ""))
    if m:
        partes.append(f"{m} minuto" + ("s" if m > 1 else ""))
    if s and not h:
        partes.append(f"{s} segundo" + ("s" if s > 1 else ""))
    return " e ".join(partes) or "0 segundos"


def _rotulo_dia(d) -> str:
    hoje = datetime.now().date()
    if d == hoje:
        return "hoje"
    if d == hoje + timedelta(days=1):
        return "amanhã"
    if 0 < (d - hoje).days <= 6:
        return DIAS_SEMANA[d.weekday()]
    return f"dia {d:%d/%m}"


def _quando_fala(dt: datetime, inteiro: bool = False) -> str:
    return _rotulo_dia(dt.date()) + ("" if inteiro else " às " + _fala_hora(dt))


# ---- números falados ("dez", "vinte e cinco") para dígitos, só para entender horas e tempos ----

_UNID = {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
         "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
         "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16,
         "dezessete": 17, "dezoito": 18, "dezenove": 19}
_DEZ = {"vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60}


def _numeros_para_digitos(t: str) -> str:
    palavras = t.split()
    saida, i = [], 0
    while i < len(palavras):
        p = palavras[i]
        if p in _DEZ:
            v = _DEZ[p]
            if (i + 2 < len(palavras) and palavras[i + 1] == "e"
                    and palavras[i + 2] in _UNID and 1 <= _UNID[palavras[i + 2]] <= 9):
                v += _UNID[palavras[i + 2]]
                i += 2
            saida.append(str(v))
        elif p in _UNID:
            saida.append(str(_UNID[p]))
        else:
            saida.append(p)
        i += 1
    return " ".join(saida)


def _duracao_segundos(t: str) -> int:
    t = _numeros_para_digitos(t)
    total, achou = 0.0, False
    for n, u in re.findall(r"(\d+(?:[.,]\d+)?)\s*(segundos?|seg|minutos?|min|horas?|h)\b", t):
        n = float(n.replace(",", "."))
        total += n * (1 if u.startswith("seg") else 60 if u.startswith("min") else 3600)
        achou = True
    if re.search(r"\bmeia hora\b", t):
        total += 1800
        achou = True
    elif re.search(r"\b\d+ horas? e meia\b", t):
        total += 1800
        achou = True
    return int(total) if achou else 0


def _hora_do_texto(t: str):
    """Acha 'às 7 horas', '7:30', '7h30', 'às 6 e 30', 'às 7 e meia'. Devolve (hora, minuto) ou None."""
    t = _numeros_para_digitos(t)
    m = (re.search(r"\b(\d{1,2})\s*(?::|h(?:oras?)?)\s*(?:e\s*)?(\d{1,2}|meia)?\b", t)
         or re.search(r"\b(?:as|para as)\s+(\d{1,2})(?:\s+e\s+(\d{1,2}|meia))?\b", t))
    if not m:
        return None
    h = int(m.group(1))
    mi = m.group(2)
    mi = 30 if mi == "meia" else int(mi) if mi else 0
    if re.search(r"\b(tarde|noite)\b", t) and h < 12:
        h += 12
    if h > 23 or mi > 59:
        return None
    return h, mi


# ======================= TIMER E ALARME =======================

TAREFAS = []   # {"id", "tipo": "timer"|"alarme", "quando": epoch, "rotulo"}
_TRAVA = threading.Lock()
SERVICOS = {"iniciado": False}


def _salvar_tarefas():
    try:
        with open(_arquivo("alarmes.json"), "w", encoding="utf-8") as f:
            json.dump(TAREFAS, f, ensure_ascii=False)
    except Exception as erro:
        print(f"(Não consegui salvar os alarmes: {erro})")


def _carregar_tarefas():
    try:
        with open(_arquivo("alarmes.json"), encoding="utf-8") as f:
            dados = json.load(f)
        with _TRAVA:
            TAREFAS.clear()
            TAREFAS.extend(t for t in dados if isinstance(t, dict) and t.get("quando", 0) > time.time())
    except Exception:
        pass


def _nova_tarefa(tipo: str, quando: float, rotulo: str):
    with _TRAVA:
        TAREFAS.append({"id": int(time.time() * 1000), "tipo": tipo, "quando": quando, "rotulo": rotulo})
        _salvar_tarefas()


def _bipar(vezes: int = 3):
    try:
        import winsound
        for _ in range(vezes):
            winsound.Beep(1200, 300)
            time.sleep(0.15)
    except Exception:
        pass


@contextmanager
def _mic_surdo():
    """Enquanto o alarme toca, o microfone ignora o som (para não gravar o próprio alarme)."""
    ESTADO["alerta_ate"] = time.time() + 120
    try:
        yield
    finally:
        ESTADO["alerta_ate"] = time.time() + 1.5  # folga para o eco acabar


def _disparar(tarefa: dict):
    if tarefa["tipo"] == "timer":
        msg = f"Senhor, o timer de {tarefa['rotulo']} terminou."
        repeticoes = 1
    else:
        msg = f"Senhor, o alarme das {tarefa['rotulo']} está tocando."
        repeticoes = 2
    print(f"[alerta] {msg}")
    with _mic_surdo():
        for _ in range(repeticoes):
            _bipar(2)
            falar(msg)


def _acao_timer(a: dict):
    try:
        seg = int(float(a.get("segundos") or 0))
    except (TypeError, ValueError):
        seg = 0
    if seg <= 0:
        falar("Não entendi o tempo do timer, senhor. Diga, por exemplo, timer de dez minutos.")
        return "mudo"
    seg = min(seg, 24 * 3600)
    rotulo = _fala_duracao(seg)
    _nova_tarefa("timer", time.time() + seg, rotulo)
    print(f"[ação] timer de {rotulo}")
    falar(f"Timer de {rotulo} iniciado, senhor.")
    return "mudo"


def _acao_alarme(a: dict):
    m = re.match(r"\s*(\d{1,2})\s*[:h]\s*(\d{1,2})?", str(a.get("hora") or ""))
    h = int(m.group(1)) if m else -1
    mi = int(m.group(2) or 0) if m else -1
    if not m or h > 23 or mi > 59:
        falar("Não entendi a hora do alarme, senhor. Diga, por exemplo, alarme às sete e meia.")
        return "mudo"
    agora = datetime.now()
    alvo = agora.replace(hour=h, minute=mi, second=0, microsecond=0)
    if str(a.get("amanha")).lower() in ("true", "1", "sim") and alvo.date() == agora.date():
        alvo += timedelta(days=1)
    if alvo <= agora:
        alvo += timedelta(days=1)
    _nova_tarefa("alarme", alvo.timestamp(), _fala_hora(alvo))
    print(f"[ação] alarme para {alvo:%d/%m %H:%M}")
    falar(f"Alarme marcado para {_quando_fala(alvo)}, senhor.")
    return "mudo"


def _acao_listar_alarmes(a: dict):
    with _TRAVA:
        itens = sorted(TAREFAS, key=lambda t: t["quando"])
    if not itens:
        falar("O senhor não tem timers nem alarmes ativos.")
        return "mudo"
    partes = []
    for t in itens:
        if t["tipo"] == "timer":
            partes.append(f"timer com {_fala_duracao(max(1, t['quando'] - time.time()))} restantes")
        else:
            partes.append(f"alarme {_quando_fala(datetime.fromtimestamp(t['quando']))}")
    falar("O senhor tem: " + _juntar(partes) + ".")
    return "mudo"


def _acao_cancelar_alarmes(a: dict):
    alvo = normalizar(str(a.get("valor") or "todos"))
    tipo = "timer" if "timer" in alvo else "alarme" if "alarme" in alvo else None
    with _TRAVA:
        antes = len(TAREFAS)
        TAREFAS[:] = [t for t in TAREFAS if tipo and t["tipo"] != tipo]
        removidos = antes - len(TAREFAS)
        _salvar_tarefas()
    if not removidos:
        falar("Não havia nada para cancelar, senhor.")
    else:
        nome = {"timer": "timer", "alarme": "alarme"}.get(tipo, "timer ou alarme")
        falar(f"{removidos} {nome}{'s' if removidos > 1 else ''} cancelado{'s' if removidos > 1 else ''}, senhor."
              if tipo else f"Cancelei tudo, senhor: {removidos} no total.")
    return "mudo"


# ======================= AGENDA (script do Google) =======================

MSG_SEM_SCRIPT = ("Para isso preciso do script da agenda configurado, senhor. "
                  "Veja o arquivo jarvis_agenda_script.gs.")
EVENTOS = {"lista": [], "avisados": set(), "ultima": 0.0}


def _agenda_configurada() -> bool:
    return bool(_variavel("JARVIS_AGENDA_SCRIPT") and _variavel("JARVIS_AGENDA_SENHA"))


def _agenda_script(**parametros):
    """Chama o script do Google. Devolve o JSON, None (não configurado) ou {'ok': False} se falhar."""
    base = _variavel("JARVIS_AGENDA_SCRIPT")
    senha = _variavel("JARVIS_AGENDA_SENHA")
    if not base or not senha:
        return None
    parametros["token"] = senha
    url = base + ("&" if "?" in base else "?") + urlencode(parametros)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as erro:
        print(f"[erro] script da agenda: {erro}")
        return {"ok": False, "erro": str(erro)}


def _listar_eventos(de, ate):
    """Compromissos entre as datas (inclusive). Devolve lista ou None se não deu para ler."""
    r = _agenda_script(acao="listar", de=de.strftime("%Y-%m-%d"), ate=ate.strftime("%Y-%m-%d"))
    if not r or not r.get("ok"):
        if r:
            print(f"[erro] não consegui listar a agenda: {r.get('erro')}")
        return None
    eventos = []
    for e in r.get("eventos") or []:
        q = _data_agenda(e.get("inicio"))
        if not q:
            continue
        fim = _data_agenda(e.get("fim"))
        eventos.append({"id": str(e.get("id") or ""), "titulo": str(e.get("titulo") or "Compromisso"),
                        "inicio": q[0], "dia_todo": q[1] or bool(e.get("dia_todo")),
                        "fim": fim[0] if fim else None})
    eventos = [e for e in eventos if de <= e["inicio"].date() <= ate]
    eventos.sort(key=lambda e: e["inicio"])
    return eventos


def _texto_agenda(quando: str):
    """Texto falado dos compromissos ('hoje', 'amanha' ou 'semana'). None se o script não está configurado."""
    if not _agenda_configurada():
        return None
    hoje = datetime.now().date()
    um = timedelta(days=1)
    de, ate = {"hoje": (hoje, hoje), "amanha": (hoje + um, hoje + um),
               "semana": (hoje, hoje + 6 * um)}[quando]
    eventos = _listar_eventos(de, ate)
    if eventos is None:
        return "Não consegui ler a agenda agora, senhor."
    agora = datetime.now()
    if quando == "hoje":
        eventos = [e for e in eventos if e["dia_todo"] or (e["fim"] or e["inicio"]) >= agora]

    def item(e):
        return e["titulo"] if e["dia_todo"] else f"{e['titulo']} às {_fala_hora(e['inicio'])}"

    if quando in ("hoje", "amanha"):
        rotulo = "Hoje" if quando == "hoje" else "Amanhã"
        if not eventos:
            return f"{rotulo} o senhor não tem {'mais ' if quando == 'hoje' else ''}compromissos."
        n = len(eventos)
        return f"{rotulo} o senhor tem {n} compromisso{'s' if n > 1 else ''}: " + "; ".join(map(item, eventos)) + "."
    if not eventos:
        return "O senhor não tem compromissos nos próximos sete dias."
    dias = {}
    for e in eventos:
        dias.setdefault(e["inicio"].date(), []).append(e)
    partes = [f"{_rotulo_dia(d).capitalize()}: " + ", ".join(map(item, dias[d])) for d in sorted(dias)]
    return "Nos próximos sete dias: " + ". ".join(partes) + "."


def _acao_agenda_dia(a: dict):
    v = normalizar(str(a.get("valor") or "hoje"))
    quando = "amanha" if "amanha" in v else "semana" if ("semana" in v or "proximos" in v) else "hoje"
    texto = _texto_agenda(quando)
    falar(texto or MSG_SEM_SCRIPT)
    return "mudo"


def _data_param(ev) -> str:
    return ev["inicio"].strftime("%Y-%m-%d") if ev["dia_todo"] else ev["inicio"].strftime("%Y-%m-%d %H:%M")


def _achar_evento(titulo: str, data=None):
    """Procura o compromisso que mais parece com o título falado (próximos 90 dias)."""
    hoje = datetime.now().date()
    eventos = _listar_eventos(hoje, hoje + timedelta(days=90))
    if not eventos:
        return None
    alvo = normalizar(titulo)

    def pontos(e):
        n = normalizar(e["titulo"])
        if alvo and (alvo in n or n in alvo):
            return 1.0
        return difflib.SequenceMatcher(None, alvo, n).ratio()

    cands = [(pontos(e), e) for e in eventos]
    cands = [c for c in cands if c[0] >= 0.5]
    if not cands:
        return None
    dia = _data_agenda(data)
    if dia:
        no_dia = [c for c in cands if c[1]["inicio"].date() == dia[0].date()]
        cands = no_dia or cands
    melhor = max(p for p, _ in cands)
    return sorted((e for p, e in cands if p >= melhor - 0.05), key=lambda e: e["inicio"])[0]


def _evento_mais_proximo():
    """O compromisso mais perto do horário atual: começando agora, começou há até 45 min, ou o próximo."""
    ult = ESTADO.get("ultimo_evento_avisado")
    if ult and _chave_evento(ult) not in DISPENSADOS \
            and -60 <= (ult["inicio"] - datetime.now()).total_seconds() / 60 <= 60:
        return ult   # o compromisso que o Jarvis acabou de avisar
    hoje = datetime.now().date()
    eventos = _listar_eventos(hoje, hoje + timedelta(days=1))
    if not eventos:
        return None
    agora = datetime.now()
    candidatos = [e for e in eventos if not e["dia_todo"] and e["inicio"] >= agora - timedelta(minutes=45)]
    if not candidatos:
        return None
    return min(candidatos, key=lambda e: abs((e["inicio"] - agora).total_seconds()))


def _acao_cancelar_compromisso(a: dict):
    if not _agenda_configurada():
        falar(MSG_SEM_SCRIPT)
        return "mudo"
    titulo = str(a.get("titulo") or a.get("valor") or "").strip()
    if not titulo:
        ev = _evento_mais_proximo()
        if not ev:
            falar("Não encontrei nenhum compromisso por perto, senhor. Diga o nome dele.")
            return "mudo"
    else:
        ev = _achar_evento(titulo, a.get("data"))
    if not ev:
        falar(f"Não encontrei nenhum compromisso parecido com {titulo}, senhor.")
        return "mudo"
    resp = perguntar_sim_nao(f"Cancelo {ev['titulo']}, {_quando_fala(ev['inicio'], ev['dia_todo'])}, senhor?")
    if resp is not True:
        falar("Certo, mantive o compromisso.")
        return "mudo"
    _dispensar_evento(ev)
    r = _agenda_script(acao="cancelar", id=ev["id"], inicio_original=_data_param(ev))
    if r and r.get("ok"):
        EVENTOS["ultima"] = 0
        print(f"[agenda] resposta do Google: {r}")
        time.sleep(1)
        restante = _listar_eventos(ev["inicio"].date(), ev["inicio"].date())
        ainda = restante is not None and any(
            x["id"] == ev["id"] and x["inicio"] == ev["inicio"] for x in restante)
        if ainda:
            print("[erro] o Google respondeu ok, mas o compromisso ainda aparece na agenda")
            falar("O Google disse que apagou, mas o compromisso ainda aparece na agenda, senhor.")
        else:
            print(f"[ação] compromisso removido da agenda: {ev['titulo']} {ev['inicio']:%d/%m %H:%M}")
            falar(f"Pronto, senhor. Cancelei {ev['titulo']}.")
    else:
        print(f"[erro] cancelar: {(r or {}).get('erro')}")
        falar("Não consegui cancelar o compromisso, senhor.")
    return "mudo"


def _acao_mudar_compromisso(a: dict):
    if not _agenda_configurada():
        falar(MSG_SEM_SCRIPT)
        return "mudo"
    titulo = str(a.get("titulo") or a.get("valor") or "").strip()
    if not titulo:
        falar("Qual compromisso devo mudar, senhor?")
        return "mudo"
    novo = _data_agenda(a.get("inicio"))
    if not novo:
        falar("Para quando devo mudar, senhor? Diga o novo dia e a hora.")
        return "mudo"
    novo_inicio, novo_inteiro = novo
    ev = _achar_evento(titulo, a.get("data"))
    if not ev:
        falar(f"Não encontrei nenhum compromisso parecido com {titulo}, senhor.")
        return "mudo"
    resp = perguntar_sim_nao(f"Mudo {ev['titulo']} de {_quando_fala(ev['inicio'], ev['dia_todo'])} "
                             f"para {_quando_fala(novo_inicio, novo_inteiro)}, senhor?")
    if resp is not True:
        falar("Certo, deixei como estava.")
        return "mudo"
    _dispensar_evento(ev)
    params = {"acao": "mudar", "id": ev["id"], "inicio_original": _data_param(ev),
              "inicio": novo_inicio.strftime("%Y-%m-%d") if novo_inteiro else novo_inicio.strftime("%Y-%m-%d %H:%M")}
    try:
        if a.get("duracao"):
            params["duracao"] = max(5, int(a["duracao"]))
    except (TypeError, ValueError):
        pass
    r = _agenda_script(**params)
    if r and r.get("ok"):
        EVENTOS["ultima"] = 0
        falar(f"Pronto, senhor. Remarquei {ev['titulo']} para {_quando_fala(novo_inicio, novo_inteiro)}.")
    else:
        print(f"[erro] mudar: {(r or {}).get('erro')}")
        falar("Não consegui remarcar o compromisso, senhor.")
    return "mudo"


# ---- lembrete falado antes dos compromissos ----

def _atualizar_eventos():
    EVENTOS["ultima"] = time.time()
    if not _agenda_configurada():
        return
    hoje = datetime.now().date()
    eventos = _listar_eventos(hoje, hoje + timedelta(days=1))
    if eventos is not None:
        EVENTOS["lista"] = [e for e in eventos if not e["dia_todo"]]


ATRASOS_AVISO = (0, 5, 15)   # minutos depois do início em que o Jarvis avisa que o senhor está atrasado
DISPENSADOS = set()          # compromissos que o senhor já dispensou ("ok") ou cancelou/remarcou


def _chave_evento(e) -> str:
    return e["id"] + e["inicio"].strftime("%Y%m%d%H%M")


def _carregar_dispensados():
    try:
        with open(_arquivo("dispensados.json"), encoding="utf-8") as f:
            DISPENSADOS.update(json.load(f))
    except Exception:
        pass


def _salvar_dispensados():
    try:
        with open(_arquivo("dispensados.json"), "w", encoding="utf-8") as f:
            json.dump(sorted(DISPENSADOS, key=lambda k: k[-12:])[-200:], f)
    except Exception as erro:
        print(f"(Não consegui salvar os avisos dispensados: {erro})")


def _tirar_dispensados_da_lista():
    EVENTOS["lista"] = [x for x in EVENTOS["lista"] if _chave_evento(x) not in DISPENSADOS]


def _dispensar_evento(e):
    """Esse compromisso nunca mais gera aviso (usado ao cancelar/remarcar)."""
    DISPENSADOS.add(_chave_evento(e))
    _tirar_dispensados_da_lista()
    _salvar_dispensados()


_RE_DISPENSAR = re.compile(
    r"(jarvis )?(ok|okay|okey|ok obrigado|ta bom|tudo certo|ja fiz|ja fui|ja cheguei|"
    r"ja estou aqui|ja to aqui|feito|pode parar|chega)"
    r"|(jarvis )?(cancel\w*|desativ\w*|silenci\w*)( o| a)?( aviso| avisos| lembrete| alerta| atraso)?")


def _pedido_dispensar(texto: str) -> bool:
    """True se a frase é só 'ok', 'já fiz', 'cancelar', 'cancela o aviso'... (e não 'cancela o alarme')."""
    t = re.sub(r"[^a-z ]", " ", normalizar(texto))
    t = re.sub(r"\s+", " ", t).strip()
    return bool(_RE_DISPENSAR.fullmatch(t))


def _acao_dispensar_aviso(a: dict):
    """'Jarvis, ok' / 'Jarvis, cancelar': para os avisos dos compromissos de agora."""
    agora = datetime.now()
    alvo = set(ESTADO.setdefault("avisos_ativos", set()))
    for e in EVENTOS["lista"]:
        minutos = (e["inicio"] - agora).total_seconds() / 60
        if -45 <= minutos <= 60:
            alvo.add(_chave_evento(e))
    DISPENSADOS.update(alvo)
    ESTADO["avisos_ativos"].clear()
    _tirar_dispensados_da_lista()
    _salvar_dispensados()
    print(f"[ação] avisos dispensados: {len(alvo)}")
    falar("Até logo, senhor.")
    return "mudo"


def _checar_lembretes():
    agora = datetime.now()
    for e in list(EVENTOS["lista"]):
        base = _chave_evento(e)
        if base in DISPENSADOS:
            continue
        falta = (e["inicio"] - agora).total_seconds()

        # antes de começar
        if 0 < falta <= ANTECEDENCIA_LEMBRETE * 60 and base not in EVENTOS["avisados"]:
            EVENTOS["avisados"].add(base)
            ESTADO.setdefault("avisos_ativos", set()).add(base)
            ESTADO["ultimo_evento_avisado"] = e
            minutos = max(1, round(falta / 60))
            with _mic_surdo():
                _bipar(2)
                falar(f"Senhor, {e['titulo']} começa em {minutos} minuto{'s' if minutos > 1 else ''}.")
            continue

        # já começou: avisa do atraso (só se for um aviso "fresco", para não repetir coisa velha)
        atraso = -falta / 60
        for marco in ATRASOS_AVISO:
            chave = f"{base}:atraso{marco}"
            if marco <= atraso < marco + 3 and chave not in EVENTOS["avisados"]:
                EVENTOS["avisados"].add(chave)
                ESTADO.setdefault("avisos_ativos", set()).add(base)
                ESTADO["ultimo_evento_avisado"] = e
                with _mic_surdo():
                    _bipar(2)
                    if marco == 0:
                        falar(f"Senhor, {e['titulo']} está começando agora.")
                    else:
                        falar(f"Senhor, o senhor está {marco} minutos atrasado para {e['titulo']}.")
                break


def _loop_servicos():
    """Roda em segundo plano: dispara timers/alarmes e avisa dos compromissos."""
    time.sleep(20)  # espera o Jarvis terminar de iniciar
    while True:
        try:
            agora = time.time()
            devidas = []
            with _TRAVA:
                for t in list(TAREFAS):
                    if t["quando"] <= agora:
                        devidas.append(t)
                        TAREFAS.remove(t)
                if devidas:
                    _salvar_tarefas()
            for t in devidas:
                threading.Thread(target=_disparar, args=(t,), daemon=True).start()
            if LEMBRETES_LIGADOS:
                if time.time() - EVENTOS["ultima"] > 300:
                    EVENTOS["ultima"] = time.time()  # evita abrir duas leituras ao mesmo tempo
                    threading.Thread(target=_atualizar_eventos, daemon=True).start()
                _checar_lembretes()
        except Exception as erro:
            print(f"[erro] serviços em segundo plano: {erro}")
        time.sleep(1)


def _falar_resumo_aprimoramento():
    """Depois que o Jarvis iniciou (PC já desbloqueado), conta o que aprimorou."""
    time.sleep(25)  # espera o "Sistemas online" e a calibração terminarem
    try:
        from auto_aprimorar import resumo_para_falar
        resumo = resumo_para_falar()
        if resumo:
            with _mic_surdo():
                falar(resumo)
    except Exception as erro:
        print(f"[erro] resumo do aprimoramento: {erro}")


def iniciar_servicos():
    """Liga timers, alarmes e lembretes. Pode chamar várias vezes: só inicia uma."""
    if SERVICOS["iniciado"]:
        return
    SERVICOS["iniciado"] = True
    _carregar_tarefas()
    _carregar_dispensados()
    threading.Thread(target=_loop_servicos, daemon=True, name="jarvis-servicos").start()
    threading.Thread(target=_falar_resumo_aprimoramento, daemon=True, name="jarvis-resumo").start()


# ======================= ANOTAÇÕES E LISTA DE COMPRAS =======================

def _ler_linhas(nome: str):
    try:
        with open(_arquivo(nome), encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return []


def _escrever_linhas(nome: str, linhas):
    with open(_arquivo(nome), "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in linhas))


def _acao_anotar(a: dict):
    texto = str(a.get("texto") or a.get("valor") or "").strip()
    if not texto:
        falar("O que devo anotar, senhor?")
        return "mudo"
    with open(_arquivo("notas.txt"), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%d/%m/%Y %H:%M}] {texto}\n")
    print(f"[ação] anotado: {texto}")
    falar("Anotado, senhor.")
    return "mudo"


def _acao_ler_notas(a: dict):
    linhas = _ler_linhas("notas.txt")
    if not linhas:
        falar("O senhor não tem anotações, senhor.")
        return "mudo"
    ultimas = [re.sub(r"^\[[^\]]*\]\s*", "", l) for l in linhas[-8:]]
    extra = f" Mostrando as {len(ultimas)} últimas." if len(linhas) > len(ultimas) else ""
    falar(f"O senhor tem {len(linhas)} anotaç{'ões' if len(linhas) > 1 else 'ão'}.{extra} " + ". ".join(ultimas) + ".")
    return "mudo"


def _acao_limpar_notas(a: dict):
    _escrever_linhas("notas.txt", [])
    falar("Anotações apagadas, senhor.")
    return "mudo"


def _lista_itens(valor):
    if isinstance(valor, str):
        valor = re.split(r",| e ", valor)
    return [str(i).strip(" .,;:!?") for i in (valor or []) if str(i).strip(" .,;:!?")]


def _acao_compras_add(a: dict):
    itens = _lista_itens(a.get("itens") or a.get("valor"))
    if not itens:
        falar("O que devo adicionar na lista, senhor?")
        return "mudo"
    atuais = _ler_linhas("compras.txt")
    existentes = {normalizar(i) for i in atuais}
    novos = [i for i in itens if normalizar(i) not in existentes]
    _escrever_linhas("compras.txt", atuais + novos)
    print(f"[ação] lista de compras + {novos}")
    if novos:
        falar(f"Adicionei {_juntar(novos)} na lista de compras, senhor.")
    else:
        falar("Já estava tudo na lista de compras, senhor.")
    return "mudo"


def _acao_compras_remover(a: dict):
    itens = _lista_itens(a.get("itens") or a.get("valor"))
    atuais = _ler_linhas("compras.txt")
    tirar, mantidos = [], list(atuais)
    for item in itens:
        alvo = normalizar(item)
        for existente in list(mantidos):
            n = normalizar(existente)
            if alvo and (alvo == n or alvo in n or n in alvo):
                mantidos.remove(existente)
                tirar.append(existente)
    if not tirar:
        falar("Não encontrei isso na lista de compras, senhor.")
    else:
        _escrever_linhas("compras.txt", mantidos)
        falar(f"Tirei {_juntar(tirar)} da lista de compras, senhor.")
    return "mudo"


def _acao_compras_ler(a: dict):
    itens = _ler_linhas("compras.txt")
    if not itens:
        falar("A lista de compras está vazia, senhor.")
    else:
        falar(f"Na lista de compras: {_juntar(itens)}.")
    return "mudo"


def _acao_compras_limpar(a: dict):
    _escrever_linhas("compras.txt", [])
    falar("Lista de compras esvaziada, senhor.")
    return "mudo"


# ======================= INFORMAÇÕES =======================

def _acao_hora_data(a: dict):
    agora = datetime.now()
    prefixo = "É" if (agora.hour, agora.minute) in ((0, 0), (1, 0)) else "São"
    falar(f"{prefixo} {_fala_hora(agora)}, {DIAS_SEMANA[agora.weekday()]}, "
          f"{agora.day} de {MESES[agora.month - 1]} de {agora.year}.")
    return "mudo"


def _dinheiro(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}".replace(",", ".") + " reais"
    return f"{v:.2f}".replace(".", ",") + " reais"


def _acao_cotacao(a: dict):
    valor = normalizar(str(a.get("valor") or "todas")).replace("btc", "bitcoin")
    pares = {"dolar": ("USD", "dólar"), "euro": ("EUR", "euro"), "bitcoin": ("BTC", "bitcoin")}
    chaves = [k for k in pares if k in valor] or list(pares)
    url = "https://economia.awesomeapi.com.br/json/last/" + ",".join(f"{pares[k][0]}-BRL" for k in chaves)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            dados = json.loads(r.read().decode("utf-8"))
        partes = []
        for k in chaves:
            d = dados.get(pares[k][0] + "BRL")
            if d:
                partes.append(f"{pares[k][1]} a {_dinheiro(float(d['bid']))}")
        if not partes:
            raise ValueError("resposta vazia")
    except Exception as erro:
        print(f"[erro] cotação: {erro}")
        falar("Não consegui consultar a cotação agora, senhor.")
        return "mudo"
    falar("Cotação agora: " + _juntar(partes) + ".")
    return "mudo"


class _MEMORIA(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _ENERGIA(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


def _uso_cpu():
    try:
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
                           capture_output=True, timeout=20, creationflags=SEM_JANELA)
        return int(float(r.stdout.decode("utf-8", "ignore").strip().replace(",", ".")))
    except Exception:
        return None


def _acao_estado_pc(a: dict):
    partes = []
    cpu = _uso_cpu()
    if cpu is not None:
        partes.append(f"processador em {cpu} por cento")
    try:
        mem = _MEMORIA()
        mem.dwLength = ctypes.sizeof(_MEMORIA)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        partes.append(f"memória em {mem.dwMemoryLoad} por cento, com "
                      f"{mem.ullAvailPhys / 1024 ** 3:.1f} gigas livres".replace(".", ","))
    except Exception:
        pass
    try:
        unidade = os.environ.get("SystemDrive", "C:") + "\\"
        livre = shutil.disk_usage(unidade).free / 1024 ** 3
        partes.append(f"{livre:.0f} gigas livres no disco {unidade[0]}")
    except Exception:
        pass
    try:
        p = _ENERGIA()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)) and p.BatteryFlag != 128 \
                and p.BatteryLifePercent <= 100:
            partes.append(f"bateria em {p.BatteryLifePercent} por cento"
                          + (", carregando" if p.ACLineStatus == 1 else ", na bateria"))
    except Exception:
        pass
    if not partes:
        falar("Não consegui ler o estado do computador, senhor.")
    else:
        falar("Estado do computador: " + _juntar(partes) + ".")
    return "mudo"


# ======================= CONTROLE DO COMPUTADOR =======================

SCRIPT_PRINT = r'''
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @"
using System.Runtime.InteropServices;
public class DpiJarvis { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[DpiJarvis]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($env:JARVIS_ARQUIVO, [System.Drawing.Imaging.ImageFormat]::Png)
'''


def _acao_print(a: dict):
    pasta = os.path.join(os.path.expanduser("~"), "Pictures", "Jarvis Prints")
    os.makedirs(pasta, exist_ok=True)
    arquivo = os.path.join(pasta, f"print_{datetime.now():%Y%m%d_%H%M%S}.png")
    time.sleep(0.4)
    ok = False
    try:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(arquivo)
        ok = True
    except Exception:
        pass
    if not ok:
        try:
            subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", SCRIPT_PRINT],
                           env={**os.environ, "JARVIS_ARQUIVO": arquivo},
                           capture_output=True, timeout=30, creationflags=SEM_JANELA)
            ok = os.path.exists(arquivo)
        except Exception as erro:
            print(f"[erro] print: {erro}")
    if ok:
        print(f"[ação] print salvo em {arquivo}")
        falar("Print salvo na pasta Imagens, em Jarvis Prints, senhor.")
    else:
        falar("Não consegui tirar o print, senhor.")
    return "mudo"


FOCO_PADRAO = ["# Um programa por linha (nome do processo, sem .exe). Linhas com # são ignoradas.",
               "chrome", "msedge", "firefox", "opera", "brave",
               "steam", "epicgameslauncher", "riotclientservices", "battle.net", "origin",
               "eadesktop", "ubisoftconnect", "discord"]


def _lista_foco():
    caminho = _arquivo("foco.txt")
    if not os.path.exists(caminho):
        _escrever_linhas("foco.txt", FOCO_PADRAO)
    nomes = [l.lower().removesuffix(".exe") for l in _ler_linhas("foco.txt") if not l.startswith("#")]
    exe = os.path.basename(NAVEGADOR.get("exe") or "").lower().removesuffix(".exe")
    if exe and exe not in nomes:
        nomes.append(exe)  # o navegador padrão sempre entra
    return nomes


def _processos_rodando():
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=20,
                           creationflags=SEM_JANELA)
        nomes = set()
        for linha in r.stdout.decode("cp1252", "ignore").splitlines():
            m = re.match(r'"([^"]+)"', linha)
            if m:
                nomes.add(m.group(1).lower().removesuffix(".exe"))
        return nomes
    except Exception:
        return set()


def _fechar_processo(nome: str):
    subprocess.run(["taskkill", "/F", "/IM", nome + ".exe"], capture_output=True, timeout=20,
                   creationflags=SEM_JANELA)


def _acao_modo_foco(a: dict):
    rodando = _processos_rodando()
    alvos = [n for n in _lista_foco() if n in rodando]
    if not alvos:
        falar("Nada da lista de foco está aberto, senhor.")
        return "mudo"
    resp = perguntar_sim_nao(f"Vou fechar {_juntar(alvos)}. Confirma, senhor?")
    if resp is not True:
        falar("Modo foco cancelado, senhor.")
        return "mudo"
    for nome in alvos:
        try:
            _fechar_processo(nome)
        except Exception as erro:
            print(f"(Não consegui fechar {nome}: {erro})")
    print(f"[ação] modo foco: fechei {alvos}")
    falar("Modo foco ativado, senhor. Bom trabalho.")
    return "mudo"


def _acao_boa_noite(a: dict):
    try:
        _tecla(0xB2)  # tecla "parar mídia"
    except Exception:
        pass
    for nome in PLAYERS_PARA_FECHAR:
        try:
            _fechar_processo(str(nome).lower().removesuffix(".exe"))
        except Exception:
            pass
    falar("Boa noite, senhor.")
    texto = _texto_agenda("amanha")
    if texto:
        falar(texto)
    with _TRAVA:
        alarmes = sorted((t for t in TAREFAS if t["tipo"] == "alarme"), key=lambda t: t["quando"])
    if alarmes:
        falar(f"O alarme {_quando_fala(datetime.fromtimestamp(alarmes[0]['quando']))} está ativo.")
    falar("Bloqueando o computador. Descanse bem.")
    time.sleep(1)
    try:
        ctypes.windll.user32.LockWorkStation()
    except Exception as erro:
        print(f"(Não consegui bloquear o computador: {erro})")
    return "mudo"


# ======================= SPOTIFY NO NAVEGADOR =======================
SPOTIFY_ESPERA = 7            # segundos esperando a página carregar
SPOTIFY_AUTO_CLICAR = True    # dá dois cliques na 1ª música da busca
SPOTIFY_CLIQUE = (0.40, 300)  # (posição horizontal em % da janela, pixels abaixo do topo da janela)


def _duplo_clique_na_janela():
    u = ctypes.WinDLL("user32")
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = u.GetForegroundWindow()
    r = wintypes.RECT()
    if not hwnd or not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    x = int(r.left + (r.right - r.left) * SPOTIFY_CLIQUE[0])
    y = int(r.top + SPOTIFY_CLIQUE[1])
    u.SetCursorPos(x, y)
    time.sleep(0.2)
    for _ in range(2):
        u.mouse_event(0x0002, 0, 0, 0, 0)  # botão esquerdo para baixo
        u.mouse_event(0x0004, 0, 0, 0, 0)  # botão esquerdo solto
        time.sleep(0.08)
    print(f"[ação] cliques do Spotify em ({x}, {y})")
    return True


def _acao_spotify(a: dict):
    consulta = str(a.get("valor") or a.get("texto") or "").strip()
    if not consulta:
        _abrir_url("https://open.spotify.com")
        falar("Abrindo o Spotify, senhor.")
        return "mudo"
    print(f"[ação] Spotify: {consulta}")
    falar(f"Tocando {consulta} no Spotify, senhor.")
    _abrir_url("https://open.spotify.com/search/" + quote(consulta, safe="") + "/tracks")
    if SPOTIFY_AUTO_CLICAR:
        time.sleep(SPOTIFY_ESPERA)
        try:
            _duplo_clique_na_janela()
        except Exception as erro:
            print(f"(Não consegui clicar no Spotify: {erro})")
    return "mudo"


# ======================= COMANDOS CRIADOS PELO PRÓPRIO JARVIS =======================

def _acao_criar_comando(a: dict):
    pedido = str(a.get("pedido") or a.get("valor") or "").strip()
    if not pedido:
        return False
    gerador_comandos.criar_comando(pedido, falar)  # fala "Criando comando" e "Novo comando criado, senhor"
    return "mudo"


def _acao_comando_auto(a: dict):
    texto = str(a.get("texto") or "")
    func = gerador_comandos.achar(texto)
    if func:
        func(texto, falar)
    return "mudo"


def _acao_resposta_aprimorar(a: dict):
    texto = str(a.get("texto") or "").strip()
    if texto:
        falar(texto)
    return "mudo"


ACOES_NOVAS = {
    "spotify": _acao_spotify,
    "timer": _acao_timer, "alarme": _acao_alarme,
    "listar_alarmes": _acao_listar_alarmes, "cancelar_alarmes": _acao_cancelar_alarmes,
    "agenda_dia": _acao_agenda_dia, "cancelar_compromisso": _acao_cancelar_compromisso,
    "mudar_compromisso": _acao_mudar_compromisso,
    "anotar": _acao_anotar, "ler_notas": _acao_ler_notas, "limpar_notas": _acao_limpar_notas,
    "compras_add": _acao_compras_add, "compras_remover": _acao_compras_remover,
    "compras_ler": _acao_compras_ler, "compras_limpar": _acao_compras_limpar,
    "hora_data": _acao_hora_data, "cotacao": _acao_cotacao, "estado_pc": _acao_estado_pc,
    "print": _acao_print, "modo_foco": _acao_modo_foco, "boa_noite": _acao_boa_noite,
    "dispensar_aviso": _acao_dispensar_aviso,
    "resposta_aprimorar": _acao_resposta_aprimorar,
    "criar_comando": _acao_criar_comando, "comando_auto": _acao_comando_auto,
}


# ======================= COMANDOS SIMPLES (sem passar pelo Ollama) =======================

def _extrair_itens(texto: str):
    s = re.sub(r"(?i)\bj[aá]r\w+\b", " ", texto)
    s = re.sub(r"(?i)\b(?:n[ao]|d[ao]|em|para|pra|à|a)?\s*(?:minha\s+|nossa\s+)?lista\s+(?:de\s+|do\s+)?"
               r"(?:compras|mercado|supermercado)\b", " ", s)
    s = re.sub(r"(?i)\b(adiciona|adicione|adicionar|coloca|coloque|colocar|p[oô]e|ponha|inclui|inclua|incluir|"
               r"anota|anote|anotar|compra|compre|comprar|tira|tire|tirar|retira|retire|retirar|"
               r"remove|remova|remover|risca|risque|riscar|apaga|apague|apagar|por favor)\b", " ", s)
    itens = []
    for parte in re.split(r",|;|\be\b", s):
        parte = re.sub(r"(?i)^\s*(o|a|os|as|um|uns|uma|umas)\s+", "", parte).strip(" .!?:-")
        if parte:
            itens.append(parte)
    return itens


def _comando_local(texto: str):
    """Reconhece os comandos simples. Devolve {"fala": "", "acoes": [...]} ou None (aí vai para o Ollama)."""
    t = re.sub(r"[,.!?;]", " ", normalizar(texto))
    t = re.sub(r"\s+", " ", t).strip()

    def acao(**campos):
        return {"fala": "", "acoes": [campos]}

    # --- comandos criados pelo auto_aprimorar (e "ativar aprimoramento") ---
    try:
        from auto_aprimorar import responder_comando
        resposta_aprimorar = responder_comando(texto)
        if resposta_aprimorar:
            return acao(tipo="resposta_aprimorar", texto=str(resposta_aprimorar))
    except Exception as erro:
        print(f"[erro] auto_aprimorar: {erro}")

    try:
        import jarvis_mensagens
        local = jarvis_mensagens.comando_local(t, texto)
        if local:
            return local
    except Exception as erro:
        print(f"[erro] mensagens: {erro}")

    t = re.sub(r"^jar(?:vis|ves|vi|vez|bas)\s+", "", t)  # "Jarvis, ..." no começo da frase

    # --- tocar no Spotify ---
    m = re.match(r"^(?:jarvis )?(?:toca|tocar|toque|coloca|coloque|colocar|poe|ponha|bota|bote|"
                 r"reproduz|reproduza|quero ouvir)\b\s*(.*)$", t)
    if m and not re.search(r"\b(lista|alarme|timer|anotac\w*|notas?|volume|mudo|janela|monitor)\b", t):
        q = m.group(1)
        q = re.sub(r"\b(no|pelo|do|na) (spotify|navegador)\b|\bspotify\b|\bpara mim\b", " ", q)
        q = re.sub(r"\b(a |uma )?(musica|faixa|som|cancao)\b", " ", q)
        q = re.sub(r"^\s*(de|da|do|a|o|um|uma)\s+", "", re.sub(r"\s+", " ", q)).strip()
        return acao(tipo="spotify", valor=q)

    # --- "ok" / cancelar o aviso de atraso ---
    if (re.fullmatch(r"(jarvis )?(ok|okay|okey|ok obrigado|ta bom|tudo certo|ja fiz|ja fui|ja cheguei|"
                     r"ja estou aqui|ja to aqui|feito|pode parar|chega)", t)
            or re.fullmatch(r"(jarvis )?(cancel\w*|desativ\w*|silenci\w*)( o| a)?"
                            r"( aviso| avisos| lembrete| alerta| atraso)?", t)):
        return acao(tipo="dispensar_aviso")

    # --- boa noite / modo foco ---
    if t in ("boa noite", "jarvis boa noite") or "modo boa noite" in t or "vou dormir" in t:
        return acao(tipo="boa_noite")
    if re.search(r"\bmodo (de )?foco\b|\b(ativa|ativar|liga|ligar|entra|entrar) (no |o )?foco\b|"
                 r"\bpreciso (me )?concentrar\b", t):
        return acao(tipo="modo_foco")

    # --- timer e alarme ---
    tem_timer = re.search(r"\b(timers?|temporizadores?)\b", t)
    tem_alarme = re.search(r"\b(alarmes?|despertador\w*)\b", t)
    if (tem_timer or tem_alarme) and re.search(r"\b(cancel\w*|desativ\w*|apag\w*|remov\w*|desliga\w*)\b", t):
        valor = "timer" if tem_timer and not tem_alarme else "alarme" if tem_alarme and not tem_timer else "todos"
        return acao(tipo="cancelar_alarmes", valor=valor)
    if re.search(r"\bquanto (tempo )?falta\b|\bquais (sao )?(os )?(alarmes|timers)\b|"
                 r"\b(tenho|tem) (algum )?(alarme|timer)\b|\bque (alarmes|timers)\b", t):
        return acao(tipo="listar_alarmes")
    relativo = re.search(r"\b(em|daqui a|dentro de|por)\s+(\d+|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|"
                         r"dez|quinze|vinte|trinta|meia)\b", t)
    if tem_timer or re.search(r"\bdaqui a\b|\bme (avisa|avise|lembra|lembre)\w* em\b", t) \
            or (tem_alarme and relativo):
        return acao(tipo="timer", segundos=_duracao_segundos(t))
    if tem_alarme or re.search(r"\bme (acorda|acorde|desperta|desperte)\b", t):
        hm = _hora_do_texto(t)
        return acao(tipo="alarme", hora=f"{hm[0]:02d}:{hm[1]:02d}" if hm else "", amanha="amanha" in t)

    # --- informações ---
    if re.search(r"\b(cotac\w+|dolar|euro|bitcoin|btc)\b", t) and not re.search(r"\b(abr\w*|pesquis\w*|youtube|site)\b", t):
        achados = [n for n in ("dolar", "euro", "bitcoin") if re.search(rf"\b{n}\b", t)]
        if re.search(r"\bbtc\b", t) and "bitcoin" not in achados:
            achados.append("bitcoin")
        return acao(tipo="cotacao", valor=" ".join(achados) or "todas")
    if re.search(r"\b(estado|status|situacao|saude|desempenho) (do|de) (meu )?(pc|computador)\b|"
                 r"\bcomo (esta|ta|estao) (o |a |meu )?(pc|computador|bateria|memoria|processador)\b|"
                 r"\buso (de|da|do) (memoria|cpu|processador)\b|\bespaco (livre )?(no|do|em) (disco|hd)\b|"
                 r"\bnivel (da|de) bateria\b|\bmemoria ram\b", t):
        return acao(tipo="estado_pc")
    if re.search(r"\b(print|prints|printscreen|screenshot|captura de tela|capturar a tela|foto da tela)\b", t):
        return acao(tipo="print")

    # --- lista de compras ---
    if re.search(r"\blista (de |do )?(compras|mercado|supermercado)\b", t):
        if re.search(r"\b(limp\w*|zer(a|ar|e)|esvazi\w*)\b|\bapag\w* (tudo|toda|a lista|minha lista)\b", t):
            return acao(tipo="compras_limpar")
        if re.search(r"\b(tira\w*|tire|remov\w*|retir\w*|risc\w*|apag\w*)\b", t):
            return acao(tipo="compras_remover", itens=_extrair_itens(texto))
        if re.search(r"\b(le|ler|leia|mostr\w*|diga|fala|quais|qual|o que (tem|falta|ta|esta))\b", t):
            return acao(tipo="compras_ler")
        return acao(tipo="compras_add", itens=_extrair_itens(texto))

    # --- anotações ---
    if re.search(r"\b(anotacoes|notas|anotei)\b", t):
        if re.search(r"\b(limp\w*|apag\w*|zer(a|ar|e)|esvazi\w*)\b", t):
            return acao(tipo="limpar_notas")
        if re.search(r"\b(le|ler|leia|mostr\w*|diga|fala|quais|o que)\b", t):
            return acao(tipo="ler_notas")
    if re.search(r"\b(anota|anote|anotar|registra|registre)\b", t):
        nota = re.sub(r"(?i)^.*?\b(anota|anote|anotar|registra|registre)\b[\s:,]*(a[ií]\s*)?(isso\s*)?(que\s+)?", "", texto)
        return acao(tipo="anotar", texto=nota.strip(" ,.:;"))

    # --- cancelar compromisso da agenda (sem passar pelo Ollama) ---
    m = re.match(r"^(?:cancel\w*|apag\w*|remov\w*|exclu\w*|delet\w*|desmarc\w*|tira\w*|tire)\s+"
                 r"(?:(?:o|a|os|as|meu|minha)\s+)*(?:compromisso|evento|reuniao|consulta|agendamento)\s*"
                 r"(?:(?:de|do|da|dos|das)\s+)?(.*)$", t)
    if m:
        nome = m.group(1).strip()
        if nome in ("hoje", "agora", "atual", "proximo", "de hoje", "de agora", "que estou atrasado"):
            nome = ""   # sem nome: o Jarvis escolhe o compromisso mais próximo
        print(f"[local] cancelar compromisso (nome: {nome or 'nenhum'})")
        return acao(tipo="cancelar_compromisso", titulo=nome)

    # --- ler a agenda (criar, cancelar e mudar ficam com o Ollama) ---
    if (re.search(r"\b(compromissos?|agenda|reunioes?|reuniao)\b", t)
            and re.search(r"\b(o que|quais|qual|le|ler|leia|tenho|tem|diz|fala|mostr\w*|hoje|amanha|semana)\b", t)
            and not re.search(r"\b(agendar|agende|marcar|marca|cancel\w*|desmarc\w*|remarc\w*|mudar|muda|"
                              r"adiar|adia|alter\w*|troca\w*)\b", t)):
        quando = "amanha" if "amanha" in t else "semana" if re.search(r"\bsemana|proximos dias\b", t) else "hoje"
        return acao(tipo="agenda_dia", valor=quando)
    if re.search(r"\bo que (eu )?tenho (para |pra )?(hoje|amanha)\b", t):
        return acao(tipo="agenda_dia", valor="amanha" if "amanha" in t else "hoje")

    # --- hora e data ---
    if re.search(r"\bque horas\b|\bhoras sao\b|\bqual (e )?a hora\b|\bque dia (e |eh )?hoje\b|"
                 r"\bqual (e )?a data\b|\bdata de hoje\b|\bem que dia\b|\bque dia da semana\b|\bhora certa\b", t):
        return acao(tipo="hora_data")

    # --- comandos criados pelo próprio Jarvis (pasta comandos_auto) ---
    if gerador_comandos.achar(t):
        return acao(tipo="comando_auto", texto=texto)
    return None


def _fazer(acao: dict):
    """Executa uma ação. Devolve True (ok), False (falhou) ou 'encerrar'."""
    tipo = str(acao.get("tipo", "")).lower()
    valor = acao.get("valor", "")

    if tipo == "abrir_site":
        url = str(valor).strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        print(f"[ação] abrindo site: {url}")
        _abrir_url(url)
        ok = True
    elif tipo == "pesquisar_google":
        print(f"[ação] pesquisando no Google: {valor}")
        _abrir_url("https://www.google.com/search?q=" + quote_plus(str(valor)))
        ok = True
    elif tipo == "pesquisar_youtube":
        print(f"[ação] pesquisando no YouTube: {valor}")
        _abrir_url("https://www.youtube.com/results?search_query=" + quote_plus(str(valor)))
        ok = True
    elif tipo == "abrir_app":
        app = achar_app(str(valor))
        if not app:
            falar(f"Não encontrei o aplicativo {valor}, senhor.")
            return False
        print(f"[ação] abrindo aplicativo: {app[0]}")
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app[1]])
        ok = True
    elif tipo == "volume":
        vezes = max(1, min(50, int(acao.get("vezes") or 5)))
        vk = {"subir": 0xAF, "descer": 0xAE, "mudo": 0xAD}.get(normalizar(str(valor)))
        if not vk:
            return False
        print(f"[ação] volume: {valor}")
        _tecla(vk, 1 if vk == 0xAD else vezes)
        ok = True
    elif tipo == "midia":
        vk = {"pausar": 0xB3, "proxima": 0xB0, "anterior": 0xB1}.get(normalizar(str(valor)))
        if not vk:
            return False
        print(f"[ação] mídia: {valor}")
        _tecla(vk)
        ok = True
    elif tipo == "agendar":
        return "mudo" if _agendar(acao) else False  # já falou: não diz "Executado com sucesso"
    elif tipo == "mover_janela":
        return mover_janela_ativa(int(valor))
    elif tipo == "encerrar":
        return "encerrar"
    elif tipo in ACOES_NOVAS:
        return ACOES_NOVAS[tipo](acao)  # as ações novas falam o resultado sozinhas
    else:
        print(f"[ação] ação desconhecida ignorada: {acao}")
        return False

    if ok and acao.get("monitor"):
        time.sleep(3)  # espera a janela nova abrir
        try:
            mover_janela_ativa(int(acao["monitor"]))
        except Exception as erro:
            print(f"(Não consegui mover a janela: {erro})")
    return ok


def executar(texto: str, confirmar=None):
    """Trata um pedido. Devolve True para continuar ligado ou False para desligar o Jarvis."""
    palavras = set(re.findall(r"[a-z0-9]+", normalizar(texto)))
    if palavras and len(palavras) <= 3 and palavras & {"desligar", "encerrar", "tchau", "sair"}:
        return False

    if _pedido_dispensar(texto):
        _acao_dispensar_aviso({})   # responde na hora, sem dizer "Executando comando"
        return True

    falar("Executando comando.")
    try:
        resposta = perguntar(texto)  # o HUD troca esta função para mostrar "processando"
    except Exception as erro:
        print(f"[erro] {erro}")
        if isinstance(erro, ErroJarvis):
            falar(str(erro))
        else:
            codigo = getattr(erro, "code", None)
            falar(f"Tive um problema{f' ({codigo})' if codigo else ''}, senhor. Tente de novo em instantes.")
        return True

    fala = str(resposta.get("fala") or "").strip()
    if fala:
        falar(fala)

    acoes = [a for a in (resposta.get("acoes") or []) if isinstance(a, dict)]
    tudo_ok, encerrar, mudo = bool(acoes), False, False
    for acao in acoes:
        try:
            r = _fazer(acao)
        except Exception as erro:
            print(f"[erro] ação {acao}: {erro}")
            r = False
        if r == "encerrar":
            encerrar = True
        elif r == "mudo":
            mudo = True
        elif not r:
            tudo_ok = False
    if encerrar:
        return False
    if acoes and tudo_ok and not mudo:
        falar("Executado com sucesso.")
    return True


# ======================= PRINCIPAL =======================

def main():
    print("Jarvis online. Ctrl+C para sair.\n")
    achar_navegador()
    detectar_monitores()
    carregar_apps()
    iniciar_servicos()
    falar("Sistemas online. Diga Jarvis quando precisar de mim, senhor.")
    limiar = calibrar()

    try:
        while True:
            print("Em espera. Diga 'Jarvis' para me chamar.\n")
            ouvido = escutar(limiar, 0)
            achou = CHAMADO.search(ouvido or "")
            if not achou:
                continue

            comando = ouvido[achou.end():].strip(" ,.!?;:-")  # "Jarvis, abre o Facebook" numa frase só
            if len(comando) < 3:
                falar("Sim, senhor.")
                comando = escutar(limiar, ESPERA_COMANDO)
                if not comando:
                    falar("Parei de escutar, senhor. Diga Jarvis quando precisar.")
                    continue

            if executar(comando, perguntar_sim_nao) is False:
                break
    except KeyboardInterrupt:
        pass
    falar("Até logo, senhor.")


# Mensagens (WhatsApp, Gmail, Discord, Instagram) e chamadas
try:
    import jarvis_mensagens
    ACOES_NOVAS.update(jarvis_mensagens.ACOES)
    jarvis_mensagens.iniciar()
except Exception as erro:
    print(f"(Mensagens do Jarvis desativadas: {erro})")

# Carrega os comandos que o próprio Jarvis já criou (pasta comandos_auto)
gerador_comandos.carregar_todos()

# Liga timers, alarmes e lembretes assim que o arquivo é carregado (também quando o HUD o importa)
iniciar_servicos()

if __name__ == "__main__":
    main()