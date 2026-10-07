"""
Jarvis - Passo 3: texto + voz + microfone (Windows).

Antes de rodar:
    pip install sounddevice SpeechRecognition

Como usar:
    - Aperte Enter (sem digitar nada) para falar. Aperte Enter de novo para parar.
    - Ou digite normalmente, se preferir texto.
"""

import os
import subprocess

import anthropic
import sounddevice as sd
import speech_recognition as sr

MODELO = "claude-sonnet-5-5"
TAXA = 16000  # taxa de amostragem do microfone

PERSONALIDADE = (
    "Você é o Jarvis, o assistente de IA de Tony Stark: educado, elegante, "
    "eficiente e com um toque sutil de humor britânico. Chame o usuário de "
    "'senhor' ou 'senhora'. Responda sempre em português do Brasil, de forma "
    "curta e direta, sem emojis nem formatação, porque sua resposta será "
    "lida em voz alta."
)

client = anthropic.Anthropic()
reconhecedor = sr.Recognizer()
historico = []

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


def gravar() -> bytes:
    """Grava do microfone até você apertar Enter."""
    pedacos = []

    def captura(indata, frames, tempo, status):
        pedacos.append(bytes(indata))

    with sd.RawInputStream(
        samplerate=TAXA, channels=1, dtype="int16", callback=captura
    ):
        input("   Gravando... aperte Enter para parar.")

    return b"".join(pedacos)


def ouvir() -> str:
    """Grava e transforma a fala em texto. Retorna '' se não entendeu."""
    dados = gravar()
    if not dados:
        return ""

    audio = sr.AudioData(dados, TAXA, 2)  # 2 bytes por amostra (int16)
    try:
        return reconhecedor.recognize_google(audio, language="pt-BR")
    except sr.UnknownValueError:
        print("Jarvis: Não consegui entender, senhor. Pode repetir?\n")
        return ""
    except sr.RequestError as erro:
        print(f"Jarvis: Erro no reconhecimento de voz ({erro}).\n")
        return ""


def perguntar(texto: str) -> str:
    historico.append({"role": "user", "content": texto})

    resposta = client.messages.create(
        model=MODELO,
        max_tokens=400,
        system=PERSONALIDADE,
        messages=historico,
    )

    texto_resposta = resposta.content[0].text
    historico.append({"role": "assistant", "content": texto_resposta})
    return texto_resposta


def main():
    print("Jarvis online.")
    print("Enter = falar | digite para usar texto | 'sair' para encerrar.\n")
    falar("Olá, senhor. Em que posso ajudar?")

    while True:
        entrada = input("Você: ").strip()

        if not entrada:
            entrada = ouvir()
            if not entrada:
                continue
            print(f"Você disse: {entrada}")

        if entrada.lower().strip(".!? ") in ("sair", "desligar", "exit"):
            falar("Até logo, senhor.")
            break

        try:
            falar(perguntar(entrada))
        except anthropic.AuthenticationError:
            print("Jarvis: Chave da API inválida ou não configurada.")
            break
        except Exception as erro:
            historico.pop()
            print(f"Jarvis: Tive um problema ({erro}). Tente de novo.\n")


if __name__ == "__main__":
    main()