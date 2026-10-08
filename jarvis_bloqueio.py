"""
Trava de seguranca do Jarvis: nao deixa ele continuar enquanto o Windows
estiver bloqueado (tela de senha). Quando voce desbloquear, ele segue.
"""
import ctypes
import sys
import time


def pc_bloqueado() -> bool:
    """True se a tela estiver bloqueada (ou sem sessao interativa)."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desktop:
            return True
        try:
            return not bool(user32.SwitchDesktop(desktop))
        finally:
            user32.CloseDesktop(desktop)
    except Exception:
        return False  # se a checagem falhar, nao trava o Jarvis


def esperar_desbloqueio(intervalo: float = 2.0) -> None:
    """Fica parado ate o Windows ser desbloqueado."""
    while pc_bloqueado():
        time.sleep(intervalo)
