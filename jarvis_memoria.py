"""
Jarvis - memória de longo prazo.

Coloque este arquivo na MESMA pasta do jarvis_hud.py. Não precisa instalar nada.
As lembranças ficam no arquivo jarvis_memoria.json (só no seu PC).

Comandos de voz:
    "Jarvis, lembra que meu aniversário é dia 10 de maio"     (guarda)
    "Jarvis, anota que a senha do wifi está no roteador"      (guarda)
    "Jarvis, o que você lembra?"                              (lê as últimas lembranças)
    "Jarvis, você lembra do meu aniversário?"                 (procura)
    "Jarvis, o que eu te falei sobre o wifi?"                 (procura)
    "Jarvis, esquece o aniversário"                           (apaga o que combina)
    "Jarvis, esquece tudo"                                    (apaga a memória toda, com confirmação)

IMPORTANTE: não guarde senhas de verdade nem números de cartão aqui. O arquivo é texto simples.
"""

import json
import os
import re
import unicodedata
from datetime import datetime

ARQUIVO = "jarvis_memoria.json"
MAXIMO = 500            # quantas lembranças no máximo
LER_ATE = 5             # quantas ele lê em voz alta de uma vez

_CAMINHO = os.path.join(os.path.dirname(os.path.abspath(__file__)), ARQUIVO)

TOLICES = {"jarvis", "o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das", "que", "meu",
           "minha", "meus", "minhas", "seu", "sua", "e", "em", "no", "na", "nos", "nas", "por", "para",
           "pra", "sobre", "isso", "esse", "essa", "me", "te", "eu", "voce", "ai", "favor", "ja", "qual",
           "quais", "foi", "era", "tem", "ter"}


# ======================= ARMAZENAMENTO =======================

def carregar() -> list:
    try:
        with open(_CAMINHO, encoding="utf-8") as f:
            dados = json.load(f)
        return [d for d in dados if isinstance(d, dict) and d.get("texto")]
    except (OSError, ValueError):
        return []


def _salvar(fatos: list):
    # grava num arquivo temporário e troca no fim: se o PC desligar no meio, a memória antiga continua inteira
    tmp = _CAMINHO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fatos[-MAXIMO:], f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _CAMINHO)


def _alinhado(texto: str) -> str:
    """Minúsculo e sem acentos, com o MESMO tamanho do original (para achar o trecho no texto bonito)."""
    saida = []
    for c in texto:
        base = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
        saida.append(base.lower() if len(base) == 1 else c.lower())
    return "".join(saida)


def _palavras(texto: str) -> list:
    return re.findall(r"[a-z0-9]+", _alinhado(texto))


def _chaves(texto: str) -> list:
    return [p for p in _palavras(texto) if p not in TOLICES and len(p) >= 3]


def _combina(fato: str, chaves: list) -> int:
    """Quantas palavras-chave aparecem no fato (aceita variações: aniversario ~ aniversarios)."""
    pal = _palavras(fato)
    n = 0
    for k in chaves:
        raiz = k[:max(4, len(k) - 2)]
        if any(p.startswith(raiz) or k.startswith(p[:max(4, len(p) - 2)]) for p in pal if len(p) >= 3):
            n += 1
    return n


def buscar(consulta: str) -> list:
    chaves = _chaves(consulta)
    if not chaves:
        return []
    achados = [(_combina(f["texto"], chaves), i, f) for i, f in enumerate(carregar())]
    achados = [a for a in achados if a[0] > 0]
    achados.sort(key=lambda a: (-a[0], -a[1]))
    return [f for _, _, f in achados]


def contexto_para_gemini(limite: int = 40) -> str:
    """Texto com as lembranças, pronto para colar no prompt do Gemini (veja o fim da conversa)."""
    fatos = carregar()[-limite:]
    if not fatos:
        return ""
    return "Coisas que o usuário pediu para você lembrar:\n" + "\n".join(f"- {f['texto']}" for f in fatos)


# ======================= COMANDOS DE VOZ =======================

_SALVAR = re.compile(
    r"\b(lembr[ae]|memoriz[ae]|anot[ae]|guard[ae]|regist[re]+)\b(?:\s+(?:isso|ai))?\s*[:,]?\s*"
    r"(?:(?:na\s+)?(?:sua\s+)?memoria\s*[:,]?\s*)?(?:que\s+|de\s+que\s+)?(.+)$")


def _juntar(lista: list) -> str:
    return ". ".join(f["texto"].strip().rstrip(".") for f in lista) + "."


def _agenda_falada() -> str:
    """Alarmes, lembretes e compromissos pendentes (vêm do jarvis_extras), prontos para falar."""
    try:
        import jarvis_extras as EX
        agora = datetime.now()
        itens = [(q, m) for q, m in EX._pendentes() if q > agora]
        if not itens:
            return ""
        partes = [f"{m}, {EX.descrever_quando(q, agora)}" for q, m in itens[:5]]
    except Exception:
        return ""
    n = len(itens)
    mais = f" E mais {n - 5}." if n > 5 else ""
    return (f"Na agenda, o senhor tem {'um compromisso' if n == 1 else f'{n} compromissos'}: "
            + "; ".join(partes) + "." + mais)


def comando_memoria(J, texto: str):
    """Devolve True se tratou o comando; None se não é sobre memória (siga o fluxo normal)."""
    try:   # datas especiais (aniversários que se repetem todo ano): vivem no jarvis_extras
        import jarvis_extras as EX
        if EX.comando_data(J, texto):
            return True
    except ImportError:
        pass
    except Exception:
        import traceback
        print("[memória] ERRO nas datas:\n" + traceback.format_exc())
    t = _alinhado(texto)
    palavras = set(re.findall(r"[a-z0-9]+", t))
    fala_memoria = bool(palavras & {"memoria", "lembranca", "lembrancas", "anotacao", "anotacoes"})
    if re.search(r"\bme\s+(?:lembr|anot)", t):   # "me lembra de..." seria um lembrete (não faço)
        return None

    # ---- esquecer ----
    if any(p.startswith("esquec") for p in palavras) or (
            fala_memoria and any(p.startswith(("apag", "limp", "delet")) for p in palavras)):
        fatos = carregar()
        if "tudo" in palavras or (fala_memoria and len(_chaves(re.sub(r"memoria|lembranc\w*|anotac\w*|apag\w*|limp\w*|delet\w*", " ", t))) == 0):
            if not fatos:
                J.falar("A minha memória já está vazia, senhor.")
                return True
            if J.perguntar_sim_nao(
                    ("Apagar a lembrança" if len(fatos) == 1 else f"Apagar as {len(fatos)} lembranças")
                    + ", senhor? Isso não dá para desfazer.") is not True:
                J.falar("Certo, não apaguei nada.")
                return True
            _salvar([])
            print("[ação] memória apagada")
            J.falar("Memória apagada, senhor.")
            return True
        chaves = _chaves(re.sub(r"esquec\w*", " ", t))
        manter = [f for f in fatos if not (chaves and _combina(f["texto"], chaves) == len(chaves))]
        apagadas = len(fatos) - len(manter)
        if apagadas:
            _salvar(manter)
            print(f"[ação] {apagadas} lembrança(s) esquecida(s)")
            J.falar("Esqueci, senhor." if apagadas == 1 else f"Esqueci {apagadas} lembranças, senhor.")
        else:
            J.falar("Não encontrei isso na minha memória, senhor.")
        return True

    # ---- consultar ----
    pergunta = bool(re.search(r"\b(o que|qual|quais|quando|onde|quem|voce lembra|lembra de|lembra do|lembra da)\b", t)) \
        or "?" in texto
    consulta = re.search(r"\b(?:lembra|lembrou|recorda)\s+(?:de|do|da|dos|das|sobre)\s+(.+)$", t) \
        or re.search(r"\b(?:te\s+)?(?:falei|disse|pedi|contei)\s+(?:sobre|de|do|da)\s+(.+)$", t) \
        or re.search(r"\bo que (?:voce )?(?:sabe|lembra|anotou|guardou)\s+(?:sobre|de|do|da)\s+(.+)$", t)
    if consulta:
        achados = buscar(consulta.group(1))
        if achados:
            J.falar("Sim, senhor. " + _juntar(achados[:LER_ATE]))
            return True
        if re.search(r"\b(lembra|lembrou|recorda|falei|disse|pedi|contei)\b", t):
            J.falar("Não tenho nada sobre isso na minha memória, senhor.")
            return True
        return None  # "o que você sabe sobre buracos negros" -> deixa o Gemini responder

    listar = (fala_memoria and bool(palavras & {"mostr", "mostra", "mostrar", "lista", "listar", "le", "ler", "fala", "falar", "diz", "dizer", "ve"})) \
        or bool(re.search(r"\bo que (?:voce )?(?:lembra|anotou|guardou|tem (?:na|em) (?:sua )?memoria)\b", t)) \
        or bool(re.search(r"\b(?:minhas )?(?:lembrancas|anotacoes)\b", t) and pergunta)
    if listar:
        fatos = carregar()
        agenda = _agenda_falada()
        if not fatos and not agenda:
            J.falar("Ainda não tenho nenhuma lembrança, senhor.")
        else:
            partes = []
            if fatos:
                ultimos = fatos[-LER_ATE:]
                quantas = f"{len(fatos)} lembrança" + ("s" if len(fatos) > 1 else "")
                partes.append(f"Tenho {quantas}. As mais recentes: " + _juntar(ultimos[::-1]))
            else:
                partes.append("Não tenho nenhuma lembrança guardada.")
            if agenda:
                partes.append(agenda)
            J.falar(" ".join(partes))
        return True

    # ---- guardar ----
    if True:
        m = _SALVAR.search(t)
        if m:
            verbo, resto = m.group(1), m.group(2).strip()
            forte = verbo.startswith(("lembr", "memoriz")) and not re.match(r"(de|do|da|dos|das|sobre|se)\b", resto)
            fraco = verbo.startswith(("anot", "guard", "regist")) and (
                fala_memoria or re.search(r"\bque\b", t[:m.start(2) + 8]) or ":" in texto)
            if (forte or fraco) and len(resto) >= 3:
                i = m.start(2)
                original = texto[i:].strip().rstrip("?").strip()
                fatos = carregar()
                if any(_alinhado(f["texto"]) == _alinhado(original) for f in fatos):
                    J.falar("Isso eu já sabia, senhor.")
                    return True
                fatos.append({"texto": original, "quando": datetime.now().isoformat(timespec="minutes")})
                _salvar(fatos)
                print(f"[ação] lembrança guardada: {original}")
                J.falar("Anotado, senhor. Vou lembrar que " + original.rstrip(".") + ".")
                return True
    return None