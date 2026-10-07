# -*- coding: utf-8 -*-
"""
Jarvis - CONFERIDOR. Só LÊ e testa: não altera nenhum arquivo seu (só cria o relatório).

Coloque na MESMA pasta do jarvis_hud.py e rode:
    python jarvis_conferir.py

Ele confere: arquivos, erros de sintaxe, pacotes, quais patches foram aplicados, variáveis do Windows,
o script da agenda do Google, o link .ics, o Ollama, a internet, os arquivos de dados (lembretes,
aniversários, memória) e testa a lógica de alarmes, datas e cálculos.

No fim ele salva o jarvis_conferir_relatorio.txt (sem senhas) para você colar aqui se algo falhar.
"""

import ast
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)
CONTAGEM = {"OK": 0, "AVISO": 0, "ERRO": 0}
LINHAS = []
PROBLEMAS = []


def log(tipo, msg, dica=""):
    CONTAGEM[tipo] += 1
    linha = f"[{tipo:5}] {msg}"
    LINHAS.append(linha)
    print(linha)
    if dica and tipo != "OK":
        for parte in dica.split("\n"):
            LINHAS.append("         -> " + parte)
            print("         -> " + parte)
        PROBLEMAS.append((tipo, msg))


def titulo(texto):
    LINHAS.append("")
    LINHAS.append(f"=== {texto} ===")
    print(f"\n=== {texto} ===")


def ler(nome):
    try:
        with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def variavel(nome):
    valor = os.environ.get(nome, "").strip()
    if not valor:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                valor = str(winreg.QueryValueEx(k, nome)[0]).strip()
        except Exception:
            valor = ""
    return valor


def baixar(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JarvisConferir"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ------------------------------------------------------------------ 1. arquivos e sintaxe
ARQUIVOS = [
    ("jarvis_hud.py", True), ("jarvis_acoes.py", True), ("jarvis_rotina.py", True),
    ("jarvis_extras.py", True), ("jarvis_memoria.py", True),
    ("jarvis_voz_id.py", False), ("jarvis_mensagens.py", False),
]


def conferir_arquivos():
    titulo("1. Arquivos e erros de sintaxe")
    for nome, obrigatorio in ARQUIVOS:
        codigo = ler(nome)
        if codigo is None:
            if obrigatorio:
                log("ERRO", f"{nome}: não encontrado nesta pasta", "Coloque o arquivo na pasta do Jarvis.")
            else:
                log("AVISO", f"{nome}: não encontrado (opcional)")
            continue
        try:
            ast.parse(codigo)
            log("OK", f"{nome}: presente e sem erro de sintaxe ({codigo.count(chr(10)) + 1} linhas)")
        except SyntaxError as erro:
            log("ERRO", f"{nome}: erro de sintaxe na linha {erro.lineno}: {erro.msg}",
                "Restaure a cópia de segurança (.bak) mais recente deste arquivo.")
    for nome in ("back_in_black.mp3",):
        if os.path.exists(os.path.join(PASTA, nome)):
            log("OK", f"{nome}: presente")
        else:
            log("AVISO", f"{nome}: não encontrado", "Sem ele a música de abertura não toca (o resto funciona).")


# ------------------------------------------------------------------ 2. pacotes
PACOTES = [
    ("sounddevice", "sounddevice", True), ("speech_recognition", "SpeechRecognition", True),
    ("edge_tts", "edge-tts", False), ("icalendar", "icalendar", False),
    ("recurring_ical_events", "recurring-ical-events", False), ("PIL", "pillow", False),
    ("google.genai", "google-genai", False),
]


def conferir_pacotes():
    titulo("2. Pacotes do Python")
    for modulo, pip, obrigatorio in PACOTES:
        try:
            achou = importlib.util.find_spec(modulo) is not None
        except (ImportError, ValueError):
            achou = False
        if achou:
            log("OK", f"{modulo}")
        else:
            log("ERRO" if obrigatorio else "AVISO", f"{modulo}: não instalado",
                f"Rode: pip install {pip}")


# ------------------------------------------------------------------ 3. patches aplicados
def conferir_patches():
    titulo("3. Patches aplicados")
    extras, memoria, hud = ler("jarvis_extras.py"), ler("jarvis_memoria.py"), ler("jarvis_hud.py")
    testes = [
        (extras, "jarvis_extras.py", "def _cmd_interprete", "modo intérprete (tradução em tempo real)",
         "adicionar_interprete.py"),
        (extras, "jarvis_extras.py", "_NA_FILA", "persistência de alarmes e lembretes", "aplicar_persistencia.py"),
        (extras, "jarvis_extras.py", "def _cmd_data", "aniversários todo ano", "aplicar_aniversarios.py"),
        (memoria, "jarvis_memoria.py", "os.replace", "gravação segura da memória", "aplicar_persistencia.py"),
        (memoria, "jarvis_memoria.py", "_agenda_falada", "'o que você lembra' lista a agenda",
         "aplicar_memoria_agenda.py"),
        (memoria, "jarvis_memoria.py", "comando_data", "gancho dos aniversários na memória",
         "aplicar_aniversarios.py"),
        (hud, "jarvis_hud.py", "jarvis_memoria", "memória ligada ao HUD", "aplicar_memoria.py"),
        (hud, "jarvis_hud.py", "jarvis_extras", "extras ligados ao HUD", "aplicar_extras.py"),
    ]
    for codigo, arquivo, marca, descricao, script in testes:
        if codigo is None:
            continue
        if marca in codigo:
            log("OK", f"{descricao}")
        else:
            log("AVISO", f"{descricao}: NÃO aplicado em {arquivo}", f"Rode: python {script}")
    if extras and "_NA_FILA" not in extras and "def _cmd_data" in extras:
        log("ERRO", "aniversários aplicados sem a persistência", "Restaure o .bak10 e rode antes o aplicar_persistencia.py.")


# ------------------------------------------------------------------ 4. variáveis
def conferir_variaveis():
    titulo("4. Variáveis do Windows (setx)")
    usadas = set()
    for nome, _ in ARQUIVOS:
        codigo = ler(nome) or ""
        usadas |= set(re.findall(r"""["']((?:JARVIS|GEMINI)_[A-Z0-9_]+|GOOGLE_API_KEY|OLLAMA_URL)["']""", codigo))
    if not usadas:
        log("AVISO", "não achei nenhuma variável usada pelo código")
    for nome in sorted(usadas):
        valor = variavel(nome)
        if valor:
            log("OK", f"{nome}: definida ({len(valor)} caracteres)")
        else:
            log("AVISO", f"{nome}: não definida",
                'Se você usa esse recurso, rode: setx ' + nome + ' "valor"  (e reinicie o Jarvis/terminal).')


# ------------------------------------------------------------------ 5. agenda do Google
def conferir_script_agenda():
    titulo("5. Script da agenda do Google (criar / listar / cancelar / mudar)")
    base, senha = variavel("JARVIS_AGENDA_SCRIPT"), variavel("JARVIS_AGENDA_SENHA")
    if not base and not senha:
        log("AVISO", "script da agenda não configurado", "Sem ele o Jarvis só abre o Google Agenda para você salvar.")
        return
    if not base or not senha:
        log("ERRO", "falta uma das duas: JARVIS_AGENDA_SCRIPT ou JARVIS_AGENDA_SENHA",
            "As duas precisam estar definidas com setx.")
        return
    if "/exec" not in base:
        log("ERRO", "JARVIS_AGENDA_SCRIPT não termina em /exec", "Use o endereço do 'Aplicativo da Web' (…/exec).")
    hoje = date.today().isoformat()
    url = base + ("&" if "?" in base else "?") + urlencode({"token": senha, "acao": "listar", "de": hoje, "ate": hoje})
    try:
        corpo = baixar(url, timeout=40)
    except urllib.error.HTTPError as erro:
        log("ERRO", f"o script respondeu HTTP {erro.code}",
            "Confira se a implantação está como 'Qualquer pessoa' e se o endereço é o /exec.")
        return
    except Exception as erro:
        log("ERRO", f"não consegui falar com o script: {erro}", "Sem internet, ou endereço errado.")
        return
    try:
        dados = json.loads(corpo)
    except ValueError:
        if "<html" in corpo.lower():
            log("ERRO", "o endereço devolveu uma página da web, não o script",
                "Publique de novo como 'Aplicativo da Web' com acesso 'Qualquer pessoa' e copie o novo /exec.")
        else:
            log("ERRO", "resposta do script não é JSON")
        return
    if dados.get("ok") and isinstance(dados.get("eventos"), list):
        log("OK", f"script da agenda funcionando: {len(dados['eventos'])} compromisso(s) hoje")
        return
    erro = str(dados.get("erro"))
    if "data inválida" in erro or "desconhecida" in erro:
        log("ERRO", f"o script publicado é a versão ANTIGA ({erro})",
            "Cole o jarvis_agenda_script.gs novo no Apps Script e publique uma NOVA VERSÃO\n"
            "(Implantar > Gerenciar implantações > lápis > Versão: Nova versão > Implantar).")
    elif "senha" in erro.lower():
        log("ERRO", "senha incorreta", "A SENHA dentro do script tem que ser igual à do JARVIS_AGENDA_SENHA.")
    else:
        log("ERRO", f"o script recusou: {erro}")


def conferir_ics():
    titulo("6. Agenda pelo link .ics (resumo, compromissos de hoje, aviso 10 min antes)")
    link = variavel("JARVIS_AGENDA")
    if not link:
        log("AVISO", "JARVIS_AGENDA não definida", "Sem ela, 'resumo da semana' e o aviso automático não leem a agenda.")
        return
    if link.startswith("webcal://"):
        link = "https://" + link[len("webcal://"):]
    try:
        corpo = baixar(link, timeout=25)
    except Exception as erro:
        log("ERRO", f"não consegui baixar o link .ics: {erro}",
            "Copie de novo o 'Endereço secreto no formato iCal' do Google Agenda.")
        return
    if "BEGIN:VCALENDAR" not in corpo:
        log("ERRO", "o link não devolveu uma agenda .ics", "Use o 'Endereço secreto no formato iCal', não o público.")
        return
    log("OK", f"link .ics funcionando ({corpo.count('BEGIN:VEVENT')} eventos no arquivo)")
    try:
        import icalendar
        import recurring_ical_events
        cal = icalendar.Calendar.from_ical(corpo.encode("utf-8"))
        n = len(list(recurring_ical_events.of(cal).at(date.today())))
        log("OK", f"{n} compromisso(s) hoje pela agenda .ics")
    except ImportError:
        log("AVISO", "icalendar/recurring-ical-events não instalados", "pip install icalendar recurring-ical-events")
    except Exception as erro:
        log("ERRO", f"não consegui ler os eventos do .ics: {erro}")


# ------------------------------------------------------------------ 7. Ollama e internet
def conferir_ollama():
    titulo("7. Ollama (a IA local do jarvis_acoes.py)")
    url = (variavel("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
    modelo = variavel("JARVIS_MODELO") or "qwen2.5:7b"
    try:
        dados = json.loads(baixar(url + "/api/tags", timeout=6))
    except Exception:
        log("AVISO", f"Ollama não respondeu em {url}",
            "Abra o Ollama. Se o seu HUD usa só o Gemini, pode ignorar este aviso.")
        return
    nomes = [m.get("name", "") for m in dados.get("models", [])]
    if modelo in nomes or modelo + ":latest" in nomes:
        log("OK", f"Ollama no ar e o modelo {modelo} está instalado")
    else:
        log("ERRO", f"Ollama no ar, mas o modelo {modelo} não está instalado", f"Rode: ollama pull {modelo}")


SERVICOS = [
    ("previsão do tempo (Open-Meteo)", "https://api.open-meteo.com/v1/forecast?latitude=-25.5&longitude=-54.6&current=temperature_2m"),
    ("cotações (AwesomeAPI)", "https://economia.awesomeapi.com.br/json/last/USD-BRL"),
    ("notícias (feed do G1)", "https://g1.globo.com/rss/g1/"),
    ("Wikipedia (pesquisa)", "https://pt.wikipedia.org/api/rest_v1/page/summary/Brasil"),
    ("tradutor (MyMemory)", "https://api.mymemory.translated.net/get?q=oi&langpair=pt|en"),
    ("esportes (TheSportsDB)", "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t=Flamengo"),
]


def conferir_internet():
    titulo("8. Serviços de internet")
    for nome, url in SERVICOS:
        try:
            corpo = baixar(url, timeout=12)
            log("OK" if corpo else "AVISO", nome)
        except Exception as erro:
            log("AVISO", f"{nome}: falhou ({str(erro)[:70]})", "Pode ser instabilidade; tente de novo mais tarde.")


# ------------------------------------------------------------------ 9. arquivos de dados
def conferir_dados():
    titulo("9. Arquivos de dados")
    agora = datetime.now()
    for nome in ("jarvis_lembretes.json", "jarvis_datas.json", "jarvis_memoria.json", "jarvis_alertas.json",
                 "jarvis_config.json", "jarvis_protocolos.json", os.path.join("jarvis_dados", "alarmes.json")):
        caminho = os.path.join(PASTA, nome)
        if not os.path.exists(caminho):
            log("OK", f"{nome}: ainda não existe (normal se você nunca usou)")
            continue
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
        except ValueError as erro:
            log("ERRO", f"{nome}: JSON corrompido ({erro})", "Apague o arquivo ou restaure uma cópia; o Jarvis cria outro.")
            continue
        extra = ""
        if nome == "jarvis_lembretes.json" and isinstance(dados, list):
            pend = [x for x in dados if not x.get("ok")]
            atrasados = 0
            for x in pend:
                try:
                    if datetime.fromisoformat(x["quando"]) < agora:
                        atrasados += 1
                except (KeyError, ValueError):
                    pass
            extra = f" ({len(pend)} pendente(s), {atrasados} já vencido(s) esperando para falar)"
        elif isinstance(dados, list):
            extra = f" ({len(dados)} item(ns))"
        log("OK", f"{nome}: válido{extra}")
    for pasta_tmp in (".tmp",):
        sobras = [n for n in os.listdir(PASTA) if n.endswith(pasta_tmp)]
        if sobras:
            log("AVISO", f"sobras de gravação interrompida: {', '.join(sobras)}", "Pode apagar esses arquivos .tmp.")


# ------------------------------------------------------------------ 10. testes de lógica
def carregar(nome):
    caminho = os.path.join(PASTA, nome + ".py")
    if not os.path.exists(caminho):
        return None
    try:
        spec = importlib.util.spec_from_file_location(nome, caminho)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo
        spec.loader.exec_module(modulo)
        return modulo
    except Exception as erro:
        log("ERRO", f"{nome}.py não carrega: {type(erro).__name__}: {erro}",
            "Esse erro também impede o Jarvis de abrir. Me mande esta linha.")
        return None


def teste(descricao, funcao):
    try:
        ok, detalhe = funcao()
    except Exception as erro:
        log("ERRO", f"teste '{descricao}' quebrou: {type(erro).__name__}: {erro}")
        return
    if ok:
        log("OK", f"teste: {descricao}")
    else:
        log("ERRO", f"teste '{descricao}' falhou: {detalhe}")


def conferir_logica():
    titulo("10. Testes da lógica (alarmes, datas, cálculo)")
    EX = carregar("jarvis_extras")
    if EX is not None:
        base = datetime(2026, 10, 4, 8, 0)

        def t_relativo():
            q, _ = EX.interpretar_quando("me avisa daqui a 20 minutos", base)
            return q == base + timedelta(minutes=20), q

        def t_hoje_12():
            q, _ = EX.interpretar_quando("alarme hoje às 12:00", base)
            return q == datetime(2026, 10, 4, 12, 0), q

        def t_amanha():
            q, _ = EX.interpretar_quando("marca dentista amanhã às 10h", base)
            return q == datetime(2026, 10, 5, 10, 0), q

        def t_nome_alarme():
            q, spans = EX.interpretar_quando("coloca um alarme para hoje às 12", base)
            msg = EX.extrair_mensagem("coloca um alarme para hoje às 12", spans)
            return msg.lower() not in ("para", "s"), repr(msg)

        def t_conta():
            expr = EX.expressao_da_fala("quanto e 15% de 280")
            return expr is not None and abs(EX.avaliar(expr) - 42) < 1e-9, expr

        def t_duracao():
            return EX.parse_duracao("1 hora e 30 minutos") == 5400, EX.parse_duracao("1 hora e 30 minutos")

        for nome_t, fn in (("lembrete 'daqui a 20 minutos'", t_relativo), ("'alarme hoje às 12:00'", t_hoje_12),
                           ("'marca dentista amanhã às 10h'", t_amanha), ("nome do alarme não vira 'para' (correção do aplicar_persistencia.py)", t_nome_alarme),
                           ("'quanto é 15% de 280' = 42", t_conta), ("duração '1 hora e 30 minutos'", t_duracao)):
            teste(nome_t, fn)
        if hasattr(EX, "_parse_data_especial"):
            teste("aniversário 'dia 10 de maio'",
                  lambda: ((lambda r: (r is not None and r[:2] == (10, 5), r))(
                      EX._parse_data_especial("meu aniversario e dia 10 de maio"))))
        if hasattr(EX, "_fala_lembrete"):
            def t_atraso():
                q = datetime.now() - timedelta(minutes=35)
                fala = EX._fala_lembrete({"texto": "pão", "tipo": "alarme"}, q)()
                return "atrasado" in fala, fala
            teste("alarme vencido avisa que o senhor está atrasado", t_atraso)
    ME = carregar("jarvis_memoria")
    if ME is not None and hasattr(ME, "_alinhado"):
        teste("memória entende acentos",
              lambda: (ME._alinhado("Aniversário") == "aniversario", ME._alinhado("Aniversário")))


# ------------------------------------------------------------------ principal
def main():
    print("Jarvis - conferidor. Isto só lê e testa; nada é alterado.")
    print(f"Pasta: {PASTA}")
    print(f"Python {sys.version.split()[0]} em {sys.platform}")
    if sys.platform != "win32":
        log("AVISO", "você não está no Windows: a leitura das variáveis (setx) e algumas partes não funcionam aqui")
    for etapa in (conferir_arquivos, conferir_pacotes, conferir_patches, conferir_variaveis,
                  conferir_script_agenda, conferir_ics, conferir_ollama, conferir_internet,
                  conferir_dados, conferir_logica):
        try:
            etapa()
        except Exception as erro:
            log("ERRO", f"a etapa {etapa.__name__} quebrou: {type(erro).__name__}: {erro}")

    titulo("RESUMO")
    print(f"OK: {CONTAGEM['OK']}   AVISOS: {CONTAGEM['AVISO']}   ERROS: {CONTAGEM['ERRO']}")
    LINHAS.append(f"OK: {CONTAGEM['OK']}   AVISOS: {CONTAGEM['AVISO']}   ERROS: {CONTAGEM['ERRO']}")
    if CONTAGEM["ERRO"]:
        print("\nPrecisam de atenção (ERROS):")
        for tipo, msg in PROBLEMAS:
            if tipo == "ERRO":
                print("  -", msg)
    elif CONTAGEM["AVISO"]:
        print("\nSem erros. Os avisos são recursos opcionais ou patches que você ainda não aplicou.")
    else:
        print("\nTudo certo!")
    try:
        with open(os.path.join(PASTA, "jarvis_conferir_relatorio.txt"), "w", encoding="utf-8") as f:
            f.write(f"Relatório de {datetime.now():%d/%m/%Y %H:%M}\n" + "\n".join(LINHAS) + "\n")
        print("\nRelatório salvo em jarvis_conferir_relatorio.txt (sem senhas). Se algo falhar, cole-o aqui.")
    except OSError:
        pass


if __name__ == "__main__":
    main()