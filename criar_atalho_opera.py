"""
Cria na sua Área de Trabalho o atalho "Opera Jarvis".

Esse atalho abre o SEU Opera de sempre (mesmo perfil, mesmo WhatsApp já logado), só que deixa
uma porta aberta, só neste computador, para o Jarvis se conectar e ler/ligar no WhatsApp Web
sem abrir janela nova.

Como usar:
    1) Feche o Opera POR COMPLETO (veja também o ícone perto do relógio e o Gerenciador de Tarefas)
    2) python criar_atalho_opera.py
    3) A partir de agora abra o Opera por esse atalho
    4) Confira com:  python jarvis_mensagens.py porta
"""
import os
import subprocess
import sys

PORTA = 9222


def achar_opera():
    for pasta in ("Opera", "Opera GX"):
        for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            raiz = os.environ.get(var, "")
            if not raiz:
                continue
            for base in (os.path.join(raiz, "Programs", pasta), os.path.join(raiz, pasta)):
                for exe in ("launcher.exe", "opera.exe"):
                    caminho = os.path.join(base, exe)
                    if os.path.exists(caminho):
                        return caminho
    return None


def opera_aberto():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq opera.exe", "/NH"],
                           capture_output=True, timeout=20, creationflags=0x08000000)
        return b"opera.exe" in r.stdout.lower()
    except Exception:
        return False


def main():
    exe = achar_opera()
    if not exe:
        sys.exit("Não achei o Opera. Abra este arquivo e coloque o caminho do opera.exe na variável EXE.")
    print(f"Opera encontrado: {exe}")
    if opera_aberto():
        print("ATENÇÃO: o Opera está aberto. Feche ele por completo antes de usar o atalho novo, "
              "senão ele ignora a porta do Jarvis.")
    comando = (
        "$d=[Environment]::GetFolderPath('Desktop'); "
        "$l=Join-Path $d 'Opera Jarvis.lnk'; "
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); "
        "$s.TargetPath=$env:J_EXE; $s.Arguments='--remote-debugging-port=' + $env:J_PORTA; "
        "$s.WorkingDirectory=(Split-Path $env:J_EXE); $s.IconLocation=$env:J_EXE; $s.Save(); "
        "Write-Output $l")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                       env={**os.environ, "J_EXE": exe, "J_PORTA": str(PORTA)},
                       capture_output=True, timeout=60, creationflags=0x08000000)
    saida = r.stdout.decode("utf-8", "ignore").strip()
    if r.returncode != 0 or not saida:
        sys.exit("Não consegui criar o atalho: " + r.stderr.decode("utf-8", "ignore")[:300])
    print(f"Pronto! Atalho criado: {saida}")
    print("Agora abra o Opera por esse atalho e depois rode:  python jarvis_mensagens.py porta")


if __name__ == "__main__":
    main()