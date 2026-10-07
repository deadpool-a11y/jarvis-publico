"""
Jarvis com Ollama - escuta automática com modo de espera.

Como funciona:
    - Fica em ESPERA: escuta em silêncio, não escreve nada do que você fala
      e não faz nada, a menos que você diga "Jarvis".
    - Você diz "Jarvis"            -> ele responde "Sim, senhor." e espera o comando.
    - Você diz "Jarvis, <comando>" -> ele responde "Sim, senhor. Executando comando."
    - Depois de responder, volta para o modo de espera.
    - Para encerrar: "Jarvis, desligar" (ele diz "Até logo, senhor") ou Ctrl+C.

Antes de rodar:
    pip install sounddevice SpeechRecognition
    ollama pull qwen2.5:7b
    (o Ollama precisa estar aberto/rodando)

Trocar de modelo sem mexer no código:
    setx JARVIS_MODELO "llama3.1:8b"
"""

import array
import json
import math
import os
import subprocess
import urllib.error
import urllib.request
from collections import deque

import sounddevice as sd
import speech_recognition as sr

# ---------------- Configurações (pode ajustar) ----------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELO = os.environ.get("JARVIS_MODELO", "qwen2.5:7b")
TAXA = 16000                 # taxa de amostragem
BLOCO = 1600                 # 0,1 segundo de áudio por leitura
SILENCIO_BLOCOS = 12         # blocos de silêncio que encerram a fala (1,2 s)
MAX_BLOCOS = 150             # limite de gravação (15 s)
MIN_BLOCOS_FALA = 4          # ignora ruídos menores que 0,4 s
SENSIBILIDADE = 3.0          # menor = mais sensível ao microfone
ESPERA_COMANDO = 80          # blocos que ele espera você falar o comando (8 s)
PALAVRAS_ATIVACAO = ("jarvis", "jarves", "jarvez", "járvis", "jarvi")
PALAVRAS_SAIR = ("desligar", "encerrar", "tchau")
MEMORIA_MENSAGENS = 12       # quantas mensagens recentes o Jarvis lembra
# --------------------------------------------------------------

PERSONALIDADE = (
    "Você é o Jarvis, o assistente de IA de Tony Stark: educado, elegante, "
    "eficiente e com um toque sutil de humor britânico. Chame o usuário de "
    "'senhor' ou 'senhora'. Responda sempre em português do Brasil, de forma "
    "curta e direta, em no máximo três frases, sem emojis nem formatação "
    "(sem asteriscos, listas ou títulos), porque sua resposta será lida em voz alta."
)

# Histórico da conversa (o Ollama não guarda sozinho, então guardamos aqui)
historico = deque(maxlen=MEMORIA_MENSAGENS)

reconhecedor = sr.Recognizer()

SCRIPT_VOZ = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) {
    if ($v.VoiceInfo.Culture.Name -eq 'pt-BR') {
        $s.SelectVoice($v.VoiceInfo.Name)
        break
    }
}
$s.Rate = 1
$s.Speak($env:JARVIS_TEXTO)
"""


class ErroOllama(Exception):
    """Erro do Ollama. 'fatal' = não adianta continuar (ex.: modelo não instalado)."""

    def __init__(self, mensagem: str, fatal: bool = False):
        super().__init__(mensagem)
        self.fatal = fatal


def falar(texto: str):
    print("Jarvis:", texto, "\n")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", SCRIPT_VOZ],
            env={**os.environ, "JARVIS_TEXTO": texto},
            check=False,
        )
    except Exception as erro:
        print(f"(Não consegui falar em voz alta: {erro})")


def volume(bloco: bytes) -> float:
    """Calcula o volume (RMS) de um pedaço de áudio."""
    amostras = array.array("h", bloco)
    if not amostras:
        return 0.0
    return math.sqrt(sum(a * a for a in amostras) / len(amostras))


def calibrar() -> float:
    """Mede o ruído do ambiente por 1 segundo para definir o limite de voz."""
    print("Calibrando o ruído do ambiente... fique em silêncio por 1 segundo.")
    medidas = []
    with sd.RawInputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO) as fluxo:
        for _ in range(10):
            dados, _ = fluxo.read(BLOCO)
            medidas.append(volume(bytes(dados)))
    ruido = sum(medidas) / len(medidas)
    limiar = max(ruido * SENSIBILIDADE, 350)
    print(f"Ruído do ambiente: {ruido:.0f} | Limite de voz: {limiar:.0f}\n")
    return limiar


def escutar(limiar: float, espera_max: int = 0) -> bytes:
    """
    Espera você falar, grava até o silêncio e devolve o áudio.
    Se espera_max > 0 e ninguém falar nesse tempo (em blocos), devolve vazio.
    """
    # O microfone só fica aberto aqui, então o Jarvis não escuta a própria voz.
    anterior = deque(maxlen=3)  # guarda um pouco antes da fala para não cortar o começo
    gravado = []
    falando = False
    silencio = 0
    esperou = 0

    with sd.RawInputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO) as fluxo:
        while True:
            dados, _ = fluxo.read(BLOCO)
            bloco = bytes(dados)
            alto = volume(bloco) > limiar

            if not falando:
                anterior.append(bloco)
                if alto:
                    falando = True
                    gravado.extend(anterior)
                else:
                    esperou += 1
                    if espera_max and esperou >= espera_max:
                        return b""
                continue

            gravado.append(bloco)
            silencio = 0 if alto else silencio + 1

            if silencio >= SILENCIO_BLOCOS or len(gravado) >= MAX_BLOCOS:
                break

    if len(gravado) - silencio < MIN_BLOCOS_FALA:
        return b""  # foi só um barulho rápido
    return b"".join(gravado)


def transcrever(dados: bytes) -> str:
    audio = sr.AudioData(dados, TAXA, 2)
    try:
        return reconhecedor.recognize_google(audio, language="pt-BR")
    except (sr.UnknownValueError, sr.RequestError):
        return ""


def perguntar(texto: str) -> str:
    mensagens = [{"role": "system", "content": PERSONALIDADE}, *historico,
                 {"role": "user", "content": texto}]
    corpo = {
        "model": MODELO,
        "messages": mensagens,
        "stream": False,
        "keep_alive": "30m",   # mantém o modelo na memória (respostas mais rápidas)
        "options": {"temperature": 0.6},
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
            raise ErroOllama(f"O modelo '{MODELO}' não está instalado. Rode: ollama pull {MODELO}",
                             fatal=True)
        raise ErroOllama(f"O Ollama respondeu com erro {erro.code}.")
    except urllib.error.URLError:
        raise ErroOllama("Não consegui falar com o Ollama. Verifique se ele está aberto.", fatal=True)
    except TimeoutError:
        raise ErroOllama("O Ollama demorou demais para responder.")

    resposta = ((dados.get("message") or {}).get("content") or "").strip()
    if not resposta:
        return "Desculpe, senhor, não consegui formular uma resposta."

    historico.append({"role": "user", "content": texto})
    historico.append({"role": "assistant", "content": resposta})
    return resposta


def remover_ativacao(texto: str) -> str:
    resto = texto.lower()
    for palavra in PALAVRAS_ATIVACAO:
        resto = resto.replace(palavra, "")
    return resto.strip(" ,.!?")


def executar(texto: str, confirmar: bool) -> bool:
    """
    Executa o comando. Devolve False se o Jarvis deve ser desligado.
    confirmar=True quando o comando veio junto com a palavra "Jarvis"
    (então ele já diz o "Sim, senhor" aqui).
    """
    if any(p in texto.lower() for p in PALAVRAS_SAIR):
        falar("Até logo, senhor.")
        return False

    falar("Sim, senhor. Executando comando." if confirmar else "Executando comando.")

    try:
        falar(perguntar(texto))
    except ErroOllama as erro:
        print(f"Jarvis: {erro}\n")
        if erro.fatal:
            return False
        falar("Tive um problema, senhor. Tente novamente em instantes.")
    except Exception as erro:
        print(f"Jarvis: Tive um problema ({erro}). Tente de novo.\n")

    return True


def main():
    print("Jarvis online. Ctrl+C para sair.\n")
    falar("Sistemas online. Diga Jarvis quando precisar de mim, senhor.")
    limiar = calibrar()
    print("Em espera. Diga 'Jarvis' para me chamar.\n")

    while True:
        try:
            # --- MODO DE ESPERA: escuta em silêncio, sem escrever nada ---
            dados = escutar(limiar)
            if not dados:
                continue

            texto = transcrever(dados)
            if not texto:
                continue

            if not any(p in texto.lower() for p in PALAVRAS_ATIVACAO):
                continue  # não chamou o Jarvis: ignora completamente

            # --- Chamou o Jarvis ---
            if len(remover_ativacao(texto)) < 2:
                # Só disse "Jarvis": confirma e espera o comando
                falar("Sim, senhor.")
                dados = escutar(limiar, espera_max=ESPERA_COMANDO)
                if not dados:
                    print("Em espera. Diga 'Jarvis' para me chamar.\n")
                    continue
                comando = transcrever(dados)
                if not comando:
                    print("Em espera. Diga 'Jarvis' para me chamar.\n")
                    continue
                continuar = executar(comando, confirmar=False)
            else:
                # Disse "Jarvis, <comando>" na mesma frase
                continuar = executar(texto, confirmar=True)

            if not continuar:
                break

            print("Em espera. Diga 'Jarvis' para me chamar.\n")

        except KeyboardInterrupt:
            print("\nJarvis desligado.")
            break


if __name__ == "__main__":
    main()