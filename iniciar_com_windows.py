"""
Jarvis - abrir sozinho ao entrar no Windows E ao desbloquear o PC.

Coloque este arquivo na MESMA pasta do jarvis_hud.py e do jarvis_acoes.py.

    python iniciar_com_windows.py            -> ativa
    python iniciar_com_windows.py testar     -> inicia a tarefa agora
    python iniciar_com_windows.py status     -> mostra se está ativa
    python iniciar_com_windows.py remover    -> desativa

Cria a tarefa agendada "Jarvis" com dois gatilhos:
    1) ao entrar no Windows (depois de digitar a senha no boot)
    2) ao desbloquear o PC (depois de digitar a senha na tela de bloqueio)
Se o Jarvis já estiver aberto, não abre outro.
"""

import os
import subprocess
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(PASTA, "jarvis_hud.py")
NOME_TAREFA = "Jarvis"
ATALHO_ANTIGO = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
    "Programs", "Startup", "Jarvis.lnk",
)

SCRIPT_TAREFA = r'''
$ErrorActionPreference = 'Stop'
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$acao = New-ScheduledTaskAction -Execute $env:T_ALVO -Argument $env:T_ARGS -WorkingDirectory $env:T_PASTA
$logon = New-ScheduledTaskTrigger -AtLogOn -User $usuario
$cls = Get-CimClass -ClassName MSFT_TaskSessionStateChangeTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$unlock = New-CimInstance -CimClass $cls -ClientOnly -Property @{ StateChange = 8; UserId = $usuario }
$cfg = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
       -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $env:T_NOME -Action $acao -Trigger @($logon, $unlock) -Settings $cfg -Force | Out-Null
'''


def achar_pythonw() -> str:
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else exe


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")


def ativar():
    for arquivo in ("jarvis_hud.py", "jarvis_acoes.py"):
        if not os.path.exists(os.path.join(PASTA, arquivo)):
            print(f"ERRO: não achei o {arquivo} nesta pasta ({PASTA}).")
            return

    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", SCRIPT_TAREFA],
        env={**os.environ, "T_NOME": NOME_TAREFA, "T_ALVO": achar_pythonw(),
             "T_ARGS": f'"{SCRIPT}" --inicio-windows', "T_PASTA": PASTA},
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    if r.returncode != 0:
        print("Não consegui criar a tarefa:")
        print(r.stderr.strip() or r.stdout.strip())
        print("Tente abrir o terminal como administrador e rodar de novo.")
        return

    # remove o atalho antigo da pasta Inicializar para não abrir duas vezes
    if os.path.exists(ATALHO_ANTIGO):
        os.remove(ATALHO_ANTIGO)

    print("Pronto! O Jarvis vai abrir ao entrar no Windows e toda vez que você desbloquear o PC.")
    print("Testar agora:  python iniciar_com_windows.py testar")


def testar():
    r = _schtasks("/run", "/tn", NOME_TAREFA)
    print(r.stdout.strip() or r.stderr.strip())


def status():
    r = _schtasks("/query", "/tn", NOME_TAREFA)
    if r.returncode == 0:
        print("ATIVO. Tarefa agendada 'Jarvis' encontrada.")
    else:
        print("DESATIVADO. Para ativar: python iniciar_com_windows.py")


def remover():
    r = _schtasks("/delete", "/tn", NOME_TAREFA, "/f")
    if os.path.exists(ATALHO_ANTIGO):
        os.remove(ATALHO_ANTIGO)
    print("Pronto. O Jarvis não vai mais abrir sozinho." if r.returncode == 0
          else "A tarefa já não existia.")


if __name__ == "__main__":
    comando = sys.argv[1].lower() if len(sys.argv) > 1 else "ativar"
    acoes = {"ativar": ativar, "testar": testar, "status": status, "remover": remover}
    if comando in acoes:
        acoes[comando]()
    else:
        print("Use: ativar, testar, status ou remover.")