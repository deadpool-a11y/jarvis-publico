"""
compromissos.py - cancelar e adiar compromissos no Jarvis.

Os compromissos ficam em compromissos.json (mesma pasta deste arquivo):
[
  {"id": 1, "titulo": "Dentista", "quando": "2026-10-05 15:00"}
]

Uso rápido no jarvis_acoes.py:
    from compromissos import tratar_comando_compromisso
    resposta = tratar_comando_compromisso(texto_do_usuario)
    if resposta is not None:
        falar(resposta)   # a sua função de voz/HUD
        return
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compromissos.json")
FORMATO = "%Y-%m-%d %H:%M"

PALAVRAS_IGNORADAS = {
    "meu", "minha", "o", "a", "os", "as", "de", "do", "da", "das", "dos", "com",
    "compromisso", "compromissos", "para", "pra", "pras", "que", "tenho", "hoje",
    "amanha", "cancela", "cancelar", "cancele", "adia", "adiar", "adie", "remarca",
    "remarcar", "desmarca", "desmarcar", "jarvis", "por", "favor", "daqui", "em",
    "um", "uma", "no", "na",
}


# ---------- utilidades ----------
def _norm(texto):
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def carregar():
    if not os.path.exists(ARQUIVO):
        return []
    try:
        with open(ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def salvar(lista):
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def _quando(c):
    return datetime.strptime(c["quando"], FORMATO)


def _descricao(c):
    dt = _quando(c)
    return f"{c['titulo']} às {dt:%H:%M} do dia {dt:%d/%m}"


def adicionar_compromisso(titulo, quando):
    """quando: objeto datetime."""
    lista = carregar()
    novo_id = max([c.get("id", 0) for c in lista], default=0) + 1
    lista.append({"id": novo_id, "titulo": titulo, "quando": quando.strftime(FORMATO)})
    salvar(lista)
    return novo_id


# ---------- interpretação do texto ----------
def _achar_hora(texto):
    """Acha '15h', '15:30' ou '15h30'. Retorna (hora, minuto|None) ou None."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2})|h(\d{2})?(?![a-z]))", texto)
    if not m:
        return None
    hora = int(m.group(1))
    minuto = m.group(2) or m.group(3)
    return hora, (int(minuto) if minuto else None)


def extrair_adiamento(texto):
    """Lê frases como 'para as 16h', '30 minutos', '2 horas', 'amanhã', 'meia hora'."""
    t = _norm(texto)
    r = {"dias": 0, "horas": 0, "minutos": 0, "nova_hora": None}

    m = re.search(r"\b(?:para|pra|pras)\s+(?:as\s+)?(\d{1,2})(?::(\d{2})|h(\d{2})?(?![a-z]))", t)
    if m:
        minuto = m.group(2) or m.group(3) or 0
        r["nova_hora"] = (int(m.group(1)), int(minuto))

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


def encontrar(texto_busca):
    """Retorna os compromissos futuros que combinam com o texto (hora ou palavras do título)."""
    t = _norm(texto_busca)
    agora = datetime.now()
    futuros = [c for c in carregar() if _quando(c).date() >= agora.date()]
    futuros.sort(key=_quando)

    hora = _achar_hora(t)
    palavras = [p for p in re.findall(r"[a-z0-9]+", t)
                if len(p) > 2 and p not in PALAVRAS_IGNORADAS and not p.isdigit()]

    achados = futuros
    if hora:
        h, mi = hora
        achados = [c for c in achados
                   if _quando(c).hour == h and (mi is None or _quando(c).minute == mi)]
    if palavras:
        achados = [c for c in achados if any(p in _norm(c["titulo"]) for p in palavras)]
    return achados


# ---------- ações ----------
def _escolher_um(achados):
    """Retorna (compromisso, mensagem_de_erro)."""
    if not achados:
        return None, "Não encontrei esse compromisso na sua agenda."
    if len(achados) > 1:
        opcoes = "; ".join(_descricao(c) for c in achados[:5])
        return None, f"Encontrei {len(achados)} compromissos: {opcoes}. Qual deles?"
    return achados[0], None


def cancelar_compromisso(texto_busca):
    c, erro = _escolher_um(encontrar(texto_busca))
    if erro:
        return erro
    lista = [x for x in carregar() if x.get("id") != c.get("id")]
    salvar(lista)
    return f"Pronto, cancelei: {_descricao(c)}."


def cancelar_todos():
    """Cancela todos os compromissos de hoje em diante e avisa quais foram."""
    agora = datetime.now()
    lista = carregar()
    cancelados = sorted((c for c in lista if _quando(c).date() >= agora.date()), key=_quando)
    if not cancelados:
        return "Você não tem nenhum compromisso para cancelar."

    ids = {c.get("id") for c in cancelados}
    salvar([c for c in lista if c.get("id") not in ids])

    detalhes = "; ".join(_descricao(c) for c in cancelados)
    n = len(cancelados)
    plural = "compromisso" if n == 1 else "compromissos"
    return f"Cancelei {n} {plural}: {detalhes}."


def adiar_compromisso(texto_busca, dias=0, horas=0, minutos=0, nova_hora=None):
    c, erro = _escolher_um(encontrar(texto_busca))
    if erro:
        return erro
    antigo = _quando(c)
    novo = antigo
    if nova_hora:
        novo = novo.replace(hour=nova_hora[0], minute=nova_hora[1])
    novo += timedelta(days=dias, hours=horas, minutes=minutos)
    if novo == antigo:
        return "Para quando você quer adiar? Diga um horário ou um tempo, como 30 minutos."

    lista = carregar()
    for x in lista:
        if x.get("id") == c.get("id"):
            x["quando"] = novo.strftime(FORMATO)
    salvar(lista)
    return f"Adiei {c['titulo']} de {antigo:%H:%M do dia %d/%m} para {novo:%H:%M do dia %d/%m}."


# ---------- ponto de entrada para o Jarvis ----------
def tratar_comando_compromisso(texto):
    """Se o texto for cancelar/adiar compromisso, executa e devolve a resposta.
    Se não for, devolve None (aí o Jarvis segue o fluxo normal)."""
    t = _norm(texto)

    if re.search(r"\b(cancel\w*|desmarc\w*|apag\w*|delet\w*|exclu\w*)\b", t):
        if re.search(r"\b(tudo|todos|todas|geral)\b", t):
            return cancelar_todos()
        return cancelar_compromisso(t)

    if re.search(r"\b(adi[ae]\w*|remarc\w*|atras\w*|empurr\w*)\b", t):
        busca = re.sub(r"\b(para|pra|pras|daqui a|em)\b.*", "", t)  # tira a parte do "novo horário"
        info = extrair_adiamento(t)
        return adiar_compromisso(busca, **info)

    return None


if __name__ == "__main__":
    # Teste rápido
    adicionar_compromisso("Dentista", datetime.now().replace(hour=15, minute=0) + timedelta(days=1))
    adicionar_compromisso("Reunião com o João", datetime.now().replace(hour=10, minute=30) + timedelta(days=1))
    print(tratar_comando_compromisso("adia o dentista para as 16h"))
    print(tratar_comando_compromisso("adia a reunião 30 minutos"))
    print(tratar_comando_compromisso("cancela o dentista"))