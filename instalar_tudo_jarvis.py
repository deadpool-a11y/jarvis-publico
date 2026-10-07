# -*- coding: utf-8 -*-
"""
Instala TODAS as correções do Jarvis na ordem certa, de uma vez.
Coloque esta pasta de scripts junto do jarvis_acoes.py e rode:
    python instalar_tudo_jarvis.py
O que já estiver instalado é pulado. Cada etapa faz sua própria cópia de segurança (.bak).
"""
import importlib.util
import os

PASTA = os.path.dirname(os.path.abspath(__file__))
ORDEM = [
    "corrigir_jarvis",                # volta a escutar logo depois do alarme/timer
    "aviso_atraso_jarvis",            # avisa quando está atrasado
    "aviso_atraso_ok_jarvis",         # "Jarvis, ok" / cancelar o aviso
    "aviso_atraso_ok_v2_jarvis",      # cancelar/ok dispensa os compromissos de agora
    "cancelar_local_v2_jarvis",       # "cancela o compromisso" (com ou sem nome)
    "verificar_cancelamento_jarvis",  # confere se sumiu da agenda do Google
    "cancelar_aviso_atual_jarvis",    # "cancelar compromisso" = o que ele acabou de avisar
]

for nome in ORDEM:
    caminho = os.path.join(PASTA, nome + ".py")
    print(f"\n== {nome} ==")
    if not os.path.exists(caminho):
        print(f"Arquivo {nome}.py não está na pasta. Coloque todos os scripts juntos e rode de novo.")
        break
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    try:
        modulo.main()
    except SystemExit as erro:
        if erro.code not in (None, 0):
            print(erro.code)
            print("Parei aqui para não bagunçar seu arquivo. Me mande esta mensagem.")
            break
else:
    print("\nTudo certo! Feche o Jarvis e abra de novo.")
