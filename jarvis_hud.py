"""
Jarvis HUD - tela estilo painel futurista Stark Industries (ciano neon).

Abre uma janela animada com régua de dias no topo, reator central, medidores
circulares, calendário, relógio, onda de voz, registro de conversa, clima,
cotação do dólar, rede e a saúde do seu PC (CPU, memória, disco, GPU e bateria),
e roda o Jarvis por trás dela.

IMPORTANTE: coloque este arquivo na MESMA pasta do jarvis_acoes.py.

Como rodar:
    python jarvis_hud.py

Para abrir sozinho quando o Windows ligar, rode uma vez:
    python iniciar_com_windows.py

Cores do reator:
    turquesa apagado = em espera    | ciano forte = ouvindo você
    laranja          = processando  | verde       = falando

O clima (painel da direita) usa a internet (wttr.in). Sem conexão, o painel
só mostra "buscando previsão". A cotação do dólar (AwesomeAPI) também usa a
internet e se atualiza sozinha a cada 2 minutos.

Teclas:  F11 = tela cheia    Esc = sair da tela cheia
Para encerrar: diga "Jarvis, desligar" ou feche a janela.

Velocidade dos anéis: ajuste VELOCIDADE_GIRO e VELOCIDADE_RADAR na seção "Aparência".
"""

import calendar
import ctypes
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from ctypes import wintypes
from datetime import datetime
import tkinter as tk

# Impede que o PowerShell/cmd abra uma janela preta a cada comando do Jarvis.
# (Só a janela do PowerShell pedida de propósito, com CREATE_NEW_CONSOLE, continua abrindo.)
if sys.platform == "win32":
    _popen_original = subprocess.Popen.__init__

    def _popen_sem_janela(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & (0x00000010 | 0x00000008):  # nova janela de propósito / processo separado
            kwargs["creationflags"] = flags | 0x08000000  # CREATE_NO_WINDOW
        _popen_original(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_sem_janela

PASTA = os.path.dirname(os.path.abspath(__file__))
os.chdir(PASTA)  # no início do Windows o programa nasce em outra pasta

# Sem console (início com o Windows usa pythonw): guarda mensagens e erros em jarvis.log
if sys.stdout is None or sys.stderr is None:
    _log_caminho = os.path.join(PASTA, "jarvis.log")
    try:
        _modo = "w" if os.path.exists(_log_caminho) and os.path.getsize(_log_caminho) > 1_000_000 else "a"
        _log = open(_log_caminho, _modo, encoding="utf-8", buffering=1)
        _log.write(f"\n--- Jarvis iniciado em {datetime.now():%d/%m/%Y %H:%M:%S} ---\n")
    except OSError:
        _log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

_MUTEX = None


def _ja_em_execucao() -> bool:
    """Evita abrir dois Jarvis ao mesmo tempo (os dois responderiam juntos)."""
    global _MUTEX
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _MUTEX = k.CreateMutexW(None, False, "Local\\JarvisHUD_InstanciaUnica")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__" and _ja_em_execucao():
    ctypes.windll.user32.MessageBoxW(
        0, "O Jarvis já está aberto.\nFeche a janela dele antes de abrir de novo.", "Jarvis", 0x40)
    sys.exit(0)

# Importar antes de criar a janela: o jarvis_acoes ajusta o DPI do Windows
import jarvis_acoes as J
import jarvis_rotina as RT
import jarvis_memoria as MEM
import jarvis_voz_id as VID
import jarvis_extras as EX
from jarvis_ponte import Ponte

PONTE = Ponte(J)  # adaptador: dá ao extras as funções que o jarvis_acoes não tem

# ------------------------- Aparência -------------------------
FUNDO = "#020d14"
LARANJA = (255, 176, 32)
VERMELHO = "#ff3b3b"

# Velocidade de giro dos anéis: graus por segundo para cada 1.0 de "velocidade" do estado.
# Menor = mais lento. Em espera (0.5): 0.5 x 30 = 15 graus/s, uma volta a cada 24 s.
VELOCIDADE_GIRO = 30
# Velocidade da varredura do radar, em graus por segundo (constante): 45 = uma volta a cada 8 s.
VELOCIDADE_RADAR = 45

# estado: (cor, velocidade dos anéis, pulsação do núcleo, altura das ondas)
ESTADOS = {
    "INICIANDO":   ("#12a8c8", 0.6, 0.03, 2),
    "CARREGANDO":  ("#ffb020", 1.2, 0.05, 2),
    "CALIBRANDO":  ("#ffb020", 0.8, 0.05, 2),
    "EM ESPERA":   ("#12c4e0", 0.5, 0.03, 2),
    "OUVINDO":     ("#45f3ff", 1.4, 0.07, 14),
    "PROCESSANDO": ("#ffb020", 3.2, 0.06, 4),
    "FALANDO":     ("#4dffb8", 1.6, 0.09, 26),
    "DESLIGADO":   ("#ff4d4d", 0.1, 0.00, 0),
}

DICAS = {
    "INICIANDO": "Iniciando sistemas...",
    "CARREGANDO": "Lendo aplicativos instalados...",
    "CALIBRANDO": "Calibrando o microfone... fique em silêncio",
    "EM ESPERA": 'Diga "Jarvis" para me chamar',
    "OUVINDO": "Pode falar, senhor",
    "PROCESSANDO": "Executando comando...",
    "FALANDO": "",
    "DESLIGADO": "Até logo, senhor",
}

DIAS = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]
DIAS_FULL = ["SEGUNDA-FEIRA", "TERÇA-FEIRA", "QUARTA-FEIRA", "QUINTA-FEIRA", "SEXTA-FEIRA", "SÁBADO", "DOMINGO"]
MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
MESES_FULL = ["JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
              "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]

ATALHOS = ["Abrir programas", "Tocar música", "Resumo do dia", "Anotar / lembrar"]


def hex_rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def rgb_hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def misturar(a, b, f: float):
    """Mistura duas cores RGB (f=0 -> a, f=1 -> b)."""
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


def fmt_vel(b: float) -> str:
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f} MB/s"
    if b >= 1024:
        return f"{b / 1024:.0f} KB/s"
    return f"{b:.0f} B/s"


FUNDO_RGB = hex_rgb(FUNDO)

# Estado atual do Jarvis (lido pela tela, escrito pelo Jarvis)
ST = {"v": "INICIANDO"}

# Volume do microfone neste instante (atualizado enquanto o Jarvis escuta)
NIVEL = {"v": 0.0, "t": 0.0}


# ------------------- Ligação com o Jarvis -------------------

class Espelho:
    """Copia o que o Jarvis imprime no terminal para o registro da tela."""

    def __init__(self, original, fila):
        self.original = original
        self.fila = fila
        self.buf = ""

    def write(self, texto):
        self.original.write(texto)
        self.buf += texto
        while "\n" in self.buf:
            linha, self.buf = self.buf.split("\n", 1)
            self._processar(linha.strip())
        return len(texto)

    def flush(self):
        self.original.flush()

    def __getattr__(self, nome):
        return getattr(self.original, nome)

    def _processar(self, linha):
        if linha.startswith("Jarvis:"):
            papel, texto = "JARVIS", linha[7:].strip()
        elif linha.startswith("[ação]"):
            papel, texto = "AÇÃO", linha[6:].strip()
        elif linha.startswith("[monitor"):
            papel, texto = "SISTEMA", linha
        else:
            return
        if texto:
            self.fila.put(("log", papel, texto))


def ligar_jarvis(fila):
    """Troca algumas funções do Jarvis por versões que avisam a tela do que está acontecendo."""
    falar_original = RT.voz_do_filme(J.falar)  # voz neural (cai para a do Windows se falhar)
    escutar_original = J.escutar
    perguntar_original = J.perguntar
    calibrar_original = J.calibrar
    carregar_original = J.carregar_apps
    volume_original = J.volume

    def volume(bloco):
        v = volume_original(bloco)
        NIVEL["v"] = v
        NIVEL["t"] = time.time()
        return v

    def falar(texto):
        anterior = ST["v"]
        ST["v"] = "FALANDO"
        try:
            if VID.eh_chamado(texto) and not VID.autorizado_chamado():
                texto = "Essa voz não está cadastrada no meu banco de dados."
            VID.marcar_fala(True)
            RT.preparar_grafico(texto)  # liga o gráfico se houver porcentagem
            with EX.TRAVA_FALA:  # duas vozes nunca ao mesmo tempo
                falar_original(texto)
        finally:
            RT.GRAFICO["ativo"] = False
            VID.marcar_fala(False)
            ST["v"] = anterior if anterior != "FALANDO" else "EM ESPERA"

    def escutar(limiar, espera_max=0):
        ST["v"] = "OUVINDO" if espera_max else "EM ESPERA"
        try:
            return escutar_original(limiar, espera_max)
        finally:
            if espera_max:
                ST["v"] = "PROCESSANDO"

    def perguntar(texto):
        anterior = ST["v"]
        ST["v"] = "PROCESSANDO"
        try:
            return perguntar_original(texto)
        finally:
            ST["v"] = anterior

    def calibrar():
        ST["v"] = "CALIBRANDO"
        try:
            limiar = calibrar_original()
        finally:
            ST["v"] = "EM ESPERA"
        # Voz: liga a escuta contínua e, na primeira vez, cadastra a sua voz
        try:
            VID.iniciar_escuta_continua()
            VID.cadastro_inicial(J)
        except Exception:
            import traceback
            print("[voz] ERRO:\n" + traceback.format_exc())
        # Depois de calibrar o microfone (em silêncio): resumo do dia + música
        try:
            if RT.iniciou_com_windows():
                RT.rotina_de_inicio(J)
        except Exception:
            import traceback
            print("[rotina] ERRO:\n" + traceback.format_exc())
        return limiar

    executar_original = J.executar

    def executar(texto, confirmar):
        # só obedece a voz cadastrada
        try:
            if not VID.autorizado_agora():
                if not VID.rejeicao_recente():
                    J.falar("Essa voz não está cadastrada no meu banco de dados.")
                VID.limpar_buffer()
                return True
            VID.limpar_buffer()
            if VID.eh_pedido_de_cadastro(texto):
                VID.cadastrar_voz_nova(J)
                return True
        except Exception:
            import traceback
            print("[voz] ERRO:\n" + traceback.format_exc())
        # memória: lembra / anota / o que você lembra / esquece
        try:
            if MEM.comando_memoria(J, texto):
                return True
        except Exception:
            import traceback
            print("[memória] ERRO:\n" + traceback.format_exc())
        # comandos do PC (desligar, reiniciar, bloquear), resumo e música
        try:
            r = RT.comando_local(J, texto)
        except Exception:
            import traceback
            print("[rotina] ERRO:\n" + traceback.format_exc())
            r = None
        if r is not None:
            return r
        # extras: protocolos, lembretes, alarmes, cotações, diagnóstico, tela, intérprete...
        try:
            if not _pula_extras(texto) and EX.comando_extra(PONTE, texto):
                return True
        except Exception:
            import traceback
            print("[extras] ERRO:\n" + traceback.format_exc())
        return executar_original(texto, confirmar)

    def carregar_apps():
        anterior = ST["v"]
        ST["v"] = "CARREGANDO"
        try:
            return carregar_original()
        finally:
            ST["v"] = anterior

    J.falar = falar
    J.escutar = escutar
    J.perguntar = perguntar
    J.calibrar = calibrar
    J.carregar_apps = carregar_apps
    J.volume = volume
    def executar_com_conversa(texto, confirmar):
        r = executar(texto, confirmar)
        if r is False:
            return False
        try:
            # modo conversa: depois de um comando, continua ouvindo alguns segundos sem exigir "Jarvis"
            if EX.config("conversa") and not CONVERSA["ativa"]:
                CONVERSA["ativa"] = True
                try:
                    if not EX.acompanhar(PONTE, lambda tx, a, b: executar(tx, confirmar) is not False):
                        return False
                finally:
                    CONVERSA["ativa"] = False
        except Exception:
            import traceback
            print("[extras] ERRO no modo conversa:\n" + traceback.format_exc())
        return r

    J.executar = executar_com_conversa


import re as _re

# Pedidos de agenda/compromissos continuam com a agenda do Google (jarvis_acoes), não com os lembretes locais
_RE_AGENDA_GOOGLE = _re.compile(r"\b(agenda|agendar|agende|compromissos?|o que (eu )?tenho)\b")
CONVERSA = {"ativa": False}


def _pula_extras(texto):
    t = EX.normalizar(texto)
    return bool(_RE_AGENDA_GOOGLE.search(t)) and "lembrete" not in t


INICIO_ATRASO = 12  # segundos de espera quando abre junto com o Windows


def rodar_jarvis(fila):
    try:
        if "--inicio-windows" in sys.argv:
            time.sleep(INICIO_ATRASO)  # espera internet, microfone e área de trabalho
        J.main()
    except Exception as erro:
        fila.put(("log", "SISTEMA", f"Erro: {erro}"))
    finally:
        ST["v"] = "DESLIGADO"
        fila.put(("fim",))


# ------------------- Clima (wttr.in) -------------------

def buscar_clima():
    req = urllib.request.Request("https://wttr.in/?format=j1&lang=pt", headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read().decode("utf-8"))

    def desc(x):
        p = x.get("lang_pt")
        return p[0]["value"] if p else x["weatherDesc"][0]["value"]

    atual = d["current_condition"][0]
    area = d["nearest_area"][0]
    dias = []
    for w in d["weather"][:3]:
        meio = w["hourly"][4]  # por volta do meio-dia
        dias.append({
            "data": datetime.strptime(w["date"], "%Y-%m-%d"),
            "max": w["maxtempC"], "min": w["mintempC"],
            "desc": desc(meio), "chuva": meio.get("chanceofrain", "0"),
        })
    return {
        "local": f'{area["areaName"][0]["value"]}, {area["country"][0]["value"]}',
        "temp": atual["temp_C"], "sens": atual["FeelsLikeC"], "umid": atual["humidity"],
        "vento": atual["windspeedKmph"], "desc": desc(atual), "dias": dias,
    }


def laco_clima(hud):
    while True:
        try:
            hud.clima = buscar_clima()
            espera = 1800
        except Exception:
            espera = 120
        time.sleep(espera)


# ------------------- Cotação do dólar (AwesomeAPI, sem chave) -------------------

def buscar_cotacao():
    cab = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request("https://economia.awesomeapi.com.br/json/last/USD-BRL", headers=cab)
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read().decode("utf-8"))["USDBRL"]
    bid = float(d["bid"])
    dados = {"bid": bid, "pct": float(d.get("pctChange") or 0),
             "alta": float(d.get("high") or bid), "baixa": float(d.get("low") or bid), "hist": []}
    try:  # últimos 30 dias, para o gráfico
        req = urllib.request.Request("https://economia.awesomeapi.com.br/json/daily/USD-BRL/30", headers=cab)
        with urllib.request.urlopen(req, timeout=12) as r:
            dias = json.loads(r.read().decode("utf-8"))
        dados["hist"] = [float(x["bid"]) for x in reversed(dias)]
    except Exception:
        pass
    return dados


def laco_cotacao(hud):
    while True:
        try:
            hud.cotacao = buscar_cotacao()
            espera = 120
        except Exception:
            espera = 60
        time.sleep(espera)


def icone_clima(desc: str) -> str:
    d = desc.lower()
    if "trovoad" in d:
        return "⚡"
    if any(p in d for p in ("chuv", "garoa", "pancada", "aguaceiro")):
        return "☂"
    if any(p in d for p in ("neve", "gelo")):
        return "❄"
    if any(p in d for p in ("nubl", "encob", "nuvens", "nebl")):
        return "☁"
    return "☀"


# ------------------- Sensores do PC -------------------

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class ENERGIA(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class MIB_IFROW(ctypes.Structure):
    """Linha da tabela de interfaces de rede do Windows (iphlpapi.GetIfTable)."""
    _fields_ = [
        ("wszName", ctypes.c_wchar * 256),
        ("dwIndex", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwMtu", wintypes.DWORD),
        ("dwSpeed", wintypes.DWORD), ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", ctypes.c_ubyte * 8),
        ("dwAdminStatus", wintypes.DWORD), ("dwOperStatus", wintypes.DWORD),
        ("dwLastChange", wintypes.DWORD),
        ("dwInOctets", wintypes.DWORD), ("dwInUcastPkts", wintypes.DWORD),
        ("dwInNUcastPkts", wintypes.DWORD), ("dwInDiscards", wintypes.DWORD),
        ("dwInErrors", wintypes.DWORD), ("dwInUnknownProtos", wintypes.DWORD),
        ("dwOutOctets", wintypes.DWORD), ("dwOutUcastPkts", wintypes.DWORD),
        ("dwOutNUcastPkts", wintypes.DWORD), ("dwOutDiscards", wintypes.DWORD),
        ("dwOutErrors", wintypes.DWORD), ("dwOutQLen", wintypes.DWORD),
        ("dwDescrLen", wintypes.DWORD), ("bDescr", ctypes.c_ubyte * 256),
    ]


class Sensores:
    """Lê o uso de CPU, memória, disco, GPU (NVIDIA), bateria e rede. Não precisa instalar nada."""

    def __init__(self):
        self.cpu = 0.0
        self.ram = 0.0
        self.ram_usada = 0.0
        self.ram_total = 0.0
        self.uptime = 0.0
        self.bateria = None      # None = o PC não tem bateria
        self.carregando = False
        self.gpu = None          # None = sem GPU NVIDIA detectada
        self.gpu_temp = None
        self.rx = 0.0            # download em bytes/s
        self.tx = 0.0            # upload em bytes/s
        # discos fixos do PC (C:, D:...), até 3
        self.discos = [{"letra": l, "pct": 0.0, "livre": 0.0, "total": 0.0} for l in self._listar_discos()]

        self._proximo = 0.0
        self._gpu_proximo = 0.0
        self._gpu_ocupado = False
        self._gpu_ok = shutil.which("nvidia-smi") is not None
        self._net_prev = None
        try:
            self._t0 = self._tempos_cpu()
        except Exception:
            self._t0 = (0, 0, 0)

    # --- leituras do Windows ---
    def _tempos_cpu(self):
        k = ctypes.windll.kernel32
        ocioso, kernel, usuario = (wintypes.FILETIME() for _ in range(3))
        k.GetSystemTimes(ctypes.byref(ocioso), ctypes.byref(kernel), ctypes.byref(usuario))
        ft = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
        return ft(ocioso), ft(kernel), ft(usuario)

    def _memoria(self):
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.dwMemoryLoad, ms.ullTotalPhys, ms.ullAvailPhys

    def _energia(self):
        e = ENERGIA()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e)):
            return None
        if e.BatteryFlag == 128 or e.BatteryLifePercent == 255:
            return None  # sem bateria
        return e.BatteryLifePercent, e.ACLineStatus == 1

    def _bytes_rede(self):
        """Total de bytes recebidos/enviados por todas as interfaces ativas (menos loopback)."""
        iph = ctypes.windll.iphlpapi
        tam = wintypes.DWORD(0)
        iph.GetIfTable(None, ctypes.byref(tam), False)
        buf = ctypes.create_string_buffer(tam.value)
        if iph.GetIfTable(buf, ctypes.byref(tam), False) != 0:
            return None
        n = wintypes.DWORD.from_buffer(buf).value
        linhas = (MIB_IFROW * n).from_buffer(buf, 4)
        entrada = saida = 0
        for l in linhas:
            if l.dwType == 24 or l.dwOperStatus < 4:  # loopback / desconectada
                continue
            entrada += l.dwInOctets
            saida += l.dwOutOctets
        return entrada, saida

    def _listar_discos(self):
        letras = []
        try:
            k = ctypes.windll.kernel32
            mascara = k.GetLogicalDrives()
            for i in range(26):
                if mascara & (1 << i):
                    letra = chr(65 + i) + ":"
                    if k.GetDriveTypeW(letra + "\\") == 3:  # 3 = disco fixo
                        letras.append(letra)
        except Exception:
            pass
        return (letras or [os.environ.get("SystemDrive", "C:")])[:3]

    def _tempo_ligado(self):
        k = ctypes.windll.kernel32
        k.GetTickCount64.restype = ctypes.c_ulonglong
        return k.GetTickCount64() / 1000

    # --- atualização (no máximo 1 vez por segundo) ---
    def atualizar(self, agora):
        if agora < self._proximo:
            return False
        self._proximo = agora + 1.0

        # cada leitura é independente: se uma falhar, as outras continuam funcionando
        try:
            t = self._tempos_cpu()
            ocioso, kernel, usuario = (t[i] - self._t0[i] for i in range(3))
            total = kernel + usuario  # o tempo do kernel já inclui o ocioso
            if total > 0:
                self.cpu = max(0.0, min(100.0, (total - ocioso) / total * 100))
            self._t0 = t
        except Exception:
            pass

        try:
            carga, total_b, livre_b = self._memoria()
            self.ram = float(carga)
            self.ram_total = total_b / 1024 ** 3
            self.ram_usada = (total_b - livre_b) / 1024 ** 3
        except Exception:
            pass

        for d in self.discos:
            try:
                uso = shutil.disk_usage(d["letra"] + "\\")
                d["pct"] = uso.used / uso.total * 100
                d["livre"] = uso.free / 1024 ** 3
                d["total"] = uso.total / 1024 ** 3
            except Exception:
                pass

        try:
            self.uptime = self._tempo_ligado()
        except Exception:
            pass

        try:
            energia = self._energia()
            if energia:
                self.bateria, self.carregando = energia
            else:
                self.bateria = None
        except Exception:
            pass

        try:
            n = self._bytes_rede()
            if n:
                if self._net_prev:
                    dt = max(0.2, agora - self._net_prev[2])
                    self.rx = ((n[0] - self._net_prev[0]) & 0xFFFFFFFF) / dt
                    self.tx = ((n[1] - self._net_prev[1]) & 0xFFFFFFFF) / dt
                self._net_prev = (n[0], n[1], agora)
        except Exception:
            pass

        if self._gpu_ok and not self._gpu_ocupado and agora >= self._gpu_proximo:
            self._gpu_proximo = agora + 3.0
            self._gpu_ocupado = True
            threading.Thread(target=self._ler_gpu, daemon=True).start()
        return True

    def _ler_gpu(self):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4, creationflags=0x08000000,
            )
            uso, temp = [v.strip() for v in r.stdout.strip().splitlines()[0].split(",")]
            self.gpu = float(uso)
            self.gpu_temp = int(temp) if temp.isdigit() else None
        except Exception:
            self._gpu_ok = False
            self.gpu = None
        finally:
            self._gpu_ocupado = False


# ------------------------- A tela -------------------------

class HUD:
    def __init__(self, root, fila):
        self.root = root
        self.fila = fila
        self.canvas = tk.Canvas(root, bg=FUNDO, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        cor, vel, pulso, amp = ESTADOS["INICIANDO"]
        self.cor = hex_rgb(cor)
        self.vel, self.pulso, self.amp = vel, pulso, amp
        self.fase = 0.0
        self.giro_radar = 0.0  # ângulo da varredura do radar (graus)
        self.nivel = 0.0  # volume da sua voz (0 a 1), suavizado
        self.linhas = deque(maxlen=6)  # (papel, texto)
        self.ultimo = time.time()
        self.sensores = Sensores()
        self.mostra = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "bat": 0.0}
        self.hist_cpu = deque(maxlen=60)
        self.hist_ram = deque(maxlen=60)
        self.hist_rx = deque(maxlen=40)
        self.hist_tx = deque(maxlen=40)
        self.clima = None  # preenchido por laco_clima
        self.cotacao = None  # preenchido por laco_cotacao
        self.s = 1.0  # escala da tela (muda com o tamanho da janela)

        # "alvos" fixos que aparecem nos radares
        rnd = random.Random(7)
        self.blips = [(rnd.uniform(0, 360), rnd.uniform(0.25, 0.9), rnd.uniform(0, 6)) for _ in range(6)]

        root.bind("<F11>", self.alternar_tela_cheia)
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        self.quadro()

    def alternar_tela_cheia(self, _=None):
        atual = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not atual)

    # ---- laço principal da animação ----
    def quadro(self):
        agora = time.time()
        dt = min(0.1, agora - self.ultimo)
        self.ultimo = agora

        while True:
            try:
                item = self.fila.get_nowait()
            except queue.Empty:
                break
            if item[0] == "log":
                self.linhas.append((item[1], item[2]))
            elif item[0] == "fim":
                self.root.after(2500, self.root.destroy)

        # suaviza a transição entre estados
        cor, vel, pulso, amp = ESTADOS.get(ST["v"], ESTADOS["EM ESPERA"])
        k = min(1.0, dt * 6)
        kv = min(1.0, dt * 2.0)  # a velocidade muda devagar, como um motor ganhando ritmo
        self.cor = misturar(self.cor, hex_rgb(cor), k)
        self.vel += (vel - self.vel) * kv
        self.pulso += (pulso - self.pulso) * k
        self.amp += (amp - self.amp) * k
        alvo = 0.0
        if agora - NIVEL["t"] < 0.35:  # microfone aberto agora
            alvo = min(1.0, max(0.0, (NIVEL["v"] - 120) / 2500) ** 0.6)
        suavidade = 0.55 if alvo > self.nivel else 0.12  # sobe rápido, desce devagar
        self.nivel += (alvo - self.nivel) * suavidade
        self.fase += dt * (self.vel + self.nivel * 1.2)
        self.giro_radar += dt * VELOCIDADE_RADAR

        sn = self.sensores
        if sn.atualizar(agora):  # a cada segundo guarda um ponto para os gráficos
            self.hist_cpu.append(sn.cpu)
            self.hist_ram.append(sn.ram)
            self.hist_rx.append(sn.rx)
            self.hist_tx.append(sn.tx)
        alvos = {"cpu": sn.cpu, "ram": sn.ram,
                 "gpu": sn.gpu or 0.0, "bat": sn.bateria or 0.0}
        for d in sn.discos:
            alvos["disco:" + d["letra"]] = d["pct"]
        for chave, valor in alvos.items():  # barras deslizam suavemente
            atual = self.mostra.get(chave, 0.0)
            self.mostra[chave] = atual + (valor - atual) * min(1.0, dt * 4)

        try:
            self.desenhar(agora)
        except tk.TclError:
            return  # janela fechada
        self.root.after(33, self.quadro)

    # ---- peças de desenho ----
    def fonte(self, tam, negrito=False):
        return ("Consolas", max(7, int(tam * self.s)), "bold" if negrito else "normal")

    @staticmethod
    def cor_carga(carga, normal):
        if carga >= 90:
            return "#ff4d4d"
        if carga >= 75:
            return "#ffb020"
        return normal

    def caixa(self, c, x0, y0, x1, y1, titulo, cores):
        """Painel com um canto cortado e título."""
        cor, medio, fraco = cores
        corte = 14 * self.s
        c.create_polygon(x0, y0, x1 - corte, y0, x1, y0 + corte, x1, y1, x0 + corte, y1, x0, y1 - corte,
                         fill="", outline=fraco, width=1)
        c.create_line(x0, y0, x0 + 34 * self.s, y0, fill=cor, width=2)
        c.create_text(x0 + 6 * self.s, y0 + 6 * self.s, text=titulo, anchor="nw", fill=medio,
                      font=self.fonte(8, True))

    def barra(self, c, x0, y, w, pct, col, fraco, n=20, altura=7):
        """Barra segmentada."""
        h = altura * self.s
        c.create_rectangle(x0, y, x0 + w, y + h, outline=fraco)
        passo = w / n
        cheios = int(round(n * max(0.0, min(100.0, pct)) / 100))
        for i in range(cheios):
            c.create_rectangle(x0 + i * passo + 1, y + 2, x0 + (i + 1) * passo - 1, y + h - 2,
                               fill=col, outline="")

    def medidor(self, c, x, y, r, pct, col, titulo, ang, cores, ticks=True, texto=None, tam=None):
        """Medidor circular: arco de progresso, marcas e arcos girando."""
        cor, medio, fraco = cores
        if ticks:
            for i in range(36):
                a = math.radians(i * 10)
                r1, r2 = r + 5 * self.s, r + (10 if i % 3 == 0 else 8) * self.s
                c.create_line(x + math.cos(a) * r1, y + math.sin(a) * r1,
                              x + math.cos(a) * r2, y + math.sin(a) * r2,
                              fill=medio if i % 3 == 0 else fraco)
        c.create_oval(x - r, y - r, x + r, y + r, outline=fraco, width=5 if ticks else 3)
        if pct > 0.5:
            c.create_arc(x - r, y - r, x + r, y + r, start=90, extent=-3.6 * min(pct, 99.9),
                         style="arc", outline=col, width=5 if ticks else 3)
        ri = r - 9 * self.s
        for k in range(2):
            c.create_arc(x - ri, y - ri, x + ri, y + ri, start=ang + k * 180, extent=50,
                         style="arc", outline=medio, width=2)
        c.create_text(x, y - (8 if ticks else 0) * self.s, text=texto or f"{pct:.0f}%", fill=col,
                      font=self.fonte(tam or (15 if ticks else 9), True))
        if ticks:
            c.create_text(x, y + 12 * self.s, text=titulo, fill=medio, font=self.fonte(8))

    def radar(self, c, x, y, r, t, ang, cores):
        """Radar com varredura e alvos piscando."""
        cor, medio, fraco = cores
        for f in (0.72, 0.45, 0.2):
            c.create_oval(x - r * f, y - r * f, x + r * f, y + r * f, outline=fraco)
        c.create_oval(x - r, y - r, x + r, y + r, outline=medio, width=2)
        c.create_line(x - r, y, x + r, y, fill=fraco)
        c.create_line(x, y - r, x, y + r, fill=fraco)
        for i in range(0, 360, 10):
            a = math.radians(i)
            tam = 8 if i % 30 == 0 else 4
            c.create_line(x + math.cos(a) * (r + 3), y + math.sin(a) * (r + 3),
                          x + math.cos(a) * (r + 3 + tam * self.s), y + math.sin(a) * (r + 3 + tam * self.s),
                          fill=medio if i % 30 == 0 else fraco)
        base = -ang * 1.3
        for k in range(7):  # varredura com rastro
            c.create_arc(x - r, y - r, x + r, y + r, start=base - k * 7, extent=7, style="pieslice",
                         fill=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.55 - k * 0.07)), outline="")
        c.create_line(x, y, x + math.cos(math.radians(-base)) * r, y - math.sin(math.radians(-base)) * r,
                      fill=cor, width=2)
        for (a, d, f) in self.blips:
            if (math.sin(t * 2 + f) + 1) / 2 > 0.35:
                rr = r * d
                px = x + math.cos(math.radians(a)) * rr
                py = y + math.sin(math.radians(a)) * rr
                c.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5, fill=cor, outline="")
        c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=cor, outline="")

    def engrenagem(self, c, x, y, r, ang, cores):
        cor, medio, fraco = cores
        c.create_oval(x - r, y - r, x + r, y + r, outline=medio, width=2)
        for i in range(12):
            a = math.radians(i * 30 + ang)
            c.create_line(x + math.cos(a) * r, y + math.sin(a) * r,
                          x + math.cos(a) * (r + 4 * self.s), y + math.sin(a) * (r + 4 * self.s),
                          fill=fraco, width=2)
        ri = r * 0.6
        c.create_arc(x - ri, y - ri, x + ri, y + ri, start=-ang * 2, extent=90, style="arc", outline=cor, width=2)
        c.create_arc(x - ri, y - ri, x + ri, y + ri, start=-ang * 2 + 180, extent=90, style="arc", outline=cor, width=2)
        c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=cor, outline="")

    def regua(self, c, W, cores):
        """Régua de dias do mês no topo, com o dia de hoje destacado."""
        cor, medio, fraco = cores
        s = self.s
        agora = datetime.now()
        n = calendar.monthrange(agora.year, agora.month)[1]
        xa, xb = 46 * s, W - 46 * s
        passo = (xb - xa) / (n - 1)
        y = 16 * s
        c.create_line(xa - 14 * s, 33 * s, xb + 14 * s, 33 * s, fill=fraco)
        for i in range(n):
            x = xa + i * passo
            atual = (i + 1) == agora.day
            if atual:
                c.create_rectangle(x - 12 * s, y - 9 * s, x + 12 * s, y + 9 * s, fill=fraco, outline=cor)
            c.create_text(x, y, text=f"{i + 1:02d}", fill=cor if atual else medio, font=self.fonte(8, atual))
            c.create_line(x, (27 if atual else 29) * s, x, 38 * s, fill=cor if atual else medio, width=2 if atual else 1)
            if i < n - 1:
                for k in range(1, 4):
                    xx = x + passo * k / 4
                    c.create_line(xx, 31 * s, xx, 35 * s, fill=fraco)

    def estado_pc(self, cor):
        """Resume a saúde do PC: (nome, cor, texto)."""
        sn, m = self.sensores, self.mostra
        alertas, critico = [], False
        if m["cpu"] >= 85:
            alertas.append("CPU alta")
            critico = critico or m["cpu"] >= 97
        if m["ram"] >= 85:
            alertas.append("RAM alta")
            critico = critico or m["ram"] >= 95
        for d in sn.discos:
            valor = m.get("disco:" + d["letra"], 0.0)
            if valor >= 92:
                alertas.append(f"disco {d['letra']} quase cheio")
                critico = critico or valor >= 97
        if sn.gpu_temp is not None and sn.gpu_temp >= 82:
            alertas.append("GPU quente")
            critico = critico or sn.gpu_temp >= 92
        if sn.bateria is not None and sn.bateria <= 15 and not sn.carregando:
            alertas.append("bateria fraca")
        if critico:
            nome, col = "CRÍTICO", "#ff4d4d"
        elif alertas:
            nome, col = "ATENÇÃO", "#ffb020"
        else:
            nome, col = "NORMAL", cor
        return nome, col, (", ".join(alertas) if alertas else "tudo funcionando bem")

    # ---- coluna da esquerda ----
    def coluna_esquerda(self, c, x0, t, ang, cores, H):
        cor, medio, fraco = cores
        s, sn, m = self.s, self.sensores, self.mostra
        L = 230 * s
        agora = datetime.now()
        ndias = calendar.monthrange(agora.year, agora.month)[1]

        d, resto = divmod(int(sn.uptime), 86400)
        h, resto = divmod(resto, 3600)
        ligado = (f"{d}d " if d else "") + f"{h}h {resto // 60:02d}min"

        # calendário: anel com o dia do mês
        self.medidor(c, x0 + 64 * s, 128 * s, 48 * s, agora.day / ndias * 100, cor,
                     MESES_FULL[agora.month - 1], ang, cores, texto=f"{agora.day:02d}", tam=22)
        xt = x0 + 132 * s
        c.create_text(xt, 108 * s, text=DIAS_FULL[agora.weekday()], anchor="w", fill=cor, font=self.fonte(9, True))
        c.create_text(xt, 127 * s, text=f"{agora.day:02d} {MESES[agora.month - 1]} {agora.year}", anchor="w",
                      fill=medio, font=self.fonte(8))
        c.create_text(xt, 144 * s, text=f"DIA {agora.timetuple().tm_yday} DO ANO", anchor="w", fill=fraco,
                      font=self.fonte(7))

        # armazenamento
        yb = 206 * s
        altura = (26 + 31 * len(sn.discos)) * s
        self.caixa(c, x0, yb, x0 + L, yb + altura, "ARMAZENAMENTO", cores)
        for i, dk in enumerate(sn.discos):
            v = m.get("disco:" + dk["letra"], 0.0)
            y = yb + (28 + i * 31) * s
            col = self.cor_carga(v, cor)
            c.create_text(x0 + 8 * s, y, text=f"DISCO {dk['letra']}", anchor="nw", fill=medio, font=self.fonte(8))
            c.create_text(x0 + L - 8 * s, y, text=f"{v:.0f}%", anchor="ne", fill=col, font=self.fonte(8, True))
            self.barra(c, x0 + 8 * s, y + 13 * s, L - 16 * s, v, col, fraco, altura=6)
            c.create_text(x0 + L - 40 * s, y, text=f"{dk['livre']:.0f} GB livres", anchor="ne", fill=fraco,
                          font=self.fonte(7))

        # energia
        ye = yb + altura + 54 * s
        if sn.bateria is not None:
            pct, texto = m["bat"], f"{m['bat']:.0f}%"
            estado = "CARREGANDO" if sn.carregando else "NA BATERIA"
            col = self.cor_carga(100 - pct, cor)
        else:
            pct, texto, estado, col = 100.0, "AC", "NA TOMADA", cor
        self.medidor(c, x0 + 46 * s, ye, 34 * s, pct, col, "ENERGIA", ang, cores, texto=texto, tam=12)
        xe = x0 + 100 * s
        c.create_text(xe, ye - 24 * s, text="ENERGIA", anchor="w", fill=medio, font=self.fonte(8))
        c.create_text(xe, ye - 8 * s, text=estado, anchor="w", fill=col, font=self.fonte(9, True))
        c.create_text(xe, ye + 12 * s, text="LIGADO HÁ", anchor="w", fill=fraco, font=self.fonte(7))
        c.create_text(xe, ye + 27 * s, text=ligado, anchor="w", fill=cor, font=self.fonte(9, True))

        # chips de estado
        yc = ye + 52 * s
        chips = [("CPU", m["cpu"], True), ("RAM", m["ram"], True),
                 ("DSK", max([m.get("disco:" + dk["letra"], 0.0) for dk in sn.discos] or [0.0]), True),
                 ("GPU", m["gpu"], sn.gpu is not None), ("BAT", 100 - m["bat"], sn.bateria is not None)]
        w = 40 * s
        for i, (nome, carga, existe) in enumerate(chips):
            xa = x0 + i * (w + 6 * s)
            col_c = self.cor_carga(carga, cor) if existe else fraco
            c.create_rectangle(xa, yc, xa + w, yc + 20 * s, outline=col_c)
            c.create_text(xa + w / 2, yc + 10 * s, text=nome, fill=col_c, font=self.fonte(8, True))

        # onda de voz
        yw, hw = yc + 58 * s, 24 * s
        n = 48
        passo = L / n
        for i in range(n):
            env = math.sin(math.pi * (i + 0.5) / n) ** 0.7
            v = (self.amp + self.nivel * 38) / 30
            hh = max(1.5, hw * min(1.0, 0.12 + v) * env *
                     (0.2 + 0.8 * abs(math.sin(t * 4 + i * 0.7) * math.sin(t * 1.7 + i * 0.23))))
            xx = x0 + i * passo + passo / 2
            c.create_line(xx, yw - hh, xx, yw + hh, fill=cor if hh > 8 * s else medio, width=2)

        # status do sistema + histórico (embaixo)
        hp = 160 * s
        y0 = H - 30 * s - hp
        self.caixa(c, x0, y0, x0 + L, y0 + hp, "STATUS DO SISTEMA", cores)
        nome, col_status, dica = self.estado_pc(cor)
        c.create_text(x0 + 8 * s, y0 + 24 * s, text=nome, anchor="nw", fill=col_status, font=self.fonte(15, True))
        c.create_text(x0 + 8 * s, y0 + 48 * s, text=dica, anchor="nw", fill=medio, font=self.fonte(8))
        c.create_text(x0 + L - 8 * s, y0 + 26 * s, text=f"RAM\n{sn.ram_usada:.1f}/{sn.ram_total:.1f} GB",
                      anchor="ne", fill=fraco, font=self.fonte(8), justify="right")
        gx0, gx1 = x0 + 8 * s, x0 + L - 8 * s
        gy0, gy1 = y0 + 70 * s, y0 + hp - 12 * s
        c.create_rectangle(gx0, gy0, gx1, gy1, outline=fraco)
        for i in range(1, 4):
            yy = gy0 + (gy1 - gy0) * i / 4
            c.create_line(gx0, yy, gx1, yy, fill=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.12)))
        for hist, col in ((self.hist_ram, medio), (self.hist_cpu, cor)):
            pts = []
            for i, v in enumerate(hist):
                pts += [gx1 - (len(hist) - 1 - i) * (gx1 - gx0) / 59, gy1 - (gy1 - gy0) * min(v, 100) / 100]
            if len(pts) >= 4:
                c.create_line(*pts, fill=col, width=2)
        c.create_text(gx0 + 3, gy0 + 2, text="CPU", anchor="nw", fill=cor, font=self.fonte(7))
        c.create_text(gx0 + 30 * s, gy0 + 2, text="RAM", anchor="nw", fill=medio, font=self.fonte(7))

    # ---- coluna da direita ----
    def clima_painel(self, c, x0, x1, y, cores):
        cor, medio, fraco = cores
        s = self.s
        h = 246 * s
        self.caixa(c, x0, y, x1, y + h, "CLIMA", cores)
        cl = self.clima
        if not cl:
            c.create_text(x0 + 8 * s, y + 30 * s, text="Buscando previsão...", anchor="nw", fill=fraco,
                          font=self.fonte(8))
            return
        c.create_text(x0 + 8 * s, y + 22 * s, text=cl["local"][:30].upper(), anchor="nw", fill=medio,
                      font=self.fonte(8))
        c.create_text(x0 + 8 * s, y + 38 * s, text=f"{cl['temp']}°C", anchor="nw", fill=cor, font=self.fonte(24, True))
        c.create_text(x1 - 8 * s, y + 40 * s, text=cl["desc"][:18], anchor="ne", fill=medio, font=self.fonte(8, True))
        c.create_text(x1 - 8 * s, y + 56 * s, text=f"SENS. {cl['sens']}°C", anchor="ne", fill=fraco, font=self.fonte(7))
        c.create_text(x1 - 8 * s, y + 70 * s, text=f"UMID. {cl['umid']}%", anchor="ne", fill=fraco, font=self.fonte(7))
        c.create_text(x1 - 8 * s, y + 84 * s, text=f"VENTO {cl['vento']} km/h", anchor="ne", fill=fraco,
                      font=self.fonte(7))
        for i, dia in enumerate(cl["dias"]):
            yy = y + (104 + i * 46) * s
            c.create_line(x0 + 8 * s, yy - 4 * s, x1 - 8 * s, yy - 4 * s, fill=fraco)
            ix, iy, ir = x0 + 24 * s, yy + 18 * s, 14 * s
            c.create_oval(ix - ir, iy - ir, ix + ir, iy + ir, outline=medio, width=2)
            c.create_text(ix, iy, text=icone_clima(dia["desc"]), fill=cor, font=self.fonte(12))
            rotulo = "HOJE" if i == 0 else "AMANHÃ" if i == 1 else DIAS[dia["data"].weekday()]
            c.create_text(x0 + 46 * s, yy + 8 * s, text=rotulo, anchor="w", fill=medio, font=self.fonte(8, True))
            c.create_text(x0 + 46 * s, yy + 22 * s, text=dia["desc"][:22], anchor="w", fill=fraco, font=self.fonte(7))
            c.create_text(x1 - 8 * s, yy + 8 * s, text=f"{dia['min']}°/{dia['max']}°", anchor="e", fill=cor,
                          font=self.fonte(9, True))
            c.create_text(x1 - 8 * s, yy + 22 * s, text=f"chuva {dia['chuva']}%", anchor="e", fill=fraco,
                          font=self.fonte(7))

    def cotacao_painel(self, c, x0, x1, y, h, cores):
        cor, medio, fraco = cores
        s = self.s
        self.caixa(c, x0, y, x1, y + h, "DÓLAR · USD/BRL", cores)
        cot = self.cotacao
        if not cot:
            c.create_text(x0 + 8 * s, y + 30 * s, text="Buscando cotação...", anchor="nw", fill=fraco,
                          font=self.fonte(8))
            return
        subiu = cot["pct"] >= 0
        col = "#4dffb8" if subiu else "#ff4d4d"
        valor = f"{cot['bid']:.2f}".replace(".", ",")
        c.create_text(x0 + 8 * s, y + 20 * s, text=f"R$ {valor}", anchor="nw", fill=cor, font=self.fonte(17, True))
        variacao = f"{abs(cot['pct']):.2f}".replace(".", ",")
        c.create_text(x0 + 8 * s, y + 50 * s, text=f"{'▲' if subiu else '▼'} {variacao}%", anchor="nw",
                      fill=col, font=self.fonte(9, True))
        minimo = f"{cot['baixa']:.2f}".replace(".", ",")
        maximo = f"{cot['alta']:.2f}".replace(".", ",")
        c.create_text(x0 + 8 * s, y + 66 * s, text=f"MÍN {minimo} · MÁX {maximo}", anchor="nw", fill=fraco,
                      font=self.fonte(7))
        # gráfico dos últimos 30 dias
        gx0, gx1 = x0 + 118 * s, x1 - 10 * s
        gy0, gy1 = y + 24 * s, y + 58 * s
        c.create_rectangle(gx0, gy0, gx1, gy1, outline=fraco)
        c.create_text(gx0 + 3, gy0 + 2, text="30 DIAS", anchor="nw", fill=fraco, font=self.fonte(7))
        hist = cot["hist"]
        if len(hist) >= 2:
            lo, hi = min(hist), max(hist)
            faixa = max(hi - lo, 0.01)
            pts = []
            for i, v in enumerate(hist):
                pts += [gx0 + 4 + (gx1 - gx0 - 8) * i / (len(hist) - 1),
                        gy1 - 4 - (gy1 - gy0 - 8) * (v - lo) / faixa]
            c.create_line(*pts, fill=cor, width=2)
            c.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3, fill=col, outline="")

    def rede_painel(self, c, x0, x1, H, cores):
        cor, medio, fraco = cores
        s, sn = self.s, self.sensores
        hp = 140 * s
        y0 = H - 30 * s - hp
        self.caixa(c, x0, y0, x1, y0 + hp, "REDE", cores)
        c.create_text(x0 + 8 * s, y0 + 24 * s, text=f"▼ {fmt_vel(sn.rx)}", anchor="nw", fill=cor,
                      font=self.fonte(9, True))
        c.create_text(x1 - 8 * s, y0 + 24 * s, text=f"▲ {fmt_vel(sn.tx)}", anchor="ne", fill="#ffb020",
                      font=self.fonte(9, True))
        gx0, gx1 = x0 + 8 * s, x1 - 8 * s
        gy0, gy1 = y0 + 46 * s, y0 + hp - 12 * s
        c.create_rectangle(gx0, gy0, gx1, gy1, outline=fraco)
        pico = max(50 * 1024, max(self.hist_rx, default=0), max(self.hist_tx, default=0))
        n = 40
        passo = (gx1 - gx0) / n
        rx = list(self.hist_rx)[-n:]
        tx = list(self.hist_tx)[-n:]
        for i, v in enumerate(rx):  # barras alinhadas à direita
            xa = gx0 + (n - len(rx) + i) * passo
            alt = max(1, (gy1 - gy0 - 2) * v / pico)
            c.create_rectangle(xa + 1, gy1 - alt, xa + passo - 1, gy1, fill=cor, outline="")
        pts = []
        for i, v in enumerate(tx):
            pts += [gx0 + (n - len(tx) + i) * passo + passo / 2, gy1 - (gy1 - gy0 - 2) * v / pico]
        if len(pts) >= 4:
            c.create_line(*pts, fill="#ffb020", width=2)

    def coluna_direita(self, c, x1, t, ang, cores, W, H):
        cor, medio, fraco = cores
        s = self.s
        L = 230 * s
        x0 = x1 - L

        # clima
        self.clima_painel(c, x0, x1, 48 * s, cores)

        # cotação do dólar (logo acima da rede) e registro da conversa (usa o espaço que sobra)
        h_rede, h_cot = 140 * s, 82 * s
        y_cot = H - 30 * s - h_rede - 10 * s - h_cot
        self.cotacao_painel(c, x0, x1, y_cot, h_cot, cores)
        yl = 310 * s
        hl = max(60 * s, y_cot - 10 * s - yl)
        self.caixa(c, x0, yl, x1, yl + hl, "REGISTRO", cores)
        cores_papel = {"JARVIS": cor, "AÇÃO": "#9fe8ff", "SISTEMA": "#ffb020"}
        cabem = max(1, int((hl - 30 * s) / (31 * s)))
        for i, (papel, texto) in enumerate(list(self.linhas)[-cabem:]):
            txt = f"{papel}: {texto}"
            if len(txt) > 62:
                txt = txt[:61] + "…"
            c.create_text(x0 + 8 * s, yl + (26 + i * 31) * s, text=txt, anchor="nw", width=L - 16 * s,
                          fill=cores_papel.get(papel, medio), font=self.fonte(8))

        # rede (embaixo)
        self.rede_painel(c, x0, x1, H, cores)

    # ---- cartão de notícia (imagem + manchete) ----
    def _foto_noticia(self, dados, largura, altura):
        if not dados:
            return None
        cache = getattr(self, "_noticia_cache", (None, 0, 0, None))
        if cache[0] is dados and cache[1] == largura and cache[2] == altura:
            return cache[3]
        foto = None
        try:
            import io
            from PIL import Image, ImageTk
            im = Image.open(io.BytesIO(dados))
            try:
                im.draft("RGB", (largura * 2, altura * 2))  # abre JPEG grande bem mais rápido
            except Exception:
                pass
            im = im.convert("RGB")
            im.thumbnail((largura, altura))
            foto = ImageTk.PhotoImage(im)
        except Exception as erro:
            print(f"[rotina] não consegui mostrar a imagem da notícia ({erro}). Para ativar: pip install pillow")
        self._noticia_cache = (dados, largura, altura, foto)
        return foto

    def noticia_painel(self, c, cx, cy, cores):
        n = RT.NOTICIA
        if not n.get("ativo"):
            return
        cor, medio, fraco = cores
        s = self.s
        w = min(560 * s, c.winfo_width() - 2 * (38 + 230 * s) - 24)
        h = w * 0.78
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        c.create_rectangle(x0, y0, x1, y1, fill=FUNDO, outline="")
        self.caixa(c, x0, y0, x1, y1, f"NOTÍCIAS  {n.get('indice', 1)}/{n.get('total', 1)}", cores)
        iw, ih = int(w - 24 * s), int(h - 28 * s - 84 * s)
        iy = y0 + 30 * s
        c.create_rectangle(cx - iw / 2, iy, cx + iw / 2, iy + ih, outline=fraco)
        foto = self._foto_noticia(n.get("imagem"), iw - 2, ih - 2)
        if foto:
            c.create_image(cx, iy + ih / 2, image=foto)
        else:
            c.create_text(cx, iy + ih / 2, text="SEM IMAGEM", fill=fraco, font=self.fonte(10, True))
        c.create_text(cx, iy + ih + 10 * s, text=n.get("titulo", ""), anchor="n", width=w - 24 * s,
                      justify="center", fill=cor, font=self.fonte(11, True))

    # ---- gráfico de porcentagens (barras para cima) ----
    def grafico_painel(self, c, cx, cy, cores):
        g = RT.GRAFICO
        dados = list(g.get("dados") or [])
        if not g.get("ativo") or not dados:
            self._graf_chave = None
            return
        cor, medio, fraco = cores
        s = self.s
        chave = tuple(dados)
        if getattr(self, "_graf_chave", None) != chave:  # gráfico novo: as barras crescem do zero
            self._graf_chave = chave
            self._graf_inicio = time.time()
        crescer = min(1.0, (time.time() - self._graf_inicio) / 0.7)
        crescer = 1 - (1 - crescer) ** 3  # começa rápido e desacelera no fim

        w = min(560 * s, c.winfo_width() - 2 * (38 + 230 * s) - 24)
        h = w * 0.78
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        c.create_rectangle(x0, y0, x1, y1, fill=FUNDO, outline="")
        self.caixa(c, x0, y0, x1, y1, "GRÁFICO", cores)

        n = len(dados)
        px0, px1 = x0 + 46 * s, x1 - 22 * s
        topo, base = y0 + 46 * s, y1 - 50 * s
        altura = base - topo
        maximo = max(100.0, max(v for _, v in dados))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):  # linhas de referência
            yy = base - altura * frac
            c.create_line(px0, yy, px1, yy, fill=medio if frac == 0 else fraco)
            c.create_text(px0 - 6 * s, yy, text=f"{maximo * frac:.0f}%", anchor="e", fill=fraco,
                          font=self.fonte(7))
        slot = (px1 - px0) / n
        larg = min(slot * 0.55, 70 * s)
        for i, (nome, v) in enumerate(dados):
            xc = px0 + slot * i + slot / 2
            alt = altura * min(v, maximo) / maximo * crescer
            c.create_rectangle(xc - larg / 2, topo, xc + larg / 2, base, outline=fraco)      # moldura (100%)
            c.create_rectangle(xc - larg / 2, base - alt, xc + larg / 2, base, fill=cor, outline="")
            c.create_text(xc, base - alt - 5 * s, text=f"{v:g}%".replace(".", ","), anchor="s", fill=cor,
                          font=self.fonte(11, True))
            c.create_text(xc, base + 8 * s, text=nome, anchor="n", width=slot - 6 * s, justify="center",
                          fill=medio, font=self.fonte(9, True))

    # ---- cartão de aviso / resposta (jarvis_extras) ----
    def cartao_painel(self, c, cx, cy, cores):
        k = EX.CARTAO
        linhas = k.get("linhas") or []
        if not linhas or time.time() > k.get("ate", 0):
            return
        cor, medio, fraco = cores
        s = self.s
        w = max(240 * s, min(520 * s, c.winfo_width() - 2 * (38 + 230 * s) - 24))
        util = w - 28 * s
        alturas = [max(1, math.ceil(len(l) * 8.5 * s / util)) * 17 * s + 6 * s for l in linhas]
        h = 44 * s + sum(alturas)
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        c.create_rectangle(x0, y0, x1, y1, fill=FUNDO, outline="")
        self.caixa(c, x0, y0, x1, y1, k.get("titulo", ""), cores)
        y = y0 + 32 * s
        for linha, a in zip(linhas, alturas):
            c.create_text(cx, y, text=linha, anchor="n", width=util, justify="center", fill=cor,
                          font=self.fonte(11, True))
            y += a

    # ---- gráfico de cotação (jarvis_extras) ----
    def linha_painel(self, c, cx, cy, cores):
        L = EX.LINHA
        pts = list(L.get("pontos") or [])
        if len(pts) < 2 or time.time() > L.get("ate", 0):
            return
        cor, medio, fraco = cores
        s = self.s
        w = max(300 * s, min(560 * s, c.winfo_width() - 2 * (38 + 230 * s) - 24))
        h = w * 0.62
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        c.create_rectangle(x0, y0, x1, y1, fill=FUNDO, outline="")
        self.caixa(c, x0, y0, x1, y1, L.get("titulo", ""), cores)
        gx0, gx1 = x0 + 20 * s, x1 - 20 * s
        gy0, gy1 = y0 + 40 * s, y1 - 46 * s
        c.create_rectangle(gx0, gy0, gx1, gy1, outline=fraco)
        lo, hi = min(pts), max(pts)
        faixa = max(hi - lo, 1e-9)
        coords = []
        for i, v in enumerate(pts):
            coords += [gx0 + (gx1 - gx0) * i / (len(pts) - 1),
                       gy1 - 4 * s - (gy1 - gy0 - 8 * s) * (v - lo) / faixa]
        c.create_line(*coords, fill=cor, width=2)
        c.create_oval(coords[-2] - 3, coords[-1] - 3, coords[-2] + 3, coords[-1] + 3, fill=cor, outline="")
        c.create_text(gx0 + 4, gy0 + 3, text=f"{hi:.2f}".replace(".", ","), anchor="nw", fill=fraco,
                      font=self.fonte(7))
        c.create_text(gx0 + 4, gy1 - 3, text=f"{lo:.2f}".replace(".", ","), anchor="sw", fill=fraco,
                      font=self.fonte(7))
        c.create_text(cx, y1 - 24 * s, text=L.get("rotulo", ""), fill=cor, font=self.fonte(13, True))

    # ---- desenho ----
    def desenhar(self, t):
        c = self.canvas
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if W < 100 or H < 100:
            return
        self.s = s = max(0.75, min(2.0, min(W / 1100, H / 760)))

        cor = rgb_hex(misturar(self.cor, (255, 255, 255), self.nivel * 0.25))
        forte = rgb_hex(misturar(self.cor, (255, 255, 255), 0.35))
        medio = rgb_hex(misturar(FUNDO_RGB, self.cor, 0.65))
        fraco = rgb_hex(misturar(FUNDO_RGB, self.cor, 0.30))
        grade = rgb_hex(misturar(FUNDO_RGB, self.cor, 0.06))
        cores = (cor, medio, fraco)
        ciano_claro = rgb_hex(misturar(self.cor, (255, 255, 255), 0.7))

        cx, cy = W // 2, int(H * 0.50)
        R = int(min(W * 0.13, H * 0.215))
        ang = self.fase * VELOCIDADE_GIRO  # graus: gira devagar e acompanha o estado
        m = 16

        # brilho suave atrás do reator
        for i in range(7, 0, -1):
            rr = R * (0.9 + i * 0.28)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline="",
                          fill=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.010 * (8 - i) + 0.01)))

        # grade de fundo
        for x in range(0, W, 50):
            c.create_line(x, 0, x, H, fill=grade)
        for y in range(0, H, 50):
            c.create_line(0, y, W, y, fill=grade)

        # régua de dias no topo
        self.regua(c, W, cores)

        # cantos embaixo e barras luminosas laterais
        for xx, dx in ((m, 1), (W - m, -1)):
            c.create_line(xx, H - m - 40 * s, xx, H - m, xx + dx * 40 * s, H - m, fill=medio, width=2)
        for xx, dx in ((m + 7, 1), (W - m - 7, -1)):
            c.create_line(xx, H * 0.30, xx, H * 0.62, fill=forte, width=4)
            c.create_line(xx + dx * 8, H * 0.26, xx + dx * 8, H * 0.66, fill=medio, width=1)
            c.create_line(xx + dx * 12, H * 0.32, xx + dx * 12, H * 0.60, fill=fraco, width=1)
        c.create_line(cx - 130 * s, H - m, cx + 130 * s, H - m, fill=forte, width=3)

        # título no topo (entre os dois medidores)
        y0 = 42 * s
        c.create_polygon(cx - 170 * s, y0, cx - 140 * s, y0 + 52 * s, cx + 140 * s, y0 + 52 * s, cx + 170 * s, y0,
                         fill=FUNDO, outline=medio, width=2)
        c.create_text(cx, y0 + 22 * s, text="J.A.R.V.I.S", fill=cor, font=self.fonte(21, True))
        c.create_text(cx, y0 + 41 * s, text="JUST A RATHER VERY INTELLIGENT SYSTEM", fill=medio,
                      font=self.fonte(7))

        # medidores do topo: RAM (esquerda) e relógio (direita)
        agora = datetime.now()
        mo = self.mostra
        self.medidor(c, cx - 230 * s, 92 * s, 38 * s, mo["ram"], self.cor_carga(mo["ram"], cor), "RAM", ang,
                     cores, texto=f"{mo['ram']:.0f}%", tam=12)
        self.medidor(c, cx + 230 * s, 92 * s, 38 * s, agora.second / 60 * 100, cor, f"{agora:%S}s", ang, cores,
                     texto=f"{agora:%H:%M}", tam=12)

        # ondas de voz ao redor do reator
        n = 64
        base = R + 30
        for i in range(n):
            a = i * 2 * math.pi / n
            h = 2 + (self.amp + self.nivel * 38) * (0.25 + 0.75 * abs(math.sin(t * 5.0 + i * 0.9) * math.sin(t * 2.3 + i * 0.31)))
            c.create_line(cx + math.cos(a) * base, cy + math.sin(a) * base,
                          cx + math.cos(a) * (base + h), cy + math.sin(a) * (base + h),
                          fill=medio if h < 12 else cor, width=2)

        # marcas externas girando devagar
        for i in range(72):
            a = math.radians(i * 5 + ang * 0.25)
            r1, r2 = R + 6, R + (20 if i % 6 == 0 else 13)
            c.create_line(cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                          cx + math.cos(a) * r2, cy + math.sin(a) * r2,
                          fill=fraco if i % 6 else medio)

        # anel fino externo e arco vermelho de destaque
        ro = R + 26
        c.create_oval(cx - ro, cy - ro, cx + ro, cy + ro, outline=fraco, width=1)
        rv = R + 10
        c.create_arc(cx - rv, cy - rv, cx + rv, cy + rv, start=150 + 10 * math.sin(t * 0.8), extent=42,
                     style="arc", outline=VERMELHO, width=3)

        # anéis
        for k in range(3):
            c.create_arc(cx - R, cy - R, cx + R, cy + R, start=ang + k * 120, extent=80,
                         style="arc", outline=cor, width=5)
        rb = R - 24
        for k in range(4):
            c.create_arc(cx - rb, cy - rb, cx + rb, cy + rb, start=-ang * 1.6 + k * 90, extent=50,
                         style="arc", outline=medio, width=3)
        rc = R - 46
        c.create_oval(cx - rc, cy - rc, cx + rc, cy + rc, outline=fraco, width=1)
        for k in range(2):
            c.create_arc(cx - rc, cy - rc, cx + rc, cy + rc, start=ang * 0.8 + k * 180, extent=30,
                         style="arc", outline=forte, width=3)
        rd = R - 66
        for k in range(12):
            c.create_arc(cx - rd, cy - rd, cx + rd, cy + rd, start=ang * 2.2 + k * 30, extent=14,
                         style="arc", outline=medio, width=3)

        # núcleo com brilho (lente)
        core = R * 0.30 * (1 + self.pulso * math.sin(t * 3.2) + self.nivel * 0.30)
        for i in range(6, 0, -1):
            rr = core + i * 6
            col = rgb_hex(misturar(FUNDO_RGB, self.cor, 0.10 + (6 - i) * 0.07 + self.nivel * 0.15))
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=col, width=2)
        c.create_oval(cx - core, cy - core, cx + core, cy + core,
                      fill=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.85)), outline=forte, width=2)
        if self.nivel > 0.05:  # onda que se expande do núcleo quando você fala
            for k in range(2):
                rr = core + 14 + ((t * 90 + k * 45) % 90) * (0.4 + self.nivel)
                fade = 1 - ((t * 90 + k * 45) % 90) / 90
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, width=2,
                              outline=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.15 + 0.6 * fade * self.nivel)))
        ri = core * 0.62
        c.create_oval(cx - ri, cy - ri, cx + ri, cy + ri,
                      fill=rgb_hex(misturar(FUNDO_RGB, self.cor, 0.35)), outline=cor, width=2)
        c.create_arc(cx - ri, cy - ri, cx + ri, cy + ri, start=ang * 0.5, extent=180, style="chord",
                     fill=forte, outline="")
        rl = ri * 0.45
        c.create_oval(cx - rl, cy - rl, cx + rl, cy + rl, fill=ciano_claro, outline="")
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=FUNDO, outline="")

        # etiquetas laterais do reator
        for xx, texto, dx in ((cx - R - 52, f"{self.sensores.cpu:.0f}% CPU", -1),
                              (cx + R + 52, f"{self.sensores.ram:.0f}% RAM", 1)):
            c.create_line(xx - dx * 4, cy, xx - dx * 30, cy, fill=medio)
            c.create_oval(xx - dx * 4 - 4, cy - 4, xx - dx * 4 + 4, cy + 4, fill=cor, outline="")
            c.create_text(xx + dx * 8, cy - 14 * s, text=texto, anchor="w" if dx > 0 else "e", fill=medio,
                          font=self.fonte(8))

        # estado
        nome = ST["v"]
        c.create_text(cx, cy + R + 66 * s, text=f"◉ {nome}", fill=cor, font=self.fonte(19, True))
        c.create_text(cx, cy + R + 92 * s, text=DICAS.get(nome, ""), fill=medio, font=self.fonte(10))

        # embaixo: atalhos de voz (esquerda), engrenagens + marca (centro), radar (direita)
        xm = cx - 250 * s
        c.create_text(xm, H - 122 * s, text="ATALHOS DE VOZ", anchor="w", fill=fraco, font=self.fonte(7, True))
        for i, item in enumerate(ATALHOS):
            yy = H - 102 * s + i * 17 * s
            c.create_oval(xm - 2, yy - 2, xm + 3, yy + 3, fill=cor, outline="")
            c.create_text(xm + 12 * s, yy, text=item, anchor="w", fill=medio, font=self.fonte(8))

        gy = H - 66 * s
        for i, dx in enumerate((-52, 0, 52)):
            self.engrenagem(c, cx + dx * s, gy, (19 if i == 1 else 14) * s, ang * (1 if i != 1 else -1.4), cores)
        c.create_text(cx, H - 26 * s, text="STARK INDUSTRIES", fill=medio, font=self.fonte(12, True))

        # radar: varredura em ritmo constante (o radar multiplica o ângulo por 1.3, por isso a divisão)
        self.radar(c, cx + 210 * s, H - 70 * s, 36 * s, t, self.giro_radar / 1.3, cores)

        # painéis dos lados
        self.coluna_esquerda(c, 38, t, ang, cores, H)
        self.coluna_direita(c, W - 38, t, ang, cores, W, H)
        self.noticia_painel(c, cx, cy, cores)  # por cima de tudo, só enquanto lê notícias
        self.grafico_painel(c, cx, cy, cores)  # gráfico de porcentagens, por cima do cartão
        self.cartao_painel(c, cx, cy, cores)  # avisos e respostas dos extras
        self.linha_painel(c, cx, cy, cores)  # gráfico de cotação dos extras


# ------------------------- Início -------------------------

def principal():
    fila = queue.Queue()
    sys.stdout = Espelho(sys.stdout, fila)
    ligar_jarvis(fila)

    root = tk.Tk()
    root.title("J.A.R.V.I.S")
    root.geometry("1280x760")
    root.minsize(1000, 700)
    root.configure(bg=FUNDO)
    hud = HUD(root, fila)

    try:
        EX.iniciar(PONTE, lambda: ST["v"], hud.sensores,
                   lambda texto: fila.put(("log", "SISTEMA", texto)))
    except Exception:
        import traceback
        print("[extras] ERRO ao ligar os extras:\n" + traceback.format_exc())

    threading.Thread(target=rodar_jarvis, args=(fila,), daemon=True).start()
    threading.Thread(target=laco_clima, args=(hud,), daemon=True).start()
    threading.Thread(target=laco_cotacao, args=(hud,), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    principal()