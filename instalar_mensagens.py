"""
Instalador automático: coloca o jarvis_mensagens no seu jarvis_acoes.py.

Como usar (na pasta do Jarvis, onde estão jarvis_acoes.py e jarvis_mensagens.py):
    pip install playwright
    python instalar_mensagens.py

Ele faz uma cópia de segurança (jarvis_acoes.py.bak) e pode ser rodado de novo sem estragar nada.
"""
import os
import shutil
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
ALVO = os.path.join(PASTA, "jarvis_acoes.py")

LINHAS_SISTEMA = '''{"tipo": "ler_mensagens", "valor": "todas" | "whatsapp" | "discord" | "gmail" | "instagram"}
{"tipo": "ligar_whatsapp", "nome": "Maria", "video": false}
{"tipo": "atender_chamada"}
{"tipo": "recusar_chamada"}
{"tipo": "encerrar_chamada"}
'''

BLOCO_LOCAL = '''    try:
        import jarvis_mensagens
        local = jarvis_mensagens.comando_local(t, texto)
        if local:
            return local
    except Exception as erro:
        print(f"[erro] mensagens: {erro}")

'''

BLOCO_FINAL = '''# Mensagens (WhatsApp, Gmail, Discord, Instagram) e chamadas
try:
    import jarvis_mensagens
    ACOES_NOVAS.update(jarvis_mensagens.ACOES)
    jarvis_mensagens.iniciar()
except Exception as erro:
    print(f"(Mensagens do Jarvis desativadas: {erro})")

'''

ANCORA_SISTEMA = "Aplicativos instalados (use o nome exato em abrir_app): {APPS}"
ANCORA_LOCAL = "    # --- boa noite / modo foco ---"
ANCORA_FINAL = "# Liga timers, alarmes e lembretes assim que o arquivo é carregado"


def main():
    if not os.path.exists(ALVO):
        sys.exit("Não achei o jarvis_acoes.py nesta pasta. Coloque este arquivo ao lado dele.")
    if not os.path.exists(os.path.join(PASTA, "jarvis_mensagens.py")):
        sys.exit("Falta o jarvis_mensagens.py nesta pasta.")
    with open(ALVO, "rb") as f:
        texto = f.read().decode("utf-8")
    if "jarvis_mensagens" in texto:
        print("O jarvis_acoes.py já está com as mensagens instaladas. Nada a fazer.")
        return
    nl = "\r\n" if "\r\n" in texto else "\n"
    texto = texto.replace("\r\n", "\n")
    for ancora in (ANCORA_SISTEMA, ANCORA_LOCAL, ANCORA_FINAL):
        if texto.count(ancora) != 1:
            sys.exit(f"Não consegui achar o ponto certo no arquivo (versão diferente?): {ancora!r}")
    texto = texto.replace(ANCORA_SISTEMA, LINHAS_SISTEMA + "\n" + ANCORA_SISTEMA)
    texto = texto.replace(ANCORA_LOCAL, BLOCO_LOCAL + ANCORA_LOCAL)
    texto = texto.replace(ANCORA_FINAL, BLOCO_FINAL + ANCORA_FINAL)
    shutil.copy2(ALVO, ALVO + ".bak")
    with open(ALVO, "wb") as f:
        f.write(texto.replace("\n", nl).encode("utf-8"))
    print("Pronto! jarvis_acoes.py atualizado (cópia de segurança em jarvis_acoes.py.bak).")
    print("Agora feche e abra o Jarvis de novo.")


if __name__ == "__main__":
    main()