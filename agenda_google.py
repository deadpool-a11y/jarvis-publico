"""
agenda_google.py - cancelar e adiar compromissos no GOOGLE AGENDA (Jarvis).

Instalação (uma vez):
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

Primeira vez: coloque o credentials.json na mesma pasta e rode
    python agenda_google.py
Vai abrir o navegador para você autorizar. Depois disso ele cria o token.json
e não pede mais (veja as instruções de configuração na conversa).

Uso no jarvis_acoes.py (ANTES de mandar o texto para o Gemini):
    from agenda_google import tratar_comando_compromisso
    resposta = tratar_comando_compromisso(texto_do_usuario)
    if resposta is not None:
        falar(resposta)   # a sua função de voz/HUD
        return
"""
import os
import re
import unicodedata
from datetime import datetime, timedelta

SCOPES = ["https://www.googleapis.com/auth/calendar"]
PASTA = os.path.dirname(os.path.abspath(__file__))
CREDENCIAIS = os.path.join(PASTA, "credentials.json")
TOKEN = os.path.join(PASTA, "token.json")
CALENDARIO = "primary"        # sua agenda principal
DIAS_PARA_FRENTE = 30         # quantos dias à frente o Jarvis enxerga

PALAVRAS_IGNORADAS = {
    "meu", "minha", "meus", "minhas", "o", "a", "os", "as", "de", "do", "da", "das",
    "dos", "com", "compromisso", "compromissos", "para", "pra", "pras", "que", "tenho",
    "hoje", "amanha", "cancela", "cancelar", "cancele", "adia", "adiar", "adie",
    "remarca", "remarcar", "desmarca", "desmarcar", "jarvis", "por", "favor", "daqui",
    "em", "um", "uma", "no", "na", "agenda", "evento", "eventos",
}

# Se o comando falar disso, NÃO é sobre a agenda (deixa o resto do Jarvis tratar)
OUTROS_ASSUNTOS = r"\b(alarme|temporizador|timer|musica|video|download|pesquisa|noticia\w*)\b"

_cache = {}


# ---------- conexão com o Google ----------
def _servico():
    if "servico" in _cache:
        return _cache["servico"]

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENCIAIS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    _cache["servico"] = build("calendar", "v3", credentials=creds)
    return _cache["servico"]


# ---------- utilidades ----------
def _norm(texto):
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _parse(info):
    """start/end do Google -> datetime local (None se for compromisso de dia inteiro)."""
    if "dateTime" not in info:
        return None
    return datetime.fromisoformat(info["dateTime"].replace("Z", "+00:00")).astimezone()


def _data(ev):
    if ev["ini"]:
        return ev["ini"].date()
    return datetime.strptime(ev["dia"], "%Y-%m-%d").date()


def _descricao(ev):
    if ev["dia_inteiro"]:
        return f"{ev['titulo']} (dia inteiro, {_data(ev):%d/%m})"
    return f"{ev['titulo']} às {ev['ini']:%H:%M} do dia {ev['ini']:%d/%m}"


def listar_eventos(dias=DIAS_PARA_FRENTE):
    """Compromissos de agora até daqui a 'dias' dias, em ordem."""
    agora = datetime.now().astimezone()
    resp = _servico().events().list(
        calendarId=CALENDARIO,
        timeMin=agora.isoformat(),
        timeMax=(agora + timedelta(days=dias)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()

    eventos = []
    for e in resp.get("items", []):
        if e.get("status") == "cancelled":
            continue
        ini = _parse(e["start"])
        eventos.append({
            "id": e["id"],
            "titulo": e.get("summary", "(sem título)"),
            "ini": ini,
            "fim": _parse(e["end"]),
            "dia_inteiro": ini is None,
            "dia": e["start"].get("date"),
        })
    return eventos


# ---------- interpretação do texto ----------
def _achar_hora(texto):
    """Acha '15h', '15:30' ou '15h30'. Retorna (hora, minuto|None) ou None."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2})|h(\d{2})?(?![a-z]))", texto)
    if not m:
        return None
    minuto = m.group(2) or m.group(3)
    return int(m.group(1)), (int(minuto) if minuto else None)


def extrair_adiamento(texto):
    """Lê 'para as 16h', '30 minutos', '2 horas', 'amanhã', 'meia hora'..."""
    t = _norm(texto)
    r = {"dias": 0, "horas": 0, "minutos": 0, "nova_hora": None}

    m = re.search(r"\b(?:para|pra|pras)\s+(?:as\s+)?(\d{1,2})(?::(\d{2})|h(\d{2})?(?![a-z]))", t)
    if m:
        r["nova_hora"] = (int(m.group(1)), int(m.group(2) or m.group(3) or 0))

    if "meia hora" in t:
        r["minutos"] += 30
    m = re.search(r"(\d+)\s*(?:minutos?|min)\b", t)
    if m:
        r["minutos"] += int(m.group(1))
    m = re.search(r"(\d+)\s*horas?\b", t)
    if m and not r["nova_hora"]:
        r["horas"] += int(m.group(1))
    m = re.search(r"(\d+)\s*dias?\b", t)
    if m:
        r["dias"] += int(m.group(1))

    if "depois de amanha" in t:
        r["dias"] += 2
    elif "amanha" in t:
        r["dias"] += 1
    if "semana que vem" in t or "proxima semana" in t:
        r["dias"] += 7
    return r


def encontrar(texto_busca, usar_data=False):
    """Compromissos que combinam com o texto (dia, hora ou palavras do título)."""
    t = _norm(texto_busca)
    eventos = listar_eventos()

    if usar_data:
        hoje = datetime.now().astimezone().date()
        alvo = None
        if "depois de amanha" in t:
            alvo = hoje + timedelta(days=2)
        elif "amanha" in t:
            alvo = hoje + timedelta(days=1)
        elif re.search(r"\bhoje\b", t):
            alvo = hoje
        if alvo:
            eventos = [e for e in eventos if _data(e) == alvo]

    hora = _achar_hora(t)
    if hora:
        h, mi = hora
        eventos = [e for e in eventos
                   if e["ini"] and e["ini"].hour == h and (mi is None or e["ini"].minute == mi)]

    palavras = [p for p in re.findall(r"[a-z0-9]+", t)
                if len(p) > 2 and p not in PALAVRAS_IGNORADAS and not p.isdigit()]
    if palavras:
        eventos = [e for e in eventos if any(p in _norm(e["titulo"]) for p in palavras)]
    return eventos


# ---------- ações ----------
def _escolher_um(achados):
    if not achados:
        return None, "Não encontrei esse compromisso na sua agenda."
    if len(achados) > 1:
        opcoes = "; ".join(_descricao(e) for e in achados[:5])
        return None, f"Encontrei {len(achados)} compromissos: {opcoes}. Qual deles?"
    return achados[0], None


def cancelar_compromisso(texto_busca):
    ev, erro = _escolher_um(encontrar(texto_busca, usar_data=True))
    if erro:
        return erro
    _servico().events().delete(calendarId=CALENDARIO, eventId=ev["id"]).execute()
    return f"Pronto, cancelei: {_descricao(ev)}."


def cancelar_todos():
    """Cancela todos os compromissos dos próximos dias e avisa quais foram."""
    eventos = listar_eventos()
    if not eventos:
        return "Você não tem nenhum compromisso para cancelar."

    servico = _servico()
    cancelados, falhas = [], []
    for ev in eventos:
        try:
            servico.events().delete(calendarId=CALENDARIO, eventId=ev["id"]).execute()
            cancelados.append(ev)
        except Exception:
            falhas.append(ev)

    msg = ""
    if cancelados:
        n = len(cancelados)
        plural = "compromisso" if n == 1 else "compromissos"
        msg = f"Cancelei {n} {plural}: " + "; ".join(_descricao(e) for e in cancelados) + "."
    if falhas:
        msg += " Não consegui cancelar: " + "; ".join(_descricao(e) for e in falhas) + "."
    return msg.strip()


def adiar_compromisso(texto_busca, dias=0, horas=0, minutos=0, nova_hora=None):
    ev, erro = _escolher_um(encontrar(texto_busca))
    if erro:
        return erro
    if ev["dia_inteiro"]:
        return "Esse compromisso é de dia inteiro; só consigo adiar os que têm horário."

    novo_ini = ev["ini"]
    if nova_hora:
        novo_ini = novo_ini.replace(hour=nova_hora[0], minute=nova_hora[1])
    novo_ini += timedelta(days=dias, hours=horas, minutes=minutos)
    if novo_ini == ev["ini"]:
        return "Para quando você quer adiar? Diga um horário ou um tempo, como 30 minutos."

    novo_fim = ev["fim"] + (novo_ini - ev["ini"])   # mantém a duração
    _servico().events().patch(
        calendarId=CALENDARIO,
        eventId=ev["id"],
        body={"start": {"dateTime": novo_ini.isoformat()},
              "end": {"dateTime": novo_fim.isoformat()}},
    ).execute()
    return (f"Adiei {ev['titulo']} de {ev['ini']:%H:%M do dia %d/%m} "
            f"para {novo_ini:%H:%M do dia %d/%m}.")


# ---------- ponto de entrada para o Jarvis ----------
def tratar_comando_compromisso(texto):
    """Cancela/adia no Google Agenda se o texto for desse tipo de pedido.
    Se não for, devolve None (o Jarvis segue o fluxo normal)."""
    t = _norm(texto)
    if re.search(OUTROS_ASSUNTOS, t):
        return None

    try:
        if re.search(r"\b(cancel\w*|desmarc\w*|apag\w*|delet\w*|exclu\w*)\b", t):
            if re.search(r"\b(tudo|todos|todas|geral)\b", t):
                return cancelar_todos()
            return cancelar_compromisso(t)

        if re.search(r"\b(adi[ae]\w*|remarc\w*|atras\w*|empurr\w*)\b", t):
            busca = re.sub(r"\b(para|pra|pras|daqui a|em)\b.*", "", t)  # tira o "novo horário"
            return adiar_compromisso(busca, **extrair_adiamento(t))
    except FileNotFoundError:
        return "Não achei o arquivo credentials.json do Google Agenda na pasta do Jarvis."
    except Exception as erro:
        return f"Não consegui acessar o Google Agenda: {erro}"

    return None


if __name__ == "__main__":
    # Rode uma vez para autorizar e testar a conexão.
    print("Conectando ao Google Agenda...")
    proximos = listar_eventos()
    print(f"Conectou! Você tem {len(proximos)} compromisso(s) nos próximos {DIAS_PARA_FRENTE} dias:")
    for ev in proximos:
        print(" -", _descricao(ev))