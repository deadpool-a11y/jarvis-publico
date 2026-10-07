import asyncio
import ctypes
import os
import subprocess
import tempfile

# ---------- Ajustes da voz ----------
VOZ = "pt-BR-AntonioNeural"   # outras: pt-PT-DuarteNeural, pt-BR-FranciscaNeural
VELOCIDADE = "-6%"            # negativo = mais devagar
TOM = "-8Hz"                  # negativo = mais grave
# ------------------------------------

# Voz antiga do Windows (usada só se a voz nova falhar)
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


def _mci(comando: str) -> int:
    return ctypes.windll.winmm.mciSendStringW(comando, None, 0, 0)


def _voz_windows(texto: str):
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", SCRIPT_VOZ],
            env={**os.environ, "JARVIS_TEXTO": texto},
            check=False,
        )
    except Exception as erro:
        print(f"(Não consegui falar em voz alta: {erro})")


def falar(texto: str):
    print("Jarvis:", texto, "\n")
    fd, caminho = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        import edge_tts
        asyncio.run(edge_tts.Communicate(texto, VOZ, rate=VELOCIDADE, pitch=TOM).save(caminho))
        if os.path.getsize(caminho) < 500:
            raise RuntimeError("áudio vazio")
        _mci("close jvoz")
        if _mci(f'open "{caminho}" alias jvoz') != 0:
            raise RuntimeError("o Windows não abriu o áudio")
        _mci("play jvoz wait")   # espera terminar de falar
    except Exception as erro:
        print(f"(Voz neural indisponível: {erro}. Usando a voz do Windows.)")
        _voz_windows(texto)
    finally:
        _mci("close jvoz")
        try:
            os.remove(caminho)
        except OSError:
            pass