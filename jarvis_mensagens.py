"""
Jarvis - mensagens e chamadas: WhatsApp Web, Gmail, Discord e Instagram.

Coloque este arquivo na MESMA pasta do jarvis_acoes.py e do jarvis_hud.py.

Instalar (uma vez):
    pip install playwright
    (usa o Microsoft Edge que já vem no Windows. Se não funcionar:
     python -m playwright install chromium)

PRIMEIRO USO (uma vez só):
    1. Feche o Jarvis por completo.
    2. python jarvis_mensagens.py login
    3. Na janela que abrir, entre no WhatsApp (QR code), Instagram e Discord.
       Volte ao terminal e aperte Enter.
    4. Abra o Jarvis normalmente. O navegador roda ESCONDIDO (sem janela) e o seu Opera nunca é tocado.

O QUE ELE FAZ
    "Jarvis, tenho mensagens?"            -> diz quantas pessoas mandaram e em quais apps,
                                             pergunta qual app você quer ouvir e lê uma por uma
    "Jarvis, mensagens do Instagram"      -> já lê direto as do Instagram
    "Jarvis, responde minhas mensagens"   -> lê cada conversa (WhatsApp, Instagram, Discord), pergunta o que
                                             você quer mandar, repete e pede confirmação, e envia.
                                             Diga "pula" para pular uma, "para" para encerrar.
    "Jarvis, liga para a Maria"           -> abre a conversa no WhatsApp Web e liga (pede confirmação)
    (chamada chegando)                    -> ele avisa em voz alta quem está ligando
    "Jarvis, atende" / "Jarvis, recusa" / "Jarvis, encerra a chamada"

DE ONDE VEM CADA MENSAGEM
    WhatsApp   -> WhatsApp Web no navegador escondido do Jarvis
    Gmail      -> IMAP com senha de app (variáveis JARVIS_GMAIL_EMAIL e JARVIS_GMAIL_SENHA)
    Discord    -> notificações do Windows (leitura) + aba web escondida (resposta)
    Instagram  -> caixa de entrada no navegador escondido (reserva: notificações do Windows)

Testar sem o Jarvis:
    python jarvis_mensagens.py login              (abre o navegador visível para você entrar nas contas)
    python jarvis_mensagens.py mensagens          (coleta e mostra tudo)
    python jarvis_mensagens.py zap                (só o WhatsApp Web)
    python jarvis_mensagens.py instagram          (só o Instagram)
    python jarvis_mensagens.py instagram_debug    (mostra o que a página do Instagram devolveu, cru)
    python jarvis_mensagens.py dom discord.com    (diagnóstico: estrutura da aba de um site)
    python jarvis_mensagens.py notificacoes       (notificações cruas do Windows)
    python jarvis_mensagens.py responder          (fluxo de responder, digitando no lugar da voz)
    python jarvis_mensagens.py gmail              (só o Gmail)
    (porta e alvos só servem para ZAP_MODO = "meu_navegador")
"""

import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# ---------------- Configurações (pode ajustar) ----------------
ZAP_ATIVO = True               # False = não usa o WhatsApp Web (WhatsApp passa a vir só das notificações)
ZAP_ABRIR_AO_INICIAR = True    # abre o WhatsApp Web quando o Jarvis liga (necessário para avisar chamadas)
CONFIRMAR_LIGACAO = True       # pergunta "Ligo para X?" antes de ligar (evita ligar para a pessoa errada)
MAX_MENSAGENS_POR_APP = 10     # quantas mensagens ler por app de cada vez
PAUSA_ENTRE = 1.5              # segundos de escuta entre uma mensagem e outra, para você dizer "para" (0 = desliga)
NOTIF_HORAS = 12               # só considera notificações das últimas X horas
GMAIL_MAX = 8                  # quantos e-mails não lidos da caixa principal
AVISOS_DE_CHAMADA = 3          # quantas vezes avisa em voz alta que alguém está ligando
ZAP_URL = "https://web.whatsapp.com/"
ZAP_NAVEGADOR = "edge"         # "edge", "chrome", "opera" ou o caminho completo de um .exe
ZAP_MODO = "oculto"            # "oculto"         = navegador INVISÍVEL só do Jarvis, perfil próprio (recomendado;
                               #                    não toca no seu Opera; faça o login uma vez com o modo "login")
                               # "janela_propria" = igual, mas com uma janela minimizada
                               # "meu_navegador"  = conecta no seu Opera aberto (faz o Opera piscar branco)
ZAP_PORTA = 9222               # só para o modo "meu_navegador"
INSTAGRAM_ATIVO = True         # True = lê o Instagram pela caixa de entrada no navegador (precisa estar logado)
INSTAGRAM_URL = "https://www.instagram.com/direct/inbox/"
DISCORD_URL = "https://discord.com/channels/@me"
CONFIRMAR_RESPOSTA = True      # repete o que você falou e pergunta "Envio?" antes de mandar (recomendado: a voz erra)
DISCORD_RESPONDER = True       # responder DMs do Discord pela aba web do Discord
# --------------------------------------------------------------

NOMES = {"whatsapp": "WhatsApp", "discord": "Discord", "instagram": "Instagram", "gmail": "Gmail"}
ORDEM = ["whatsapp", "discord", "instagram", "gmail"]
PALAVRAS_APP = {
    "whatsapp": ("whatsapp", "whats", "zap", "zapzap"),
    "gmail": ("gmail", "email", "emails", "e-mail", "e-mails", "correio"),
    "discord": ("discord", "discorde"),
    "instagram": ("instagram", "insta"),
}
PASTA_DADOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_dados")

RE_ATENDER = re.compile(r"^\s*(atender|accept|answer)", re.I)
RE_RECUSAR = re.compile(r"^\s*(recusar|rejeitar|decline|reject)", re.I)
RE_ENCERRAR = re.compile(r"(encerrar|finalizar|desligar|end call|leave|hang ?up)", re.I)
RE_VIDEO = re.compile(r"v[ií]deo", re.I)
RE_CHAMADA = re.compile(r"chamar|chamada|ligar|call|voice|voz|[aá]udio|v[ií]deo", re.I)
RE_NAO_NOME = re.compile(r"atender|recusar|accept|decline|reject|chamada|call|ligando|calling|voz|v[ií]deo", re.I)


class ErroMsg(Exception):
    """Erro com mensagem amigável para o Jarvis falar."""


def _norm(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


# ======================= LIGAÇÃO COM O jarvis_acoes =======================

class _Stub:
    """Usado só quando você roda este arquivo sozinho para testar."""
    ESTADO = {"limiar": 350.0}
    ESPERA_COMANDO = 12

    @staticmethod
    def falar(texto):
        print("Jarvis:", texto)

    @staticmethod
    def escutar(limiar, espera_max=0):
        return input("(você) > ")

    @staticmethod
    def perguntar_sim_nao(pergunta):
        return input(f"Jarvis: {pergunta} (s/n) > ").strip().lower().startswith("s")

    @staticmethod
    def _bipar(vezes=3):
        pass

    @staticmethod
    def _variavel(nome):
        """Lê a variável do ambiente e, se não achar, direto do registro do Windows (onde o setx guarda)."""
        valor = os.environ.get(nome, "").strip()
        if not valor:
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                    valor = str(winreg.QueryValueEx(k, nome)[0]).strip()
            except Exception:
                valor = ""
        return valor


def _J():
    """O módulo jarvis_acoes (o HUD troca falar/escutar lá dentro, então sempre chamamos pelo nome)."""
    for nome in ("jarvis_acoes", "__main__"):
        m = sys.modules.get(nome)
        if m is not None and hasattr(m, "falar") and hasattr(m, "ACOES_NOVAS"):
            return m
    return _Stub


def _pasta() -> str:
    os.makedirs(PASTA_DADOS, exist_ok=True)
    return PASTA_DADOS


def _mesmo_nome(pedido: str, achado: str) -> bool:
    a, b = _norm(pedido), _norm(achado)
    return bool(a and b and (a == b or a in b or b in a))


def _juntar(itens) -> str:
    itens = [str(i) for i in itens]
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


# ======================= MENSAGENS JÁ LIDAS =======================

def _chave(m: dict) -> str:
    base = f"{m['app']}|{_norm(m['nome'])}|{_norm(m['texto'])}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:16]


def _carregar_lidas() -> list:
    try:
        with open(os.path.join(_pasta(), "msgs_lidas.json"), encoding="utf-8") as f:
            dados = json.load(f)
        return [str(c) for c in dados] if isinstance(dados, list) else []
    except Exception:
        return []


def _marcar_lidas(msgs: list):
    chaves = _carregar_lidas() + [_chave(m) for m in msgs]
    try:
        with open(os.path.join(_pasta(), "msgs_lidas.json"), "w", encoding="utf-8") as f:
            json.dump(chaves[-600:], f)
    except Exception as erro:
        print(f"(Não consegui salvar as mensagens lidas: {erro})")


# ======================= GMAIL (IMAP) =======================

def _gmail():
    """E-mails não lidos da caixa principal. None = Gmail não configurado."""
    J = _J()
    usuario = J._variavel("JARVIS_GMAIL_EMAIL").strip().strip("\"'").strip()
    senha = J._variavel("JARVIS_GMAIL_SENHA").strip().strip("\"'").replace(" ", "")
    if not usuario or not senha:
        return None
    import email as _email
    import imaplib
    from email.header import decode_header, make_header
    from email.utils import parseaddr

    def limpo(valor):
        try:
            return str(make_header(decode_header(valor or ""))).strip()
        except Exception:
            return (valor or "").strip()

    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
    except Exception as erro:
        print(f"[erro] gmail conexão: {erro}")
        raise ErroMsg("Não consegui conectar no Gmail, senhor. Verifique a internet.")
    try:
        M.login(usuario, senha)
    except imaplib.IMAP4.error as erro:
        detalhe = str(erro)
        print(f"[erro] gmail login: {detalhe}")
        baixo = detalhe.lower()
        if "application-specific" in baixo:
            raise ErroMsg("O Gmail exige uma senha de app, senhor. A senha normal não funciona.")
        if "web login" in baixo or "weblogin" in baixo or "web browser" in baixo:
            raise ErroMsg("O Google pediu confirmação no navegador, senhor. Entre no Gmail pelo navegador e tente de novo.")
        if "imap" in baixo and ("disabled" in baixo or "not enabled" in baixo):
            raise ErroMsg("O acesso IMAP está desligado no seu Gmail, senhor. Ative nas configurações do Gmail.")
        raise ErroMsg("O Gmail recusou o e-mail ou a senha, senhor. Use uma senha de app de 16 letras "
                      "e confira se o IMAP está ativado.")
    except Exception as erro:
        print(f"[erro] gmail login: {erro}")
        raise ErroMsg("Não consegui entrar no Gmail agora, senhor.")
    msgs = []
    try:
        M.select("INBOX", readonly=True)  # readonly: não marca nada como lido
        tipo, dados = M.search(None, "X-GM-RAW", '"in:inbox is:unread category:primary"')
        if tipo != "OK":
            tipo, dados = M.search(None, "UNSEEN")
        ids = (dados[0] or b"").split()[-GMAIL_MAX:]
        for i in ids:
            tipo, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if tipo != "OK" or not d or not isinstance(d[0], tuple):
                continue
            cab = _email.message_from_bytes(d[0][1])
            nome, endereco = parseaddr(limpo(cab.get("From")))
            nome = nome or endereco.split("@")[0] or "remetente desconhecido"
            msgs.append({"app": "gmail", "nome": nome,
                         "texto": limpo(cab.get("Subject")) or "sem assunto"})
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return msgs


# ======================= NOTIFICAÇÕES DO WINDOWS (Discord, Instagram...) =======================

def _copiar_banco():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Notifications")
    origem = os.path.join(base, "wpndatabase.db")
    if not os.path.exists(origem):
        return None, None
    tmp = tempfile.mkdtemp(prefix="jarvis_wpn_")
    for sufixo in ("", "-wal", "-shm"):
        if os.path.exists(origem + sufixo):
            shutil.copy2(origem + sufixo, os.path.join(tmp, "wpndatabase.db" + sufixo))
    return tmp, os.path.join(tmp, "wpndatabase.db")


def _linhas_notificacoes(limite=150):
    tmp, banco = _copiar_banco()
    if not banco:
        return []
    try:
        con = sqlite3.connect(banco)
        try:
            return con.execute(
                "SELECT n.Id, n.Payload, n.ArrivalTime, h.PrimaryId FROM Notification n "
                "LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
                "ORDER BY n.ArrivalTime DESC LIMIT ?", (limite,)).fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _textos_toast(payload):
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "ignore")
    payload = (payload or "").replace("\x00", "").lstrip("\ufeff").strip()
    if not payload.startswith("<"):
        return []
    try:
        raiz = ET.fromstring(payload)
    except ET.ParseError:
        return []
    textos = ["".join(t.itertext()).strip() for t in raiz.iter("text")]
    return [t for t in textos if t]


def _app_da_notificacao(handler: str, textos: list):
    h = (handler or "").lower()
    for app in ("whatsapp", "discord", "instagram"):
        if app in h:
            return app
    atribuicao = textos[-1].lower() if len(textos) >= 3 else ""   # navegadores colocam o site no fim
    for app in ("whatsapp", "discord", "instagram"):
        if app in atribuicao:
            return app
    return ""


def _notificacoes():
    corte = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=NOTIF_HORAS)
    achadas = []
    for id_, payload, chegada, handler in _linhas_notificacoes():
        try:
            quando = datetime(1601, 1, 1) + timedelta(microseconds=int(chegada) / 10)
        except Exception:
            continue
        if quando < corte:
            continue
        textos = _textos_toast(payload)
        if not textos:
            continue
        app = _app_da_notificacao(handler, textos)
        if not app:
            continue
        nome = textos[0]
        corpo = textos[1] if len(textos) > 1 else ""
        if re.fullmatch(r"(whatsapp|discord|instagram)(\.com)?", nome.lower()) and corpo:
            if ":" in corpo:
                nome, corpo = [p.strip() for p in corpo.split(":", 1)]
            else:
                nome = "alguém"
        achadas.append((quando, {"app": app, "nome": nome, "texto": corpo or "enviou uma mensagem"}))
    achadas.sort(key=lambda x: x[0])
    return [m for _, m in achadas]


# ======================= WHATSAPP WEB (navegador controlado) =======================

JS_NAO_LIDAS = r"""
() => {
  const painel = document.querySelector('#pane-side');
  if (!painel) return [];
  const linhas = painel.querySelectorAll('[role="listitem"], [role="row"]');
  const saida = [];
  for (const it of linhas) {
    const selo = it.querySelector('[aria-label*="não lida" i], [aria-label*="nao lida" i], [aria-label*="unread" i]');
    if (!selo) continue;
    const t = it.querySelector('span[title]');
    const titulo = t ? (t.getAttribute('title') || t.innerText || '') : '';
    const textos = Array.from(it.querySelectorAll('span[dir]'))
      .map(s => (s.innerText || '').trim()).filter(Boolean);
    saida.push({titulo: titulo.trim(), textos: textos, selo: (selo.innerText || '').trim()});
  }
  return saida;
}
"""

RE_RUIDO = re.compile(r"^(\d{1,2}:\d{2}(\s?[ap]m)?|ontem|yesterday|hoje|today|\d{1,2}/\d{1,2}(/\d{2,4})?|"
                      r"(seg|ter|qua|qui|sex|s[aá]b|dom)\w*\.?|\d+)$", re.I)


def _preview(item: dict):
    titulo = item.get("titulo") or ""
    selo = item.get("selo") or ""
    vistos, restantes = set(), []
    for t in item.get("textos") or []:
        if t in vistos or t == titulo or t == selo or RE_RUIDO.match(t):
            continue
        vistos.add(t)
        restantes.append(t)
    return restantes[-1] if restantes else ""


# ======================= INSTAGRAM (caixa de entrada no navegador) =======================

# Lê a lista de conversas da caixa de entrada. No Instagram atual cada conversa é um <div role="button">
# (no layout antigo era um link para /direct/t/<id>). A conversa não lida tem uma linha "Unread" / "Não lida".
JS_INSTAGRAM = r"""
() => {
  const saida = [], vistos = new Set();
  const marca = /(^|\n)\s*(unread|n[ãa]o lida)\s*($|\n)/i;
  for (const el of document.querySelectorAll('a[href*="/direct/t/"], [role="button"]')) {
    const texto = el.innerText || '';
    if (!texto || texto.length > 300) continue;
    const linhas = texto.split('\n').map(s => s.trim()).filter(Boolean);
    if (linhas.length < 2 || linhas.length > 8) continue;
    const chave = linhas.join('|');
    if (vistos.has(chave)) continue;
    vistos.add(chave);
    saida.push({linhas: linhas, selo: marca.test(texto)});
  }
  return saida;
}
"""

JS_INSTAGRAM_DOM = r"""
() => {
  const q = s => document.querySelectorAll(s).length;
  const info = {url: location.href, titulo: document.title, contagens: {}, amostras: []};
  for (const s of ['a[href*="/direct/"]', 'a[href*="/direct/t/"]', '[role="listitem"]', '[role="row"]',
                   '[role="grid"]', '[role="button"]', '[role="main"]', '[role="tab"]', 'input[name="username"]']) {
    info.contagens[s] = q(s);
  }
  const cand = Array.from(document.querySelectorAll('a, [role="button"], [role="listitem"], [role="row"]'))
    .filter(e => {
      const t = (e.innerText || '');
      const l = t.split('\n').filter(x => x.trim());
      return l.length >= 2 && l.length <= 6 && t.length < 200;
    });
  for (const e of cand.slice(0, 12)) {
    info.amostras.push({tag: e.tagName, role: e.getAttribute('role'), href: e.getAttribute('href'),
      linhas: (e.innerText || '').split('\n').map(x => x.trim()).filter(Boolean)});
  }
  return info;
}
"""

JS_DOM_CONVERSA = r"""
() => {
  const main = document.querySelector('[role="main"]') || document.body;
  const r = main.getBoundingClientRect();
  const itens = [];
  const els = Array.from(main.querySelectorAll('[dir="auto"], [role="row"], [data-list-item-id]'));
  for (const e of els.slice(-40)) {
    const t = (e.innerText || '').trim();
    if (!t || t.length > 300) continue;
    const b = e.getBoundingClientRect();
    itens.push({tag: e.tagName, role: e.getAttribute('role'), id: e.getAttribute('data-list-item-id'),
      aria: e.getAttribute('aria-label'),
      x: Math.round(((b.left + b.right) / 2 - r.left) / (r.width || 1) * 100),
      texto: t.slice(0, 120)});
  }
  return {largura: Math.round(r.width), itens: itens};
}
"""

JS_IG_MARCAR = r"""
(nome) => {
  const lixo = /^(online|[·•]|n[ãa]o lidas?|unread|ativ[oa].*|active.*)$/i;
  const tempo = /^(\d+\s*(s|seg|min|m|h|d|sem|w|a)\.?|agora|now|ontem|yesterday)$/i;
  const alvo = (nome || '').trim().toLowerCase();
  for (const el of document.querySelectorAll('a[href*="/direct/t/"], [role="button"]')) {
    const texto = el.innerText || '';
    if (!texto || texto.length > 300) continue;
    const linhas = texto.split('\n').map(s => s.trim()).filter(Boolean);
    if (linhas.length < 2 || linhas.length > 8) continue;
    const resto = linhas.filter(l => !lixo.test(l) && !tempo.test(l));
    if (resto.length && resto[0].toLowerCase() === alvo) {
      el.setAttribute('data-jarvis', 'alvo');
      return true;
    }
  }
  return false;
}
"""

_TEMPO_IG = r"(\d+\s*(s|seg|min|m|h|d|sem|w|a)\.?|agora|now|ontem|yesterday)"
RE_IG_VOCE = re.compile(r"^(voc[eê]|you)\s*:", re.I)
RE_IG_TEMPO = re.compile(r"^" + _TEMPO_IG + r"$", re.I)
RE_IG_TEMPO_FIM = re.compile(r"\s*[·•]\s*" + _TEMPO_IG + r"\s*$", re.I)
RE_IG_LIXO = re.compile(r"^(online|[·•]|n[ãa]o lidas?|unread|ativ[oa]( agora| h[aá].*)?|active( now)?.*)$", re.I)
RE_IG_NOVAS = re.compile(r"^(\d+)\+?\s*(new messages?|novas? mensagens?)", re.I)


def _ig_para_msgs(conversas: list) -> list:
    """Transforma o que a página devolveu em mensagens (só as não lidas, só as recebidas)."""
    msgs = []
    for c in conversas or []:
        if not c.get("selo"):
            continue          # sem a marca "Unread" / "Não lida" não há nada novo
        linhas = [l for l in (c.get("linhas") or []) if l]
        resto = [l for l in linhas if not RE_IG_LIXO.match(l) and not RE_IG_TEMPO.match(l)]
        if not resto:
            continue
        nome = resto[0]
        previa = RE_IG_TEMPO_FIM.sub("", resto[1]).strip() if len(resto) > 1 else ""
        if RE_IG_VOCE.match(previa):
            continue          # a última mensagem é sua
        novas = RE_IG_NOVAS.match(previa)
        if novas:             # grupo com várias mensagens: o Instagram mostra só a contagem
            n = int(novas.group(1))
            mais = "+" in previa[:len(novas.group(1)) + 1]
            previa = (f"{n} ou mais mensagens novas" if mais
                      else "1 mensagem nova" if n == 1 else f"{n} mensagens novas")
        msgs.append({"app": "instagram", "nome": nome, "texto": previa or "enviou uma mensagem"})
    return msgs


def _achar_exe(preferido: str):
    """Caminho do .exe do navegador pedido em ZAP_NAVEGADOR (None = usar Chrome/Edge pelo canal)."""
    pref = (preferido or "").strip()
    if pref.lower().endswith(".exe"):
        return pref if os.path.exists(pref) else None
    if pref.lower() != "opera":
        return None
    candidatos = []
    padrao = str(getattr(_J(), "NAVEGADOR", {}).get("exe") or "")   # navegador padrão detectado pelo Jarvis
    if "opera" in padrao.lower():
        candidatos.append(padrao)
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        raiz = os.environ.get(var, "")
        if not raiz:
            continue
        for pasta in ("Opera", "Opera GX"):
            candidatos.append(os.path.join(raiz, "Programs", pasta, "opera.exe"))
            candidatos.append(os.path.join(raiz, pasta, "opera.exe"))
    for c in candidatos:
        if c and os.path.exists(c) and os.path.basename(c).lower() == "opera.exe":
            return c
    return None


class _Zap:
    def __init__(self):
        self.fila = queue.Queue()
        self.thread = None
        self.browser = None
        self.ctx = None
        self.page = None
        self.page_ig = None
        self._tema_ok = set()
        self.erro = None
        self.tocando = False
        self.avisos = 0
        self.ultimo_aviso = 0.0
        self.trava = threading.Lock()

    # ---- fora da thread do navegador ----
    def iniciar(self):
        with self.trava:
            if self.thread and self.thread.is_alive():
                return
            self.erro = None
            self.thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-zap")
            self.thread.start()

    def chamar(self, nome: str, *args, timeout: int = 90):
        """Pede uma tarefa para a thread do navegador (o Playwright só funciona na thread que o criou)."""
        if not ZAP_ATIVO:
            raise ErroMsg("O WhatsApp Web está desativado, senhor.")
        self.iniciar()
        if self.erro:
            raise ErroMsg(self.erro)
        res = queue.Queue()
        self.fila.put((nome, args, res))
        try:
            ok, valor = res.get(timeout=timeout)
        except queue.Empty:
            raise ErroMsg("O WhatsApp Web demorou demais para responder, senhor.")
        if ok:
            return valor
        raise valor

    # ---- dentro da thread do navegador ----
    def _abrir_navegador(self, p, visivel=False):
        """Abre o navegador PRÓPRIO do Jarvis (perfil separado do seu navegador).
        visivel=True mostra a janela (usado só no modo 'login')."""
        oculto = (ZAP_MODO == "oculto") and not visivel
        args = ["--use-fake-ui-for-media-stream",
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling"]
        if not oculto and not visivel:
            args.append("--start-minimized")
        opcoes = dict(user_data_dir=os.path.join(_pasta(), "whatsapp_perfil"), headless=oculto,
                      args=args, ignore_default_args=["--enable-automation"],
                      permissions=["microphone", "notifications"])
        if oculto:
            opcoes.update(viewport={"width": 1280, "height": 900},
                          user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
        else:
            opcoes.update(no_viewport=True, color_scheme="no-override")
        tentativas = []
        pref = (ZAP_NAVEGADOR or "").strip().lower()
        exe = _achar_exe(ZAP_NAVEGADOR)
        if exe:
            tentativas.append(("executable_path", exe))
        elif pref == "opera" or pref.endswith(".exe"):
            print(f"(Não achei o navegador '{ZAP_NAVEGADOR}'. Vou tentar o Edge ou o Chrome.)")
        if pref == "chrome":
            tentativas += [("channel", "chrome"), ("channel", "msedge"), (None, None)]
        else:
            tentativas += [("channel", "msedge"), ("channel", "chrome"), (None, None)]
        for chave, valor in tentativas:
            try:
                kw = dict(opcoes)
                if chave:
                    kw[chave] = valor
                ctx = p.chromium.launch_persistent_context(**kw)
                print(f"Navegador do Jarvis aberto ({'oculto' if oculto else 'visível'}): {valor or 'Chromium do Playwright'}")
                return ctx
            except Exception as erro:
                print(f"(não abriu com {valor or 'chromium'}: {str(erro)[:150]})")
        raise ErroMsg("Não consegui abrir o navegador do Jarvis, senhor. Feche outras janelas dele "
                      "ou rode python -m playwright install chromium.")

    def _achar_aba(self, dominio: str = "web.whatsapp.com"):
        """Procura uma aba já aberta cujo endereço contém o texto pedido."""
        try:
            for pg in self.ctx.pages:
                if not pg.is_closed() and dominio in pg.url:
                    return pg
        except Exception:
            pass
        return None

    def _sem_tema_forcado(self):
        """Só importa no modo 'meu_navegador': o Playwright força o tema claro nas abas que controla.
        Aqui o Jarvis devolve o tema normal (uma vez por aba)."""
        if ZAP_MODO != "meu_navegador":
            return
        try:
            paginas = list(self.ctx.pages)
        except Exception:
            return
        for pg in paginas:
            if id(pg) in self._tema_ok:
                continue
            self._tema_ok.add(id(pg))
            try:
                if pg.is_closed():
                    continue
                try:
                    pg.emulate_media(color_scheme="no-override", reduced_motion="no-override",
                                     forced_colors="no-override")
                except Exception:
                    pg.emulate_media(color_scheme="no-override")
            except Exception:
                pass

    def _conectar(self, p):
        if ZAP_MODO == "meu_navegador":
            try:
                self.browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{ZAP_PORTA}", timeout=5000)
            except Exception:
                raise ErroMsg("Não consegui falar com o seu Opera, senhor. Abra o Opera pelo atalho Opera Jarvis.")
            self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            self._tema_ok = set()
            self._sem_tema_forcado()
            self.page = self._achar_aba()
            self.page_ig = None
            print("Conectado ao seu Opera aberto.")
        else:
            self.browser = None
            self.ctx = self._abrir_navegador(p)
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            self.page_ig = None
            self._tema_ok = set()
            self.page.goto(ZAP_URL)
            if INSTAGRAM_ATIVO:
                try:
                    self.page_ig = self.ctx.new_page()
                    self.page_ig.goto(INSTAGRAM_URL)
                except Exception as erro:
                    print(f"(Instagram: não consegui abrir agora: {str(erro)[:100]})")
            if DISCORD_RESPONDER:
                try:
                    self.ctx.new_page().goto(DISCORD_URL)
                except Exception as erro:
                    print(f"(Discord: não consegui abrir agora: {str(erro)[:100]})")
            print("Navegador do Jarvis pronto. Se algo pedir login, rode: python jarvis_mensagens.py login")

    def _bombear(self, ms: int = 250):
        """Espera um pouquinho deixando o Playwright processar eventos, em vez de ficar parado na fila."""
        pg = self.page if (self.page is not None and not self.page.is_closed()) else None
        if pg is None:
            try:
                pg = next((x for x in self.ctx.pages if not x.is_closed()), None)
            except Exception:
                pg = None
        try:
            if pg is not None:
                pg.wait_for_timeout(ms)
                return
        except Exception:
            pass
        time.sleep(ms / 1000)

    def _servir(self):
        proximo = 0.0
        while True:
            if self.browser is not None and not self.browser.is_connected():
                print("(O navegador foi fechado. Vou esperar ele abrir de novo.)")
                return
            try:
                nome, args, res = self.fila.get_nowait()
            except queue.Empty:
                self._bombear()
                self._sem_tema_forcado()
                if time.time() >= proximo:      # a checagem de chamadas só a cada 2 s
                    self._vigiar()
                    proximo = time.time() + 2
                continue
            try:
                res.put((True, getattr(self, "_j_" + nome)(*args)))
            except ErroMsg as erro:
                res.put((False, erro))
            except Exception as erro:
                print(f"[erro] WhatsApp Web ({nome}): {erro}")
                res.put((False, ErroMsg("Não consegui usar o navegador agora, senhor.")))

    def _loop(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.erro = "Falta instalar o Playwright, senhor. Rode pip install playwright."
            print(f"[erro] {self.erro}")
            return
        ultimo = ""
        try:
            with sync_playwright() as p:
                while True:
                    try:
                        self._conectar(p)
                    except ErroMsg as erro:
                        self.erro = str(erro)
                        if self.erro != ultimo:
                            print(f"[WhatsApp] {self.erro}")
                            ultimo = self.erro
                        if ZAP_MODO != "meu_navegador":
                            return
                        time.sleep(10)   # fica tentando: assim que o Opera abrir, ele conecta
                        continue
                    self.erro, ultimo = None, ""
                    self._servir()
        except Exception as erro:
            self.erro = "O WhatsApp Web parou de funcionar, senhor. Reinicie o Jarvis."
            print(f"[erro] WhatsApp Web: {erro}")

    def _pagina(self):
        pg = self.page
        if pg is None or pg.is_closed() or "web.whatsapp.com" not in pg.url:
            pg = self._achar_aba()
            if pg is None and ZAP_MODO != "meu_navegador":
                pg = self.ctx.new_page()      # navegador próprio do Jarvis: ele mesmo abre a aba
                pg.goto(ZAP_URL)
            if pg is None:
                raise ErroMsg("Não achei o WhatsApp Web aberto, senhor. Abra o painel do WhatsApp "
                              "na barra lateral do Opera.")
            self.page = pg
        try:
            pg.wait_for_selector("#pane-side", timeout=20000)
        except Exception:
            raise ErroMsg("O WhatsApp Web não está conectado, senhor. Rode python jarvis_mensagens.py login "
                          "e escaneie o QR code.")
        return pg

    def _limpar_busca(self, pg):
        try:
            caixa = pg.locator("#side div[contenteditable='true']").first
            if caixa.count() and caixa.inner_text().strip():
                caixa.click()
                pg.keyboard.press("Control+A")
                pg.keyboard.press("Backspace")
                pg.keyboard.press("Escape")
        except Exception:
            pass

    # ---- tarefas ----
    def _j_nao_lidas(self):
        pg = self._pagina()
        self._limpar_busca(pg)
        msgs = []
        for item in pg.evaluate(JS_NAO_LIDAS):
            nome = item.get("titulo") or "alguém"
            msgs.append({"app": "whatsapp", "nome": nome, "texto": _preview(item) or "enviou uma mensagem"})
        return msgs

    def _pagina_instagram(self):
        """Aba do Instagram na caixa de entrada. No navegador próprio do Jarvis ele abre a aba sozinho."""
        pg = self.page_ig
        if pg is None or pg.is_closed() or "instagram.com" not in pg.url:
            pg = self._achar_aba("instagram.com/direct") or self._achar_aba("instagram.com")
            if pg is None and ZAP_MODO != "meu_navegador":
                pg = self.ctx.new_page()
                pg.goto(INSTAGRAM_URL)
            if pg is None:
                raise ErroMsg("Não achei o Instagram aberto, senhor. Abra o painel do Instagram "
                              "na barra lateral do Opera.")
            self.page_ig = pg
        try:
            if "accounts/login" in pg.url:
                raise ErroMsg("O Instagram não está conectado, senhor. Rode python jarvis_mensagens.py login.")
            if "/direct/" not in pg.url:
                pg.goto(INSTAGRAM_URL)
            pg.wait_for_selector('[role="main"] [role="button"]', timeout=15000)
            pg.wait_for_timeout(1500)
        except ErroMsg:
            raise
        except Exception as erro:
            if pg.is_closed():
                self.page_ig = None
                raise ErroMsg("A aba do Instagram foi fechada, senhor. Tente de novo.")
            print(f"(Instagram: não achei conversas na página: {str(erro)[:120]})")
            try:
                if "login" in pg.url or pg.locator("input[name='username']").count():
                    raise ErroMsg("O Instagram não está conectado, senhor. Rode python jarvis_mensagens.py login.")
            except ErroMsg:
                raise
            except Exception:
                pass
        return pg

    def _j_instagram_nao_lidas(self, debug=False):
        pg = self._pagina_instagram()
        pg.wait_for_timeout(800)
        conversas = pg.evaluate(JS_INSTAGRAM)
        if debug:
            return conversas
        return _ig_para_msgs(conversas)

    def _j_dom(self, trecho: str = "instagram.com"):
        """Diagnóstico: mostra a estrutura da aba cujo endereço contém o texto pedido."""
        from urllib.parse import urlparse
        pg = self._achar_aba(trecho)
        sites = []
        for x in self.ctx.pages:
            try:
                if not x.is_closed():
                    sites.append(urlparse(x.url).netloc or x.url[:30])
            except Exception:
                pass
        if pg is None:
            return {"abas_abertas": sites, "erro": f"nenhuma aba com '{trecho}' encontrada"}
        return {"abas_abertas": sites, "url": pg.url, "pagina": pg.evaluate(JS_INSTAGRAM_DOM),
                "conversa": pg.evaluate(JS_DOM_CONVERSA)}

    # ---- enviar mensagens ----
    def _digitar_e_enviar(self, pg, caixa, texto: str):
        caixa.click()
        pg.keyboard.insert_text(texto)
        pg.wait_for_timeout(400)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1200)

    def _j_enviar_whatsapp(self, nome: str, texto: str):
        achado = self._j_abrir_conversa(nome)
        if not _mesmo_nome(nome, achado):
            raise ErroMsg(f"Abri a conversa de {achado} em vez de {nome}, senhor. Não enviei.")
        pg = self._pagina()
        caixa = pg.locator("#main footer div[contenteditable='true']").first
        if caixa.count() == 0:
            raise ErroMsg("Não achei a caixa de mensagem do WhatsApp, senhor.")
        self._digitar_e_enviar(pg, caixa, texto)
        return True

    def _j_enviar_instagram(self, nome: str, texto: str):
        pg = self._pagina_instagram()
        if not pg.evaluate(JS_IG_MARCAR, nome):
            raise ErroMsg(f"Não achei a conversa de {nome} no Instagram, senhor.")
        try:
            pg.locator('[data-jarvis="alvo"]').first.click(timeout=5000)
        finally:
            try:
                pg.evaluate("() => document.querySelectorAll('[data-jarvis]').forEach(e => e.removeAttribute('data-jarvis'))")
            except Exception:
                pass
        caixa = pg.locator('div[role="textbox"][contenteditable="true"]').first
        try:
            caixa.wait_for(timeout=8000)
        except Exception:
            raise ErroMsg("Não achei a caixa de mensagem do Instagram, senhor. Não enviei.")
        self._digitar_e_enviar(pg, caixa, texto)
        try:
            pg.goto(INSTAGRAM_URL)       # volta para a lista de conversas
        except Exception:
            pass
        return True

    def _pagina_discord(self):
        pg = self._achar_aba("discord.com")
        if pg is None and ZAP_MODO != "meu_navegador":
            pg = self.ctx.new_page()
            pg.goto(DISCORD_URL)
            pg.wait_for_timeout(3000)
        if pg is None:
            raise ErroMsg("Para responder no Discord eu preciso do painel do Discord aberto na barra lateral do Opera, senhor.")
        if "/login" in pg.url:
            raise ErroMsg("O Discord não está conectado, senhor. Rode python jarvis_mensagens.py login.")
        return pg

    def _j_enviar_discord(self, nome: str, texto: str):
        if not DISCORD_RESPONDER:
            raise ErroMsg("Responder no Discord está desligado, senhor.")
        if "(" in nome:
            raise ErroMsg("Só consigo responder mensagens diretas do Discord, não as de servidores, senhor.")
        pg = self._pagina_discord()
        pg.keyboard.press("Control+K")            # busca rápida do Discord
        pg.wait_for_timeout(600)
        pg.keyboard.insert_text(nome)
        pg.wait_for_timeout(1300)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1500)
        caixa = pg.locator('div[role="textbox"][contenteditable="true"]').first
        if caixa.count() == 0:
            raise ErroMsg("Não consegui abrir a conversa no Discord, senhor. Não enviei.")
        rotulo = caixa.get_attribute("aria-label") or ""
        if _norm(nome) not in _norm(rotulo):     # só envia se der para confirmar que é a conversa certa
            try:
                pg.keyboard.press("Escape")
            except Exception:
                pass
            raise ErroMsg(f"Não consegui confirmar que abri a conversa com {nome} no Discord, senhor. Não enviei.")
        self._digitar_e_enviar(pg, caixa, texto)
        return True

    def _j_abrir_conversa(self, nome: str):
        pg = self._pagina()
        if ZAP_MODO == "meu_navegador":
            try:
                pg.bring_to_front()
            except Exception:
                pass
        caixa = pg.locator("#side div[contenteditable='true']").first
        caixa.click()
        pg.keyboard.press("Control+A")
        pg.keyboard.press("Backspace")
        pg.keyboard.type(nome, delay=40)
        pg.wait_for_timeout(1500)
        resultados = pg.locator("#pane-side [role='listitem'], #pane-side [role='row']")
        if resultados.count() == 0:
            self._limpar_busca(pg)
            raise ErroMsg(f"Não encontrei {nome} no WhatsApp, senhor.")
        resultados.first.click()
        pg.wait_for_selector("#main header", timeout=8000)
        titulo = pg.locator("#main header span[title]").first
        achado = (titulo.get_attribute("title") or titulo.inner_text() or nome).strip()
        self._limpar_busca(pg)
        return achado

    def _j_ligar(self, video: bool):
        pg = self._pagina()
        if ZAP_MODO == "meu_navegador":
            try:
                pg.bring_to_front()
            except Exception:
                pass
        cab = pg.locator("#main header")
        if cab.count() == 0:
            raise ErroMsg("Não há conversa aberta no WhatsApp, senhor.")
        botoes = cab.get_by_role("button")
        candidatos = []
        for i in range(botoes.count()):
            b = botoes.nth(i)
            nome = (b.get_attribute("aria-label") or b.inner_text() or "").strip()
            if nome and RE_CHAMADA.search(nome):
                candidatos.append((nome, b))
        if video:
            alvo = [b for n, b in candidatos if RE_VIDEO.search(n)]
        else:
            alvo = [b for n, b in candidatos if not RE_VIDEO.search(n)]
        if not alvo:
            raise ErroMsg("Não achei o botão de chamada, senhor. Talvez o WhatsApp Web ainda não tenha "
                          "liberado chamadas na sua conta.")
        alvo[0].click(timeout=5000)
        pg.wait_for_timeout(700)
        # se abriu um menu "Voz / Vídeo", escolhe a opção certa
        padrao = RE_VIDEO if video else re.compile(r"voz|voice|[aá]udio", re.I)
        opcao = pg.get_by_role("menuitem", name=padrao)
        if opcao.count():
            opcao.first.click(timeout=3000)
        return True

    def _clicar_chamada(self, padrao, texto_erro: str):
        pg = self._pagina()
        b = pg.get_by_role("button", name=padrao)
        if b.count() == 0:
            raise ErroMsg(texto_erro)
        b.first.click(timeout=5000)
        self.tocando = False
        return True

    def _j_atender(self):
        return self._clicar_chamada(RE_ATENDER, "Não há nenhuma chamada tocando no WhatsApp, senhor.")

    def _j_recusar(self):
        return self._clicar_chamada(RE_RECUSAR, "Não há nenhuma chamada tocando no WhatsApp, senhor.")

    def _j_encerrar(self):
        return self._clicar_chamada(RE_ENCERRAR, "Não achei nenhuma chamada em andamento, senhor.")

    # ---- chamada chegando ----
    def _nome_chamador(self, botao) -> str:
        try:
            caixa = botao.locator("xpath=ancestor::*[@role='dialog' or @role='alertdialog'][1]")
            texto = caixa.inner_text(timeout=1500) if caixa.count() else ""
            for linha in (l.strip() for l in texto.splitlines()):
                if linha and not RE_NAO_NOME.search(linha):
                    return linha
        except Exception:
            pass
        return "alguém"

    def _vigiar(self):
        self._sem_tema_forcado()
        pg = self._achar_aba()
        if pg is None:
            return
        self.page = pg
        try:
            botao = pg.get_by_role("button", name=RE_ATENDER)
            if botao.count() == 0:
                self.tocando, self.avisos = False, 0
                return
            if self.avisos >= AVISOS_DE_CHAMADA or time.time() - self.ultimo_aviso < 12:
                return
            nome = self._nome_chamador(botao.first)
            self.tocando = True
            self.avisos += 1
            self.ultimo_aviso = time.time()
            threading.Thread(target=_avisar_chamada, args=(nome,), daemon=True).start()
        except Exception:
            pass


zap = _Zap()


def _avisar_chamada(nome: str):
    J = _J()
    J._bipar(2)
    J.falar(f"Senhor, {nome} está ligando no WhatsApp. Diga Jarvis, atende, ou Jarvis, recusa.")


# ======================= COLETAR TUDO =======================

def coletar(repetir: bool = False):
    """Devolve (mensagens, problemas). Cada mensagem: {"app", "nome", "texto"}."""
    msgs, problemas = [], []
    usar_notif_zap = not ZAP_ATIVO
    usar_notif_ig = True      # só deixa de usar as notificações se a caixa de entrada trouxer mensagens

    if ZAP_ATIVO:
        try:
            msgs += zap.chamar("nao_lidas")
        except ErroMsg as erro:
            problemas.append(str(erro))
            usar_notif_zap = True
        except Exception as erro:
            print(f"[erro] whatsapp: {erro}")
            usar_notif_zap = True

    if ZAP_ATIVO and INSTAGRAM_ATIVO:
        try:
            ig = zap.chamar("instagram_nao_lidas")
            msgs += ig
            usar_notif_ig = not ig
        except ErroMsg as erro:
            if str(erro) not in problemas:
                problemas.append(str(erro))
        except Exception as erro:
            print(f"[erro] instagram: {erro}")

    try:
        for m in _notificacoes():
            if m["app"] == "whatsapp" and not usar_notif_zap:
                continue
            if m["app"] == "instagram" and not usar_notif_ig:
                continue
            msgs.append(m)
    except Exception as erro:
        print(f"[erro] notificações do Windows: {erro}")

    try:
        emails = _gmail()
        if emails is None:
            print("(Gmail não configurado: defina JARVIS_GMAIL_EMAIL e JARVIS_GMAIL_SENHA.)")
        else:
            msgs += emails
    except ErroMsg as erro:
        problemas.append(str(erro))
    except Exception as erro:
        print(f"[erro] gmail: {erro}")
        problemas.append("Não consegui ler o Gmail agora, senhor.")

    lidas = set() if repetir else set(_carregar_lidas())
    vistos, final = set(), []
    for m in msgs:
        c = _chave(m)
        if c in vistos or c in lidas:
            continue
        vistos.add(c)
        final.append(m)
    final.sort(key=lambda m: ORDEM.index(m["app"]) if m["app"] in ORDEM else 99)
    return final, problemas


# ======================= FALAR AS MENSAGENS =======================

def _app_do_texto(t: str) -> str:
    t = _norm(t)
    for app, palavras in PALAVRAS_APP.items():
        if any(re.search(rf"\b{re.escape(p)}\b", t) for p in palavras):
            return app
    if re.search(r"\b(tod[ao]s?|tudo|geral|qualquer)\b", t):
        return "todas"
    return ""


def _limpar_texto(texto: str, limite: int = 300) -> str:
    texto = re.sub(r"https?://\S+|www\.\S+", "link", texto or "")
    texto = re.sub(r"[\U00010000-\U0010FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u2060\u200b]", "", texto)  # emojis: a voz não lê bem
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:limite].rstrip() + ("…" if len(texto) > limite else "")


def _frase(m: dict) -> str:
    nome = _limpar_texto(m["nome"], 80) or "alguém"
    app = NOMES.get(m["app"], m["app"])
    texto = _limpar_texto(m["texto"])
    if m["app"] == "gmail":
        return f"{nome}, no {app}, assunto: {texto}."
    return f"{nome}, no {app}, disse: {texto}."


def _acao_ler_mensagens(a: dict):
    J = _J()
    escolha = _app_do_texto(str(a.get("valor") or ""))
    repetir = str(a.get("repetir")).lower() in ("true", "1", "sim")
    J.falar("Verificando suas mensagens, senhor.")
    msgs, problemas = coletar(repetir)
    for p in problemas:
        J.falar(p)
    if not msgs:
        J.falar("O senhor não tem mensagens novas.")
        return "mudo"

    por_app = {}
    for m in msgs:
        por_app.setdefault(m["app"], []).append(m)

    if not escolha:
        pessoas = {app: len({_norm(m["nome"]) for m in ms}) for app, ms in por_app.items()}
        total = len({_norm(m["nome"]) for m in msgs})
        partes = [f"{n} no {NOMES[app]}" for app, n in pessoas.items()]
        abertura = (f"Senhor, {total} pessoas te mandaram mensagem" if total > 1
                    else "Senhor, 1 pessoa te mandou mensagem")
        if len(por_app) == 1:
            unico = next(iter(por_app))
            resp = J.perguntar_sim_nao(f"{abertura}, só no {NOMES[unico]}. Quer que eu leia?")
            if resp is not True:
                J.falar("Certo, senhor.")
                return "mudo"
            escolha = unico
        else:
            J.falar(f"{abertura}: {_juntar(partes)}. Qual aplicativo o senhor quer ouvir?")
            resposta = J.escutar(J.ESTADO["limiar"], J.ESPERA_COMANDO)
            escolha = _app_do_texto(resposta)
            if not escolha:
                if re.search(r"\b(nao|nenhum\w*|deixa|depois|cancela)\b", _norm(resposta)):
                    J.falar("Certo, senhor.")
                else:
                    J.falar("Não entendi qual aplicativo, senhor. Peça de novo quando quiser.")
                return "mudo"

    if escolha != "todas" and escolha not in por_app:
        J.falar(f"Não há mensagens novas no {NOMES.get(escolha, escolha)}, senhor.")
        return "mudo"

    parar = False
    ultimo = ""
    while escolha:
        apps = list(por_app) if escolha == "todas" else [escolha]
        for app in apps:
            fila = por_app.pop(app, [])
            lidas = []
            for m in fila[:MAX_MENSAGENS_POR_APP]:
                J.falar(_frase(m))
                lidas.append(m)
                if PAUSA_ENTRE:
                    ouvido = _norm(J.escutar(J.ESTADO["limiar"], PAUSA_ENTRE) or "")
                    if re.search(r"\b(para|pare|chega|parar|cancela|silencio|basta)\b", ouvido):
                        parar = True
                        break
            _marcar_lidas(lidas)
            ultimo = app
            if parar:
                break
            if len(fila) > MAX_MENSAGENS_POR_APP:
                J.falar(f"Há mais {len(fila) - MAX_MENSAGENS_POR_APP} no {NOMES[app]}. Peça de novo para ouvir.")
        if parar or not por_app:
            break

        # terminou um app e ainda sobraram outros: pergunta qual quer ouvir agora
        restantes = list(por_app)
        if len(restantes) == 1:
            r = restantes[0]
            if J.perguntar_sim_nao(f"Terminei o {NOMES[ultimo]}, senhor. Ainda há mensagens no {NOMES[r]}. Quer ouvir?") is not True:
                J.falar("Certo, senhor.")
                return "mudo"
            escolha = r
        else:
            J.falar(f"Terminei o {NOMES[ultimo]}, senhor. Ainda há mensagens no "
                    f"{_juntar([NOMES[x] for x in restantes])}. Qual o senhor quer ouvir?")
            resposta = J.escutar(J.ESTADO["limiar"], J.ESPERA_COMANDO)
            escolha = _app_do_texto(resposta)
            if not escolha or (escolha != "todas" and escolha not in por_app):
                if re.search(r"\b(nao|nenhum\w*|deixa|depois|cancela|chega|basta|para)\b", _norm(resposta)):
                    J.falar("Certo, senhor.")
                else:
                    J.falar("Não entendi qual aplicativo, senhor. Peça de novo quando quiser.")
                return "mudo"

    J.falar("Certo, senhor." if parar else "Essas foram as mensagens, senhor.")
    return "mudo"


# ======================= RESPONDER MENSAGENS (uma por uma) =======================

RESPONDER_APPS = ("whatsapp", "instagram", "discord")
RE_PULAR = re.compile(r"(nada|nenhuma?|pula|pular|proxim[ao]|ignora|ignorar|deixa|deixa pra la|depois|sem resposta)")
RE_PARAR = re.compile(r"(para|pare|parar|chega|basta|cancela|cancelar|encerra|encerrar)")


def _enviar(app: str, nome: str, texto: str):
    if app not in RESPONDER_APPS:
        raise ErroMsg(f"Não sei responder mensagens do {NOMES.get(app, app)}, senhor.")
    zap.chamar("enviar_" + app, nome, texto, timeout=60)


def _acao_responder_mensagens(a: dict):
    J = _J()
    escolha = _app_do_texto(str(a.get("valor") or ""))
    J.falar("Verificando suas mensagens, senhor.")
    msgs, problemas = coletar(True)
    for p in problemas:
        J.falar(p)
    ultimas = {}
    for m in msgs:                      # uma resposta por conversa (vale a última mensagem dela)
        if m["app"] in RESPONDER_APPS and escolha in ("", "todas", m["app"]):
            ultimas[(m["app"], _norm(m["nome"]))] = m
    fila = list(ultimas.values())
    if not fila:
        J.falar("Não há mensagens para responder, senhor.")
        return "mudo"
    n = len(fila)
    J.falar(f"O senhor tem {n} conversa{'s' if n > 1 else ''} para responder.")
    enviadas = 0
    for m in fila:
        nome = _limpar_texto(m["nome"], 80) or "essa pessoa"
        J.falar(_frase(m))
        J.falar(f"O que o senhor quer mandar para {nome}?")
        resposta = (J.escutar(J.ESTADO["limiar"], J.ESPERA_COMANDO) or "").strip()
        t = _norm(resposta).strip(" .,!?")
        if not t or RE_PULAR.fullmatch(t):
            J.falar("Certo, pulei essa.")
            continue
        if RE_PARAR.fullmatch(t):
            J.falar("Certo, senhor. Parei de responder.")
            break
        if CONFIRMAR_RESPOSTA and J.perguntar_sim_nao(f"Mando para {nome}: {resposta}. Envio, senhor?") is not True:
            J.falar("Certo, não enviei.")
            continue
        try:
            _enviar(m["app"], m["nome"], resposta)
        except ErroMsg as erro:
            J.falar(str(erro))
            continue
        except Exception as erro:
            print(f"[erro] enviar {m['app']}: {erro}")
            J.falar("Não consegui enviar essa, senhor.")
            continue
        enviadas += 1
        J.falar(f"Enviado para {nome}.")
    J.falar(f"Pronto, senhor. Enviei {enviadas} resposta{'s' if enviadas != 1 else ''}.")
    return "mudo"


# ======================= LIGAR / ATENDER =======================

def _acao_ligar_whatsapp(a: dict):
    J = _J()
    nome = str(a.get("nome") or a.get("valor") or "").strip()
    video = str(a.get("video")).lower() in ("true", "1", "sim")
    if not nome:
        J.falar("Para quem devo ligar, senhor?")
        return "mudo"
    J.falar(f"Procurando {nome} no WhatsApp, senhor.")
    try:
        achado = zap.chamar("abrir_conversa", nome)
        if CONFIRMAR_LIGACAO:
            pergunta = (f"Encontrei {achado}. Faço uma chamada de vídeo para {achado}, senhor?" if video
                        else f"Encontrei {achado}. Ligo para {achado}, senhor?")
            if J.perguntar_sim_nao(pergunta) is not True:
                J.falar("Certo, não liguei.")
                return "mudo"
        zap.chamar("ligar", video)
    except ErroMsg as erro:
        J.falar(str(erro))
        return "mudo"
    J.falar(f"Ligando para {achado} no WhatsApp.")
    return "mudo"


def _acao_chamada(metodo: str, ok_fala: str):
    def acao(a: dict):
        J = _J()
        try:
            zap.chamar(metodo)
        except ErroMsg as erro:
            J.falar(str(erro))
            return "mudo"
        J.falar(ok_fala)
        return "mudo"
    return acao


ACOES = {
    "ler_mensagens": _acao_ler_mensagens,
    "responder_mensagens": _acao_responder_mensagens,
    "ligar_whatsapp": _acao_ligar_whatsapp,
    "atender_chamada": _acao_chamada("atender", "Chamada atendida, senhor."),
    "recusar_chamada": _acao_chamada("recusar", "Chamada recusada, senhor."),
    "encerrar_chamada": _acao_chamada("encerrar", "Chamada encerrada, senhor."),
}


# ======================= COMANDOS DE VOZ (sem passar pelo Ollama) =======================

def comando_local(t: str, texto: str):
    """t = frase normalizada (sem acento, sem pontuação). Devolve {"fala": "", "acoes": [...]} ou None."""
    def acao(**campos):
        return {"fala": "", "acoes": [campos]}

    # --- chamadas que estão tocando ---
    if re.search(r"\b(recusa|recusar|recuse|rejeita|rejeitar|rejeite)\b|\bnao (atende|atenda)\b", t):
        return acao(tipo="recusar_chamada")
    if re.search(r"\b(atende|atender|atenda|aceita|aceitar|aceite)\b", t) and \
            (len(t.split()) <= 4 or re.search(r"\b(chamada|ligacao|telefone|zap|whatsapp)\b", t)):
        return acao(tipo="atender_chamada")
    if re.search(r"\b(encerra|encerrar|encerre|finaliza|finalizar|desliga|desligar|desligue) (a )?(chamada|ligacao)\b", t):
        return acao(tipo="encerrar_chamada")

    # --- ligar para alguém ---
    m = (re.search(r"\b(?:liga|ligar|ligue|chama|chamar|chame|telefona|telefonar)\s+"
                   r"(?:uma?\s+)?(?:(?:chamada|ligacao)\s+)?(?:de\s+(?:video|voz)\s+)?"
                   r"(?:para|pra|pro|ao|a)\s+(?:o\s+|a\s+)?(.+)$", t)
         or re.search(r"\b(?:faz|fazer|faca|fazem)\s+uma?\s+(?:chamada|ligacao)\s+(?:de\s+(?:video|voz)\s+)?"
                      r"(?:para|pra|pro|ao|a)\s+(?:o\s+|a\s+)?(.+)$", t))
    if m and not re.search(r"\b(mensagem|mensagens|alarme|timer)\b", t):
        nome = re.sub(r"\b(no|pelo|pela|via|por)\s+(whatsapp|whats|zap)\b.*$", "", m.group(1))
        nome = re.sub(r"\b(por|de|em)\s+video\b|\bpor favor\b|\bvideo\b", "", nome).strip(" ,.")
        if nome:
            return acao(tipo="ligar_whatsapp", nome=nome, video=bool(re.search(r"\bvideo\b", t)))

    # --- responder as mensagens que chegaram (uma por uma) ---
    if re.search(r"\b(responde|responda|responder)\b", t) and \
            re.search(r"\b(mensagem|mensagens|msgs?|zap|whatsapp|insta|instagram|discord)\b", t):
        return acao(tipo="responder_mensagens", valor=_app_do_texto(t))

    # --- ler mensagens (mandar mensagem fica de fora) ---
    if re.search(r"\b(manda|mande|mandar|envia|envie|enviar|escreve|escreva|escrever|responde|responda)\b", t):
        return None
    if re.search(r"\b(mensagem|mensagens|recados?|e-?mails?|emails?|notificacoes)\b", t) and \
            (_app_do_texto(t) in NOMES or
             re.search(r"\b(le|ler|leia|mostr\w*|fala|diga|tenho|tem|chegou|chegaram|mandou|mandaram|enviaram|"
                       r"novas?|novos?|nao lidas?|quem|quais|alguma|algum)\b", t)):
        return acao(tipo="ler_mensagens", valor=_app_do_texto(t),
                    repetir=bool(re.search(r"\b(de novo|outra vez|novamente|repete|repetir)\b", t)))
    return None


# ======================= INICIAR =======================

def iniciar():
    """Chamado pelo jarvis_acoes ao ligar: abre o navegador do Jarvis para poder avisar das chamadas."""
    if ZAP_ATIVO and ZAP_ABRIR_AO_INICIAR:
        zap.iniciar()


# ======================= TESTE =======================

if __name__ == "__main__":
    modo = (sys.argv[1] if len(sys.argv) > 1 else "mensagens").lower()
    if modo == "login":
        # Abre o navegador do Jarvis VISÍVEL para você entrar nas contas (só precisa fazer uma vez).
        # O Jarvis precisa estar fechado, porque o perfil não pode ser usado por dois ao mesmo tempo.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = zap._abrir_navegador(p, visivel=True)
            pg0 = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg0.goto(ZAP_URL)
            ctx.new_page().goto(INSTAGRAM_URL)
            ctx.new_page().goto("https://discord.com/login")
            input("Entre nas 3 contas (WhatsApp por QR code). Quando terminar, volte aqui e aperte Enter... ")
            ctx.close()
        print("Pronto! Login salvo. Agora abra o Jarvis normalmente.")
    elif modo == "notificacoes":
        for id_, payload, chegada, handler in _linhas_notificacoes(30):
            print(f"--- {handler} | {_textos_toast(payload)}")
    elif modo == "porta":
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{ZAP_PORTA}/json/version", timeout=3) as r:
                info = json.loads(r.read().decode("utf-8", "ignore"))
            print(f"OK! Navegador respondendo na porta {ZAP_PORTA}: {info.get('Browser')}")
        except Exception as erro:
            print(f"Nada respondeu na porta {ZAP_PORTA} ({erro}).")
            print("(Só serve para ZAP_MODO = 'meu_navegador'.)")
    elif modo == "alvos":
        import urllib.request
        from urllib.parse import urlparse
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{ZAP_PORTA}/json/list", timeout=3) as r:
                alvos = json.loads(r.read().decode("utf-8", "ignore"))
            for a in alvos:
                site = urlparse(a.get("url", "")).netloc or a.get("url", "")[:40]
                print(f"{str(a.get('type')):14} {site:34} {str(a.get('title', ''))[:40]}")
        except Exception as erro:
            print(f"Não consegui listar ({erro}). (Só serve para ZAP_MODO = 'meu_navegador'.)")
    elif modo == "zap":
        print(json.dumps(zap.chamar("nao_lidas", timeout=180), ensure_ascii=False, indent=2))
    elif modo == "instagram":
        try:
            print(json.dumps(zap.chamar("instagram_nao_lidas", timeout=180), ensure_ascii=False, indent=2))
        except ErroMsg as erro:
            print("RESULTADO:", erro)
    elif modo == "instagram_debug":
        try:
            print(json.dumps(zap.chamar("instagram_nao_lidas", True, timeout=180), ensure_ascii=False, indent=2))
        except ErroMsg as erro:
            print("RESULTADO:", erro)
    elif modo in ("instagram_dom", "dom"):
        trecho = sys.argv[2] if (modo == "dom" and len(sys.argv) > 2) else "instagram.com"
        try:
            print(json.dumps(zap.chamar("dom", trecho, timeout=60), ensure_ascii=False, indent=2))
        except ErroMsg as erro:
            print("RESULTADO:", erro)
    elif modo == "responder":
        _acao_responder_mensagens({})
    elif modo == "gmail":
        u = _Stub._variavel("JARVIS_GMAIL_EMAIL").strip().strip("\"'")
        p = _Stub._variavel("JARVIS_GMAIL_SENHA").strip().strip("\"'").replace(" ", "")
        print(f"E-mail configurado: {u or '(VAZIO)'}")
        print(f"Senha configurada: {len(p)} caracteres (uma senha de app tem 16)" if p else "Senha configurada: (VAZIA)")
        try:
            achados = _gmail()
            if achados is None:
                print("RESULTADO: e-mail ou senha não configurados (use o setx e abra um terminal novo).")
            else:
                print(f"RESULTADO: login OK, {len(achados)} e-mail(s) não lido(s) na caixa principal.")
                print(json.dumps(achados, ensure_ascii=False, indent=2))
        except ErroMsg as erro:
            print("RESULTADO:", erro)
    else:
        achadas, avisos = coletar(repetir=True)
        for p in avisos:
            print("AVISO:", p)
        for m in achadas:
            print(_frase(m))
        print(f"\n{len(achadas)} mensagem(ns).")