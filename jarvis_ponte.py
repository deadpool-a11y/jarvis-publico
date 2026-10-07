"""
Ponte entre o jarvis_extras.py e o jarvis_acoes.py.
O extras espera um objeto "J" com várias funções que o jarvis_acoes.py não tem
(janelas, monitores, transcrever...). Esta classe fornece essas funções e repassa
todo o resto para o jarvis_acoes.
"""
import ctypes
import os
import subprocess
import time
import unicodedata
from ctypes import wintypes

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
SEM_JANELA = 0x08000000


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _variavel(nome):
    valor = os.environ.get(nome, "").strip()
    if not valor:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                valor = str(winreg.QueryValueEx(k, nome)[0]).strip()
        except Exception:
            valor = ""
    return valor


class Ponte:
    def __init__(self, J):
        self._J = J
        self._client = None
        u = self.user32 = ctypes.WinDLL("user32")
        u.GetForegroundWindow.restype = wintypes.HWND
        u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        k = self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            d = self._dwm = ctypes.WinDLL("dwmapi")
            d.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        except Exception:
            self._dwm = None

    def __getattr__(self, nome):
        if nome.startswith("__") or nome == "_J":
            raise AttributeError(nome)
        return getattr(self._J, nome)

    # ---------- microfone / IA ----------
    @property
    def LIMIAR(self):
        return self._J.ESTADO["limiar"]

    def escutar(self, limiar, espera_max=0):
        # o extras conta a espera em blocos de 0,1 s; o jarvis_acoes conta em SEGUNDOS
        segundos = espera_max / 10 if espera_max else 0
        return self._J.escutar(limiar, segundos)

    def transcrever(self, dados):
        return dados if isinstance(dados, str) else ""

    @property
    def MODELO(self):
        return os.environ.get("JARVIS_GEMINI_MODELO") or "gemini-2.5-flash"

    @property
    def client(self):
        if self._client is None:
            from google import genai
            chave = _variavel("GEMINI_API_KEY") or _variavel("GOOGLE_API_KEY")
            self._client = genai.Client(api_key=chave) if chave else genai.Client()
        return self._client

    # ---------- monitores e janelas ----------
    def listar_monitores(self):
        saida = []
        for (l, t, r, b) in self._J.MONITORES:
            saida.append({"esq": l, "topo": t, "dir": r, "base": b,
                          "m_esq": l, "m_topo": t, "m_dir": r, "m_base": b})
        return saida

    def _oculta(self, hwnd):
        if not self._dwm:
            return False
        v = wintypes.DWORD(0)
        try:
            self._dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            return False
        return bool(v.value)

    def janelas_visiveis(self):
        u = self.user32
        achadas = []

        def cb(hwnd, lp):
            try:
                if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0 and not self._oculta(hwnd):
                    achadas.append(hwnd)
            except Exception:
                pass
            return True

        u.EnumWindows(WNDENUMPROC(cb), 0)
        return achadas

    def info_janela(self, hwnd):
        u, k = self.user32, self._k
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = k.OpenProcess(0x1000, False, pid.value)
        if h:
            try:
                tam = wintypes.DWORD(520)
                b = ctypes.create_unicode_buffer(520)
                if k.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(tam)):
                    exe = os.path.splitext(os.path.basename(b.value))[0]
            finally:
                k.CloseHandle(h)
        return buf.value, exe

    def janelas_por_nome(self, nome):
        alvo = _norm(nome)
        if alvo in ("navegador", "browser"):
            alvo = _norm(os.path.splitext(os.path.basename(self._J.NAVEGADOR.get("exe") or ""))[0]) or alvo
        achadas = []
        for h in self.janelas_visiveis():
            titulo, exe = self.info_janela(h)
            if "j.a.r.v.i.s" in titulo.lower():
                continue
            if alvo and (alvo in _norm(titulo) or alvo in _norm(exe)):
                achadas.append(h)
        return achadas

    def area_janela(self, h):
        r = wintypes.RECT()
        self.user32.GetWindowRect(h, ctypes.byref(r))
        return max(0, r.right - r.left) * max(0, r.bottom - r.top)

    def posicionar(self, h, mon):
        try:
            u = self.user32
            u.ShowWindow(h, 9)
            l, t, r, b = mon["esq"], mon["topo"], mon["dir"], mon["base"]
            u.SetWindowPos(h, None, l + 40, t + 40, (r - l) - 80, (b - t) - 80, 0x0004 | 0x0010)
            u.ShowWindow(h, 3)
            return True
        except Exception:
            return False

    # ---------- ações usadas pelos protocolos ----------
    def achar_app(self, nome):
        if _norm(nome) in ("navegador", "browser"):
            exe = self._J.NAVEGADOR.get("exe")
            if exe:
                return {"Name": self._J.NAVEGADOR.get("nome") or "navegador", "exe": exe}
        achado = self._J.achar_app(nome)
        if not achado:
            return None
        return {"Name": achado[0], "AppID": achado[1]}

    def lancar_app(self, app):
        if app.get("exe"):
            subprocess.Popen([app["exe"]])
        else:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["AppID"]])

    def mover_nova_janela(self, antes, app, monitor):
        time.sleep(3)
        try:
            self._J.mover_janela_ativa(int(monitor))
        except Exception as erro:
            print(f"(Não consegui mover a janela: {erro})")

    def fechar_programa(self, nome):
        exes = set()
        for h in self.janelas_por_nome(nome):
            exe = self.info_janela(h)[1]
            if exe and exe.lower() not in ("explorer", "python", "pythonw"):
                exes.add(exe)
        for exe in exes:
            subprocess.run(["taskkill", "/F", "/IM", exe + ".exe"], capture_output=True,
                           timeout=20, creationflags=SEM_JANELA)

    def abrir_site(self, url):
        url = str(url).strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._J._abrir_url(url)

    def pesquisar_google(self, busca):
        from urllib.parse import quote_plus
        self._J._abrir_url("https://www.google.com/search?q=" + quote_plus(str(busca)))

    def procurar_youtube(self, busca):
        from urllib.parse import quote_plus
        self._J._abrir_url("https://www.youtube.com/results?search_query=" + quote_plus(str(busca)))

    def ajustar_volume(self, como, passos=5):
        como = _norm(como)
        if como.startswith("mud"):
            self._J._tecla(0xAD)
        elif como.startswith("dimin") or como.startswith("desc") or como.startswith("baix"):
            self._J._tecla(0xAE, max(1, int(passos)))
        else:
            self._J._tecla(0xAF, max(1, int(passos)))

    def _win_combo(self, *teclas):
        u = ctypes.windll.user32
        for vk in teclas:
            u.keybd_event(vk, 0, 0, 0)
        for vk in reversed(teclas):
            u.keybd_event(vk, 0, 2, 0)

    def minimizar_tudo(self):
        self._win_combo(0x5B, 0x44)            # Win + D

    def restaurar_janelas(self):
        self._win_combo(0x5B, 0x10, 0x4D)      # Win + Shift + M
