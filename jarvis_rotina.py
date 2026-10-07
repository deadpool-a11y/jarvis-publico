"""
Jarvis - rotina de início e controle do PC.

Coloque este arquivo na MESMA pasta do jarvis_acoes.py.

Quando o Jarvis abre junto com o Windows ele:
    1. fala o tempo (previsão do dia);
    2. fala seus compromissos de hoje (se você configurar a agenda, veja abaixo);
    3. lê as principais notícias;
    4. toca AC/DC - Back In Black, ele mesmo (sem navegador).

MÚSICA: coloque o arquivo da música (mp3) na mesma pasta com o nome
back_in_black.mp3 (ou mude MUSICA_ARQUIVO abaixo).

Comandos de voz novos:
    "Jarvis, desligar pc"  /  "reiniciar pc"  /  "bloquear"
    "Jarvis, cancelar desligamento"
    "Jarvis, bom dia" ou "resumo do dia"  (repete o resumo, sem música)
    "Jarvis, notícias"  /  "previsão do tempo"  /  "meus compromissos"
    "Jarvis, limpar armazenamento"  (apaga temporários antigos e esvazia a lixeira)
    "Jarvis, toca Back In Black"
    "Jarvis, pausa a música"  (pausa/continua)   |   "Jarvis, para a música"
    "Jarvis, abrir minha agenda"  (abre o Google Agenda de hoje no navegador)
    "Jarvis, agendar dentista amanhã às 15 horas"  (quem cria o evento é o jarvis_acoes.py)

Para testar a rotina sem reiniciar o PC:
    python jarvis_hud.py --rotina

AGENDA (opcional, de graça): no Google Agenda, em Configurações da sua agenda,
copie o "Endereço secreto no formato iCal" e salve no Windows, SEM escrever no código:
    setx JARVIS_AGENDA "https://calendar.google.com/calendar/ical/....ics"
(feche e abra o terminal depois; se o Jarvis abre com o Windows, reinicie o PC uma vez)
Depois rode:  pip install icalendar recurring-ical-events
"""

import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as hora_do_dia
from urllib.parse import quote_plus


def _link_da_agenda() -> str:
    """Lê o link da agenda da variável JARVIS_AGENDA (também procura direto no registro do Windows)."""
    link = os.environ.get("JARVIS_AGENDA", "").strip()
    if not link:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                link = str(winreg.QueryValueEx(k, "JARVIS_AGENDA")[0]).strip()
        except Exception:
            link = ""
    if link.startswith("webcal://"):
        link = "https://" + link[len("webcal://"):]
    return link


# ---------------- Configurações (pode ajustar) ----------------
CIDADE = "Foz do Iguaçu"
CALENDARIO_ICS = _link_da_agenda()   # vem da variável JARVIS_AGENDA (veja no topo do arquivo)
GOOGLE_CONTA = __import__("os").environ.get("JARVIS_GOOGLE_CONTA", "")            # opcional: seu e-mail do Google, se o navegador abrir a conta errada
NOTICIAS_QTD = 4             # quantas manchetes ele lê
MUSICA_ARQUIVO = "back_in_black.mp3"   # arquivo da música, na MESMA pasta do Jarvis
MUSICA_VOLUME = 70           # 0 a 100
VOZ = "pt-BR-AntonioNeural"  # voz masculina, calma (outras: pt-PT-DuarteNeural, pt-BR-FranciscaNeural)
VOZ_VELOCIDADE = "-6%"       # negativo = mais devagar e elegante
VOZ_TOM = "-8Hz"             # negativo = mais grave
TOCAR_MUSICA_NO_INICIO = True
LIMPEZA_CONFIRMAR = True     # pergunta "sim ou não" antes de limpar
LIMPAR_LIXEIRA = True        # também esvazia a lixeira (não dá para desfazer)
CONFIRMAR_DESLIGAR = True    # pergunta "sim ou não" antes de desligar/reiniciar
SEGUNDOS_ATE_DESLIGAR = 15   # tempo para você dizer "cancelar desligamento"
# --------------------------------------------------------------

JANELA_CMD = 0x08000000  # não abre janela preta ao rodar comandos do Windows


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


MINUTOS_APOS_LIGAR = 10  # se o PC ligou há menos que isso, conta como "início do Windows"


def _minutos_ligado() -> float:
    try:
        k = ctypes.windll.kernel32
        k.GetTickCount64.restype = ctypes.c_ulonglong
        return k.GetTickCount64() / 60000
    except Exception:
        return 9999.0


def iniciou_com_windows() -> bool:
    """True se abriu com o parâmetro OU se o PC acabou de ligar (não depende do atalho de início)."""
    motivo = None
    if "--inicio-windows" in sys.argv or "--rotina" in sys.argv:
        motivo = "parâmetro na linha de comando"
    elif _minutos_ligado() < MINUTOS_APOS_LIGAR:
        motivo = f"PC ligado há menos de {MINUTOS_APOS_LIGAR} minutos"
    print(f"[rotina] rotina de início: {'SIM (' + motivo + ')' if motivo else 'não'}")
    return motivo is not None


# ======================= INTERNET =======================

def _baixar(url: str, timeout: int = 15) -> bytes:
    """Baixa uma página. Tenta 3 vezes porque, ao ligar o PC, a internet pode demorar."""
    ultimo = None
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as erro:
            ultimo = erro
            time.sleep(3)
    raise ultimo


CODIGOS_TEMPO = {
    0: "céu limpo", 1: "céu quase limpo", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina", 51: "garoa fraca", 53: "garoa", 55: "garoa forte",
    56: "garoa gelada", 57: "garoa gelada", 61: "chuva fraca", 63: "chuva", 65: "chuva forte",
    66: "chuva gelada", 67: "chuva gelada", 71: "neve fraca", 73: "neve", 75: "neve forte",
    77: "neve", 80: "pancadas de chuva fracas", 81: "pancadas de chuva",
    82: "pancadas de chuva fortes", 85: "neve", 86: "neve forte", 95: "tempestade",
    96: "tempestade com granizo", 99: "tempestade com granizo forte",
}


def clima() -> str:
    """Previsão do tempo (Open-Meteo: gratuito e sem chave)."""
    geo = json.loads(_baixar(
        "https://geocoding-api.open-meteo.com/v1/search?count=1&language=pt&name=" + quote_plus(CIDADE)))
    local = geo["results"][0]
    prev = json.loads(_baixar(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={local['latitude']}&longitude={local['longitude']}"
        "&current=temperature_2m,apparent_temperature,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=1"))
    atual, dia = prev["current"], prev["daily"]

    texto = (f"Em {local['name']}, agora faz {round(atual['temperature_2m'])} graus, "
             f"{CODIGOS_TEMPO.get(atual['weather_code'], 'tempo variável')}, "
             f"com sensação de {round(atual['apparent_temperature'])}. "
             f"Hoje a máxima é de {round(dia['temperature_2m_max'][0])} "
             f"e a mínima de {round(dia['temperature_2m_min'][0])} graus")
    chuva = dia.get("precipitation_probability_max", [None])[0]
    if chuva is not None:
        texto += f", com {round(chuva)} por cento de chance de chuva"
    return texto + "."


def noticias() -> str:
    """Principais manchetes do Google Notícias (Brasil)."""
    dados = _baixar("https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419")
    itens = ET.fromstring(dados).findall("./channel/item")[:NOTICIAS_QTD]
    titulos = []
    for item in itens:
        titulo = (item.findtext("title") or "").strip()
        if " - " in titulo:
            titulo = titulo.rsplit(" - ", 1)[0]  # tira o nome do jornal do final
        if titulo:
            titulos.append(titulo)
    if not titulos:
        return ""
    return "As principais notícias de agora. " + ". ".join(titulos) + "."


# ---------- Notícias com imagem (aparecem no HUD enquanto o Jarvis lê) ----------
NOTICIAS_FEED = "https://g1.globo.com/rss/g1/"   # feed que traz foto em cada notícia (pode trocar)
NOTICIA = {"ativo": False, "titulo": "", "imagem": None, "indice": 0, "total": 0}   # o HUD lê isto


def _imagem_do_item(item):
    """Procura o endereço da foto de uma notícia do feed (media:content, thumbnail, enclosure ou <img>)."""
    for el in item.iter():
        marca = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
        url = el.get("url") or ""
        if marca in ("content", "thumbnail", "enclosure") and url.startswith("http"):
            tipo = (el.get("type") or "").lower()
            if tipo.startswith("video"):
                continue
            if marca != "enclosure" or tipo.startswith("image") or re.search(r"\.(jpe?g|png|webp)", url, re.I):
                return url
    desc = item.findtext("description") or ""
    m = re.search(r"""<img[^>]+src=["']([^"']+)""", desc, re.I)
    return m.group(1) if m else ""


def _noticias_itens():
    """Lista de (manchete, endereço da foto). Vazia se o feed não responder."""
    try:
        itens = ET.fromstring(_baixar(NOTICIAS_FEED, timeout=15)).findall("./channel/item")
    except Exception as erro:
        print(f"[rotina] feed de notícias com imagem indisponível: {erro}")
        return []
    saida = []
    for item in itens:
        titulo = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
        if titulo:
            saida.append((titulo, _imagem_do_item(item)))
        if len(saida) >= NOTICIAS_QTD:
            break
    return saida


def _baixar_imagem(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            dados = r.read(6_000_000)
        return dados if len(dados) > 500 else None
    except Exception as erro:
        print(f"[rotina] não consegui baixar uma imagem de notícia: {erro}")
        return None


def ler_noticias(J):
    """Lê as manchetes uma a uma, mostrando a imagem de cada no HUD. Se o feed falhar, lê só o texto."""
    itens = _noticias_itens()
    if not itens:
        texto = noticias()
        J.falar(texto or "Não consegui obter as notícias, senhor.")
        return ""
    fotos = [_baixar_imagem(url) for _, url in itens]
    J.falar("As principais notícias de agora.")
    try:
        for i, ((titulo, _), foto) in enumerate(zip(itens, fotos), 1):
            NOTICIA.update(ativo=True, titulo=titulo, imagem=foto, indice=i, total=len(itens))
            J.falar(titulo.rstrip(".") + ".")
    finally:
        NOTICIA["ativo"] = False
        NOTICIA["imagem"] = None
    return ""


# ---------- Gráfico de porcentagens (aparece no HUD enquanto o Jarvis fala) ----------
GRAFICO = {"ativo": False, "dados": []}   # o HUD lê isto: lista de (nome, valor)

_NUM_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)", re.I)
_FILLER = {
    "tem", "com", "é", "são", "de", "do", "da", "dos", "das", "e", "a", "o", "os", "as", "em", "para",
    "por", "contra", "tinha", "aparece", "lidera", "segue", "aponta", "marca", "fica", "ficou", "tendo",
    "teve", "cento", "pesquisa", "segundo", "versus", "x", "vs", "na", "no", "que", "seguido", "seguida",
    "ante", "aos", "às", "ao", "um", "uma", "já", "ainda", "mais", "menos", "cerca", "aproximadamente",
    "hoje", "agora", "foi", "ser", "está", "estão", "fez", "tiveram", "chega", "alcança", "atinge",
}


def _palavras_uteis(trecho):
    palavras = re.findall(r"[^\W\d_][\w'-]*", trecho, re.U)
    return [p for p in palavras if p.lower() not in _FILLER]


def extrair_percentuais(texto):
    """Acha 'nome + porcentagem' no texto. Devolve lista de (nome, valor), no máximo 6."""
    texto = texto or ""
    achados = list(_NUM_PCT.finditer(texto))
    dados = []
    anterior = 0
    for i, m in enumerate(achados[:6]):
        try:
            valor = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        fim = achados[i + 1].start() if i + 1 < len(achados) else len(texto)
        partes_antes = re.split(r"[:;.!?,]", texto[anterior:m.start()])
        trecho_depois = re.split(r"[:;.!?,]", texto[m.end():fim])[0]
        nome = " ".join(_palavras_uteis(partes_antes[-1])[-2:])
        usou_depois = False
        if not nome:
            nome = " ".join(_palavras_uteis(trecho_depois)[:2])
            usou_depois = bool(nome)
        if not nome:
            for parte in reversed(partes_antes[:-1]):
                p = _palavras_uteis(parte)
                if p:
                    nome = " ".join(p[-2:])
                    break
        dados.append((nome[:20] or f"Item {len(dados) + 1}", valor))
        anterior = m.end() + (len(trecho_depois) if usou_depois else 0)
    return dados


def preparar_grafico(texto):
    """Chamado pelo HUD antes de cada fala: liga o gráfico se o texto tiver porcentagem."""
    try:
        dados = extrair_percentuais(texto) if isinstance(texto, str) else []
    except Exception as erro:
        print(f"[rotina] erro ao montar o gráfico: {erro}")
        dados = []
    GRAFICO["dados"] = dados
    GRAFICO["ativo"] = bool(dados)


def _local(dt):
    """Converte para a hora local, sem fuso (para comparar e ordenar)."""
    if isinstance(dt, datetime):
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    return datetime.combine(dt, hora_do_dia.min)


def _hora_falada(dt: datetime) -> str:
    return f"{dt.hour} horas" if dt.minute == 0 else f"{dt.hour} horas e {dt.minute}"


def compromissos(explicito: bool = True) -> str:
    """Compromissos de hoje, lidos da agenda (link iCal)."""
    if not CALENDARIO_ICS:
        return "A sua agenda ainda não está configurada, senhor." if explicito else ""
    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        return ("Para ler a agenda preciso do comando: pip install icalendar "
                "recurring-ical-events.") if explicito else ""

    cal = icalendar.Calendar.from_ical(_baixar(CALENDARIO_ICS, timeout=25))
    agora = datetime.now()
    lista = []
    for ev in recurring_ical_events.of(cal).at(date.today()):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        ini_bruto = ev.get("DTSTART").dt
        dia_todo = not isinstance(ini_bruto, datetime)
        ini = _local(ini_bruto)
        if not dia_todo and ev.get("DTEND") is not None and _local(ev.get("DTEND").dt) < agora:
            continue  # já terminou
        lista.append((ini, str(ev.get("SUMMARY", "compromisso sem título")), dia_todo))

    if not lista:
        return "O senhor não tem mais compromissos hoje."
    lista.sort(key=lambda x: x[0])
    partes = [f"{titulo}, o dia todo" if todo else f"{titulo}, às {_hora_falada(ini)}"
              for ini, titulo, todo in lista]
    quantos = "um compromisso" if len(partes) == 1 else f"{len(partes)} compromissos"
    return f"Hoje o senhor tem {quantos}. " + ". ".join(partes) + "."


def abrir_agenda_no_navegador(J):
    """Abre o Google Agenda, na visão do dia (hoje), no navegador."""
    url = "https://calendar.google.com/calendar/r/day"
    if GOOGLE_CONTA:
        url += "?authuser=" + quote_plus(GOOGLE_CONTA)
    J.falar("Abrindo a sua agenda, senhor.")
    print(f"[ação] abrindo a agenda no navegador: {url}")
    abrir = getattr(J, "_abrir_url", None)  # usa o navegador padrão que o Jarvis já descobriu
    if callable(abrir):
        abrir(url)
    else:
        webbrowser.open(url)


# ======================= MÚSICA =======================
# Toca o arquivo de música dentro do próprio Jarvis (sem abrir navegador),
# usando o player do Windows (MCI). Funciona com mp3, wav e wma.

def _mci(comando: str):
    """Manda um comando ao player do Windows. Devolve (código de erro, resposta)."""
    buf = ctypes.create_unicode_buffer(255)
    erro = ctypes.windll.winmm.mciSendStringW(comando, buf, 254, 0)
    return erro, buf.value


def _achar_musica() -> str:
    pasta = os.path.dirname(os.path.abspath(__file__))
    exato = os.path.join(pasta, MUSICA_ARQUIVO)
    if os.path.exists(exato):
        return exato
    for padrao in ("*back*black*", "*acdc*", "*ac*dc*"):  # nomes parecidos
        for f in glob.glob(os.path.join(pasta, padrao)):
            if f.lower().endswith((".mp3", ".wav", ".wma")):
                return f
    return ""


def tocar_musica(J):
    caminho = _achar_musica()
    if not caminho:
        print(f"[rotina] música não encontrada. Coloque '{MUSICA_ARQUIVO}' na pasta do Jarvis.")
        J.falar(f"Não encontrei o arquivo da música, senhor. Coloque {MUSICA_ARQUIVO} na minha pasta.")
        return
    _mci("close musica")
    erro, _ = _mci(f'open "{caminho}" alias musica')
    if erro:
        print(f"[rotina] o Windows não conseguiu abrir a música (erro {erro}): {caminho}")
        J.falar("Não consegui abrir o arquivo de música, senhor.")
        return
    _mci(f"setaudio musica volume to {int(MUSICA_VOLUME) * 10}")
    _mci("play musica")
    print(f"[ação] tocando música: {os.path.basename(caminho)}")


def _tecla_midia(J):
    tecla = getattr(J, "_tecla", None)
    if callable(tecla):
        tecla(0xB3)  # play/pause do Windows: o Spotify no navegador obedece


def pausar_musica(J):
    """Pausa/continua. Se não for o MP3 local, manda play/pause para o Spotify."""
    erro, modo = _mci("status musica mode")
    if erro or modo not in ("playing", "paused"):
        _tecla_midia(J)
        print("[ação] play/pause enviado ao Windows")
    elif modo == "playing":
        _mci("pause musica")
        print("[ação] música pausada")
    else:
        _mci("resume musica")
        print("[ação] música retomada")


def parar_musica(J):
    erro, modo = _mci("status musica mode")
    if erro or modo not in ("playing", "paused"):
        _tecla_midia(J)
        return
    _mci("stop musica")
    _mci("close musica")
    print("[ação] música parada")


# ======================= VOZ =======================
# Voz neural (edge-tts, gratuita, precisa de internet): mais natural e grave que a do Windows.
# Se não houver internet ou o pacote não estiver instalado, usa a voz antiga do Windows.
# Instalar:  pip install edge-tts

def voz_do_filme(falar_padrao):
    """Devolve uma função falar() com a voz neural, que cai para a voz antiga se der erro."""
    aviso = []

    def gerar(texto, caminho):
        import asyncio
        import edge_tts
        asyncio.run(edge_tts.Communicate(texto, VOZ, rate=VOZ_VELOCIDADE, pitch=VOZ_TOM).save(caminho))

    def falar(texto: str):
        fd, caminho = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            gerar(texto, caminho)
            if os.path.getsize(caminho) < 500:
                raise RuntimeError("áudio vazio")
        except Exception as erro:
            if not aviso:
                aviso.append(True)
                print(f"[rotina] voz neural indisponível ({erro}); usando a voz do Windows. "
                      "Para ativar: pip install edge-tts")
            try:
                os.remove(caminho)
            except OSError:
                pass
            return falar_padrao(texto)

        print("Jarvis:", texto, "\n")
        try:
            _mci("setaudio musica volume to 250")  # abaixa a música enquanto ele fala
            _mci("close jvoz")
            erro, _ = _mci(f'open "{caminho}" alias jvoz')
            if erro:
                raise RuntimeError(f"erro {erro} ao abrir o áudio")
            _mci("play jvoz wait")
        except Exception as erro:
            print(f"[rotina] não consegui tocar a voz ({erro})")
        finally:
            _mci("close jvoz")
            _mci(f"setaudio musica volume to {int(MUSICA_VOLUME) * 10}")
            try:
                os.remove(caminho)
            except OSError:
                pass

    return falar


# ======================= CONTROLE DO PC =======================

def _desligar_ou_reiniciar(J, reiniciar: bool):
    verbo = "Reiniciar" if reiniciar else "Desligar"
    if CONFIRMAR_DESLIGAR:
        if J.perguntar_sim_nao(f"{verbo} o computador, senhor?") is not True:
            J.falar("Certo, cancelado.")
            return
    subprocess.run(["shutdown", "/r" if reiniciar else "/s", "/t", str(SEGUNDOS_ATE_DESLIGAR)],
                   capture_output=True, creationflags=JANELA_CMD)
    print(f"[ação] {'reiniciando' if reiniciar else 'desligando'} o PC em {SEGUNDOS_ATE_DESLIGAR}s")
    J.falar(f"{'Reiniciando' if reiniciar else 'Desligando'} em {SEGUNDOS_ATE_DESLIGAR} segundos. "
            "Diga Jarvis, cancelar desligamento, para desistir.")


def _cancelar_desligamento(J):
    r = subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=JANELA_CMD)
    if r.returncode == 0:
        print("[ação] desligamento cancelado")
        J.falar("Desligamento cancelado, senhor.")
    else:
        J.falar("Não há nenhum desligamento programado, senhor.")


def _bloquear(J):
    print("[ação] bloqueando o PC")
    J.falar("Bloqueando o computador, senhor.")
    ctypes.windll.user32.LockWorkStation()


# ======================= LIMPEZA DE ARMAZENAMENTO =======================

def _eh_link(caminho: str) -> bool:
    """True para atalhos/junções de pasta (o Jarvis nunca entra neles ao limpar)."""
    try:
        k = ctypes.windll.kernel32
        k.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        k.GetFileAttributesW.restype = ctypes.c_uint
        attrs = k.GetFileAttributesW(caminho)
        return attrs != 0xFFFFFFFF and bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except Exception:
        return True  # na dúvida, não mexe


def _apagar_temporarios() -> int:
    """Apaga arquivos temporários com mais de 24 horas. Devolve os bytes liberados."""
    pastas = {os.environ.get("TEMP", ""), os.environ.get("TMP", ""),
              os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")}
    limite = time.time() - 24 * 3600  # arquivos de hoje ficam (podem estar em uso)
    total = 0
    for pasta in pastas:
        if not pasta or not os.path.isdir(pasta):
            continue
        vazias = []
        for atual, subpastas, arquivos in os.walk(pasta):
            subpastas[:] = [d for d in subpastas if not _eh_link(os.path.join(atual, d))]
            vazias += [os.path.join(atual, d) for d in subpastas]
            for nome in arquivos:
                caminho = os.path.join(atual, nome)
                try:
                    if os.path.getmtime(caminho) > limite:
                        continue
                    tamanho = os.path.getsize(caminho)
                    os.remove(caminho)
                    total += tamanho
                except OSError:
                    pass  # arquivo em uso ou sem permissão: ignora
        for d in sorted(vazias, key=len, reverse=True):
            try:
                os.rmdir(d)  # só remove se a pasta estiver vazia
            except OSError:
                pass
    return total


def limpar_armazenamento(J):
    """Apaga temporários antigos e esvazia a lixeira. Não toca em Documentos, Downloads etc."""
    if LIMPEZA_CONFIRMAR:
        aviso = "Vou apagar os arquivos temporários antigos" + \
                (" e esvaziar a lixeira" if LIMPAR_LIXEIRA else "") + ". Continuar, senhor?"
        if J.perguntar_sim_nao(aviso) is not True:
            J.falar("Certo, cancelado.")
            return

    J.falar("Limpando, senhor. Pode levar um minuto.")
    unidade = os.environ.get("SystemDrive", "C:") + "\\"
    try:
        antes = shutil.disk_usage(unidade).free
    except OSError:
        antes = 0

    liberado = _apagar_temporarios()
    if LIMPAR_LIXEIRA:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                           capture_output=True, timeout=180, creationflags=JANELA_CMD)
        except Exception as erro:
            print(f"[rotina] não consegui esvaziar a lixeira: {erro}")
    try:
        liberado = max(liberado, shutil.disk_usage(unidade).free - antes)
    except OSError:
        pass

    gb = liberado / 1024 ** 3
    quanto = f"{gb:.1f}".replace(".", ",") + " gigabytes" if gb >= 1 else f"{int(liberado / 1024 ** 2)} megabytes"
    print(f"[ação] limpeza concluída: {quanto} liberados")
    J.falar(f"Pronto, senhor. Liberei cerca de {quanto}.")


# ======================= ROTINAS =======================

def saudacao() -> str:
    """Bom dia (5h às 11h59), boa tarde (12h às 17h59) ou boa noite (18h às 4h59), pelo relógio do PC."""
    hora = datetime.now().hour
    if 5 <= hora < 12:
        return "Bom dia"
    if 12 <= hora < 18:
        return "Boa tarde"
    return "Boa noite"


def resumo(J, com_musica: bool = False):
    J.falar(f"{saudacao()}, senhor. Aqui está o seu resumo.")

    etapas = (("a previsão do tempo", clima),
              ("a agenda", lambda: compromissos(explicito=False)),
              ("as notícias", lambda: ler_noticias(J)))
    for nome, funcao in etapas:
        try:
            texto = funcao()
            if texto:
                J.falar(texto)
        except Exception as erro:
            print(f"[rotina] erro em {nome}: {erro}")
            J.falar(f"Não consegui obter {nome}, senhor.")

    if com_musica:
        J.falar("E agora, um pouco de AC/DC.")
        tocar_musica(J)


_JA_RODOU = []


def rotina_de_inicio(J):
    if _JA_RODOU:  # evita rodar duas vezes
        return
    _JA_RODOU.append(True)
    resumo(J, com_musica=TOCAR_MUSICA_NO_INICIO)


# ======================= COMANDOS DE VOZ =======================

PC = {"pc", "computador", "maquina", "notebook", "pece", "tela"}
LIMPEZA_PALAVRAS = {"armazenamento", "disco", "espaco", "lixo", "lixeira", "temporarios", "temporario"}
ENFEITES = {"jarvis", "por", "favor", "o", "a", "agora", "ai", "ok", "pode"}
# Palavras de quem quer CRIAR um compromisso (isso vai para a IA, não é "ler a agenda")
CRIAR_COMPROMISSO = {"agendar", "agende", "agendamento", "marcar", "marque", "criar", "crie",
                     "adicionar", "adicione", "anotar", "anote", "um", "uma",
                     "reuniao", "consulta", "lembrete", "evento"}


ABRIR = {"abrir", "abre", "abra", "mostrar", "mostra", "mostre", "exibir", "exiba"}


def comando_local(J, texto: str):
    """
    Trata os comandos do PC, do resumo e da música sem passar pela IA.
    Devolve True (tratado, continue ligado) ou None (não é comigo: siga o fluxo normal).
    """
    t = normalizar(texto)
    palavras = set(re.findall(r"[a-z0-9]+", t))
    compacto = re.sub(r"[^a-z]", "", t)
    tem_pc = bool(palavras & PC) or "p c" in t
    so_isso = len(palavras - ENFEITES) <= 1  # a frase é só o comando, ex.: "Jarvis, reiniciar"

    def raiz(*rs):
        return any(w.startswith(r) for w in palavras for r in rs)

    # ---- PC ----
    if raiz("cancel") and raiz("deslig", "reinic"):
        _cancelar_desligamento(J)
        return True
    if raiz("deslig") and tem_pc:          # "desligar" sozinho continua desligando só o Jarvis
        _desligar_ou_reiniciar(J, reiniciar=False)
        return True
    if raiz("reinic", "restart") and (tem_pc or so_isso):
        _desligar_ou_reiniciar(J, reiniciar=True)
        return True
    if raiz("bloque") and (tem_pc or so_isso):
        _bloquear(J)
        return True

    # ---- limpeza ----
    if raiz("limp", "liber") and (palavras & LIMPEZA_PALAVRAS or (tem_pc and raiz("limp"))):
        limpar_armazenamento(J)
        return True

    # ---- música ----
    fala_musica = "musica" in palavras or "som" in palavras
    if fala_musica and raiz("para", "encerr", "desliga"):
        parar_musica(J)
        return True
    if (fala_musica and raiz("paus", "continu", "retom")) or (raiz("paus") and so_isso):
        pausar_musica(J)
        return True

    # ---- resumo, tempo, notícias, agenda ----
    def dizer(funcao, falha):
        try:
            J.falar(funcao() or "Nada a informar, senhor.")
        except Exception as erro:
            print(f"[rotina] erro: {erro}")
            J.falar(falha)

    if "bom dia" in t or "resumo" in palavras or raiz("briefing"):
        resumo(J)
        return True
    if raiz("noticia", "manchete"):
        try:
            ler_noticias(J)
        except Exception as erro:
            print(f"[rotina] erro: {erro}")
            J.falar("Não consegui obter as notícias, senhor.")
        return True
    if raiz("previsao", "clima", "chover", "chuva") or "como esta o tempo" in t:
        dizer(clima, "Não consegui obter a previsão do tempo, senhor.")
        return True
    # "abrir minha agenda" -> abre no navegador (em vez de ler em voz alta)
    if palavras & ABRIR and ("agenda" in palavras or "calendario" in palavras):
        abrir_agenda_no_navegador(J)
        return True
    # "agendar dentista", "marcar um compromisso"... não é ler a agenda: segue para a IA
    if raiz("compromiss", "agenda") and not (palavras & CRIAR_COMPROMISSO):
        dizer(compromissos, "Não consegui ler a sua agenda, senhor.")
        return True

    return None