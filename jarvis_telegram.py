"""
Jarvis pelo Telegram: mande comandos do celular, de qualquer lugar, e o Jarvis do seu PC executa.

Coloque este arquivo na MESMA pasta do jarvis_acoes.py e rode o aplicar_telegram.py (uma vez).

CONFIGURAR (uma vez):
  1. No Telegram, fale com @BotFather, mande /newbot, escolha nome e usuário (terminado em "bot").
     Ele entrega um TOKEN (parece 123456789:ABCdef...). Trate como senha.
  2. No PowerShell:   setx JARVIS_TELEGRAM_TOKEN "COLE_O_TOKEN_AQUI"
  3. Feche e abra o Jarvis. Abra o seu bot no Telegram e mande qualquer mensagem.
     Ele responde com o seu ID numérico.
  4. No PowerShell:   setx JARVIS_TELEGRAM_ID "SEU_ID"
  5. Feche e abra o Jarvis de novo. Ele manda "Jarvis online no PC, senhor." no seu Telegram.

Depois é só conversar com o bot: "que horas são", "toca Queen", "cotação do dólar",
"anota comprar café", "desligar pc" (ele pede confirmação: responda sim ou não)...

SEGURANÇA
  - Só o seu ID obedece; qualquer outra pessoa é ignorada.
  - O PC não abre porta nenhuma: ele é quem busca as mensagens no Telegram.
  - Encerrar o Jarvis só pelo PC (não dá pelo celular).
  - Mensagens com mais de 2 minutos e as acumuladas enquanto o Jarvis estava fechado são ignoradas.
  - Se o token vazar: no @BotFather, /revoke.

MENSAGEM DE VOZ (opcional): precisa do ffmpeg instalado (winget install ffmpeg).
Sem ele, o bot avisa e funciona só com texto.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlencode

import jarvis_acoes as J

try:
    import jarvis_rotina as RT
except Exception:
    RT = None
try:
    import jarvis_memoria as MEM
except Exception:
    MEM = None

EXEC_ORIGINAL = J.executar   # guarda o executar ORIGINAL (o HUD troca depois por um que exige a sua voz)

FALAR_NO_PC = False    # True = o Jarvis também fala nas caixas de som do PC quando o pedido vem do celular
IDADE_MAXIMA = 120     # segundos: ignora mensagens mais velhas que isso

_local = threading.local()
_estado = {"offset": 0, "iniciado": False}

AJUDA = ("Jarvis pelo Telegram, senhor. Escreva o comando como falaria, por exemplo:\n"
         "- que horas são\n- toca Bohemian Rhapsody\n- cotação do dólar\n- o que eu tenho amanhã\n"
         "- anota comprar café\n- tira um print da tela\n- desligar pc\n"
         "Também aceito mensagem de voz (se o ffmpeg estiver instalado no PC).")


def _token():
    return J._variavel("JARVIS_TELEGRAM_TOKEN")


def _dono():
    return J._variavel("JARVIS_TELEGRAM_ID")


def _api(metodo, _tempo=30, **params):
    url = f"https://api.telegram.org/bot{_token()}/{metodo}"
    req = urllib.request.Request(url, data=urlencode(params).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=_tempo) as r:
        dados = json.loads(r.read().decode("utf-8"))
    if not dados.get("ok"):
        raise RuntimeError(str(dados.get("description")))
    return dados["result"]


def _enviar(texto, chat=None):
    chat = chat or _dono()
    texto = str(texto or "")
    if not chat or not texto:
        return
    for i in range(0, len(texto), 4000):
        try:
            _api("sendMessage", chat_id=chat, text=texto[i:i + 4000])
        except Exception as erro:
            print(f"[telegram] erro ao enviar: {erro}")


def _atualizacoes(espera):
    """Pede mensagens novas ao Telegram (espera até `espera` segundos) e avança o contador."""
    res = _api("getUpdates", _tempo=espera + 10, offset=_estado["offset"], timeout=espera,
               allowed_updates=json.dumps(["message"]))
    if res:
        _estado["offset"] = max(u["update_id"] for u in res) + 1
    return res


def _esperar_texto(segundos):
    fim = time.time() + segundos
    while time.time() < fim:
        for u in _atualizacoes(5):
            msg = u.get("message") or {}
            if str((msg.get("from") or {}).get("id")) == _dono() and msg.get("text"):
                return msg["text"]
    return ""


def _voz_para_texto(file_id):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("sem ffmpeg")
    caminho = _api("getFile", file_id=file_id)["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{_token()}/{caminho}", timeout=30) as r:
        dados = r.read()
    pasta = tempfile.mkdtemp()
    ogg, wav = os.path.join(pasta, "voz.ogg"), os.path.join(pasta, "voz.wav")
    try:
        with open(ogg, "wb") as f:
            f.write(dados)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", ogg, "-ar", "16000", "-ac", "1", wav],
                       capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import speech_recognition as sr
        reconhecedor = sr.Recognizer()
        with sr.AudioFile(wav) as fonte:
            audio = reconhecedor.record(fonte)
        try:
            return reconhecedor.recognize_google(audio, language="pt-BR").strip()
        except sr.UnknownValueError:
            return ""
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def _instalar():
    """Faz o Jarvis responder por Telegram (em vez de falar no PC) quando o pedido vem do celular."""
    if getattr(J.falar, "_tg", False):
        return
    falar_original = J.falar
    sim_nao_original = J.perguntar_sim_nao

    def falar(texto):
        if getattr(_local, "remoto", False):
            texto = (texto or "").strip()
            if texto and texto != "Executando comando.":
                print(f"Jarvis: {texto}\n")
                _enviar(texto)
            if FALAR_NO_PC:
                falar_original(texto)
            return
        return falar_original(texto)

    def perguntar_sim_nao(pergunta):
        if not getattr(_local, "remoto", False):
            return sim_nao_original(pergunta)
        _enviar(f"{pergunta} (responda sim ou não)")
        resposta = _esperar_texto(60)
        palavras = set(re.findall(r"[a-z]+", J.normalizar(resposta)))
        if palavras & {"nao", "cancela", "cancelar", "negativo", "deixa", "pare", "para"}:
            return False
        if palavras & {"sim", "pode", "confirmo", "claro", "positivo", "isso", "vai", "continua",
                       "continue", "certo", "ok", "afirmativo"}:
            return True
        return None

    falar._tg = True
    J.falar = falar
    J.perguntar_sim_nao = perguntar_sim_nao


def _executar_remoto(texto):
    if MEM:
        try:
            if MEM.comando_memoria(J, texto):
                return True
        except Exception as erro:
            print(f"[telegram] memória: {erro}")
    if RT:
        try:
            r = RT.comando_local(J, texto)
            if r is not None:
                return r
        except Exception as erro:
            print(f"[telegram] rotina: {erro}")
    return EXEC_ORIGINAL(texto, J.perguntar_sim_nao)


def _tratar(u):
    msg = u.get("message") or {}
    de = str((msg.get("from") or {}).get("id") or "")
    chat = msg.get("chat") or {}
    if chat.get("type") != "private" or not de:
        return
    dono = _dono()
    if not dono:
        _enviar(f"Seu ID do Telegram é {de}. No PowerShell: setx JARVIS_TELEGRAM_ID \"{de}\" "
                "e reinicie o Jarvis.", chat.get("id"))
        print(f"[telegram] mensagem do ID {de} (JARVIS_TELEGRAM_ID ainda não configurado)")
        return
    if de != dono:
        print(f"[telegram] ignorei mensagem do ID {de} (não autorizado)")
        return
    if time.time() - msg.get("date", time.time()) > IDADE_MAXIMA:
        return

    texto = (msg.get("text") or "").strip()
    if not texto and msg.get("voice"):
        try:
            texto = _voz_para_texto(msg["voice"]["file_id"])
        except Exception as erro:
            _enviar("Não consegui entender a mensagem de voz, senhor. "
                    + ("Instale o ffmpeg no PC (winget install ffmpeg)." if "ffmpeg" in str(erro)
                       else "Tente de novo ou escreva o comando."))
            return
        if not texto:
            _enviar("Não entendi o áudio, senhor.")
            return
        _enviar(f"Ouvi: {texto}")
    if not texto:
        return
    if texto.lower() in ("/start", "/ajuda", "/help"):
        _enviar(AJUDA)
        return

    m = J.CHAMADO.search(texto)
    if m and m.start() <= 2:
        texto = texto[m.end():].strip(" ,.!?;:-") or texto

    print(f"[ação] pedido pelo celular: {texto}")
    _instalar()
    _local.remoto = True
    try:
        r = _executar_remoto(texto)
        if r is False:
            _enviar("Por segurança, só encerro o Jarvis pelo próprio PC, senhor.")
    except Exception as erro:
        print(f"[telegram] erro: {erro}")
        _enviar("Tive um problema ao executar isso, senhor.")
    finally:
        _local.remoto = False


def _laco():
    time.sleep(20)  # espera o Jarvis terminar de iniciar
    try:  # descarta o que ficou acumulado enquanto o Jarvis estava fechado
        res = _api("getUpdates", offset=-1, timeout=0)
        if res:
            _estado["offset"] = res[-1]["update_id"] + 1
    except Exception as erro:
        print(f"[telegram] não consegui conectar: {erro}")
    if _dono():
        _enviar("Jarvis online no PC, senhor.")
        print("[telegram] online")
    else:
        print("[telegram] token ok. Mande uma mensagem ao bot para descobrir o seu ID.")
    while True:
        try:
            for u in _atualizacoes(25):
                _tratar(u)
        except Exception as erro:
            print(f"[telegram] {erro}")
            time.sleep(5)


def iniciar():
    if _estado["iniciado"]:
        return
    if not _token():
        print("(Telegram desativado: falta a variável JARVIS_TELEGRAM_TOKEN)")
        return
    _estado["iniciado"] = True
    threading.Thread(target=_laco, daemon=True, name="jarvis-telegram").start()