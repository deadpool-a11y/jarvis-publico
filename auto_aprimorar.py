"""
auto_aprimorar.py - Auto aprimoramento do Jarvis (versão com GitHub + comando de voz).

Como funciona:
  1. Pede ao Ollama (local) UM comando novo por vez, escrito como "plugin",
     sorteando um tema para não repetir sempre a mesma ideia.
  2. Valida o plugin SEM executá-lo (AST rigorosa) e depois o testa de verdade
     (importa e chama executar()) num processo separado, com tempo limite,
     sem rede e sem abrir o navegador.
  3. Se falhar, devolve o erro ao Ollama e dá uma segunda chance de corrigir.
  4. Se passou, guarda em melhorias/ e registra em melhorias.json.
     Se falhou de vez, vai para melhorias/quarentena/ (o código principal nunca é alterado).
  5. Tudo que foi aprimorado é enviado ao GitHub (commit + push).
  6. Quando você desbloquear o PC, resumo_para_falar() devolve a frase
     "Senhor, eu aprimorei hoje: ... (diga "...")".

Comando de voz:
    "ativar aprimoramento"    -> o Jarvis oferece: aprimorar só hoje ou todo dia
    "aprimora só hoje"        -> roda agora e só hoje
    "aprimora todo dia"       -> roda agora e passa a rodar todo dia (tarefa agendada)
    "desativar aprimoramento" -> para tudo

Uso por linha de comando (tarefa agendada):
    pythonw auto_aprimorar.py                -> 1 ciclo por dia, se o modo permitir
    python  auto_aprimorar.py --forcar       -> roda agora, ignorando o modo
    python  auto_aprimorar.py --listar       -> mostra o histórico de melhorias
    python  auto_aprimorar.py --desativar melhoria_XXXX.py   -> manda uma melhoria para a quarentena

Para o Jarvis USAR as melhorias:
    from auto_aprimorar import responder_comando
    resposta = responder_comando(texto_falado)     # None se nada for ativado
IMPORTANTE: passe TODAS as frases faladas para responder_comando, senão o Jarvis
não ouve sua resposta "só hoje" / "todo dia".
Mensagens e erros ficam em auto_aprimorar.log (com pythonw não há console).
"""
import argparse
import ast
import datetime
import difflib
import importlib.util
import json
import logging
import logging.handlers
import os
import random
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid

# ----------------------------- CONFIGURAÇÃO -----------------------------
OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO = "qwen2.5:7b"            # troque pelo modelo que você usa no Ollama
MELHORIAS_POR_CICLO = 2      # quantas melhorias tentar por execução
TENTATIVAS_POR_MELHORIA = 2  # 1ª tentativa + correções guiadas pelo erro
TIMEOUT_OLLAMA = 300         # segundos
TIMEOUT_TESTE = 15           # segundos para o teste de cada plugin
TIMEOUT_EXECUCAO = 10        # segundos máximos de um plugin respondendo a um comando
TAMANHO_MAX_CODIGO = 6000    # caracteres
DIAS_QUARENTENA = 30         # arquivos mais velhos que isso são apagados da quarentena
TRAVA_MAX_IDADE = 3600       # segundos até considerar uma trava esquecida

GIT_ATIVO = True             # enviar melhorias para o GitHub
TIMEOUT_GIT = 60             # segundos para cada comando git
MODO_PADRAO = "desligado"    # "desligado", "hoje" ou "todo_dia" (use "todo_dia" p/ comportamento antigo)

PASTA = os.path.dirname(os.path.abspath(__file__))
PASTA_PLUGINS = os.path.join(PASTA, "melhorias")
PASTA_QUARENTENA = os.path.join(PASTA_PLUGINS, "quarentena")
ARQUIVO_LOG = os.path.join(PASTA, "melhorias.json")
ARQUIVO_TEXTO_LOG = os.path.join(PASTA, "auto_aprimorar.log")
ARQUIVO_TRAVA = os.path.join(PASTA, ".auto_aprimorar.lock")
ARQUIVO_CONFIG = os.path.join(PASTA, "config_aprimorar.json")

TEMAS = [
    "tempo e datas (contagem regressiva, dia da semana, dias até um evento)",
    "matemática do dia a dia (porcentagem, gorjeta, conversão de unidades)",
    "produtividade (técnica pomodoro, lembrete de pausa, dica de foco)",
    "curiosidades e frases motivacionais",
    "jogos simples (cara ou coroa, dado, sorteio, pedra papel tesoura)",
    "informações do sistema (data, hora, sistema operacional)",
    "saúde e bem-estar (lembrete de beber água, alongamento, postura)",
    "abrir sites úteis no navegador (clima, mapas, tradutor)",
    "texto (contar palavras de uma frase, inverter texto, soletrar)",
]

# Segurança: o que um plugin gerado pode usar
IMPORTS_OK = {
    "datetime", "time", "random", "math", "webbrowser", "json", "re",
    "platform", "calendar", "statistics", "urllib.request",
}
NOMES_PROIBIDOS = {
    "eval", "exec", "compile", "open", "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir", "type", "input", "breakpoint", "help",
    "exit", "quit", "memoryview", "super", "object",
}
ATRIBUTOS_PROIBIDOS = {
    "system", "popen", "remove", "unlink", "rmtree", "rmdir", "rename",
    "mro", "subclasses", "gi_frame", "f_globals", "f_locals",
}
NOS_PROIBIDOS = (
    ast.While, ast.ClassDef, ast.Global, ast.Nonlocal, ast.AsyncFunctionDef,
    ast.AsyncFor, ast.AsyncWith, ast.Await, ast.Yield, ast.YieldFrom,
)

os.makedirs(PASTA_PLUGINS, exist_ok=True)
os.makedirs(PASTA_QUARENTENA, exist_ok=True)


# ----------------------------- LOG DE TEXTO -----------------------------
def _criar_logger():
    logger = logging.getLogger("auto_aprimorar")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    arq = logging.handlers.RotatingFileHandler(ARQUIVO_TEXTO_LOG, maxBytes=200_000,
                                               backupCount=2, encoding="utf-8")
    arq.setFormatter(fmt)
    logger.addHandler(arq)
    if sys.stderr is not None:  # com pythonw não existe stderr
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        logger.addHandler(console)
    return logger


log = _criar_logger()


class ErroOllama(Exception):
    """Ollama desligado, modelo ausente, resposta inválida, etc."""


# ------------------------------- UTILIDADES -----------------------------
def _normalizar(txt):
    """minúsculas, sem acento, sem pontuação, espaços únicos."""
    txt = unicodedata.normalize("NFD", str(txt).lower())
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn")
    txt = re.sub(r"[^a-z0-9 ]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _python_console():
    """pythonw não tem stdout; para testes usamos o python.exe irmão."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(alt):
            return alt
    return exe


def _mover_quarentena(caminho, nome=None):
    nome = nome or os.path.basename(caminho)
    destino = os.path.join(PASTA_QUARENTENA, nome)
    if os.path.exists(destino):
        base, ext = os.path.splitext(nome)
        destino = os.path.join(PASTA_QUARENTENA, f"{base}_{uuid.uuid4().hex[:4]}{ext}")
    os.replace(caminho, destino)


def _limpar_quarentena():
    limite = time.time() - DIAS_QUARENTENA * 86400
    for arq in os.listdir(PASTA_QUARENTENA):
        caminho = os.path.join(PASTA_QUARENTENA, arq)
        try:
            if os.path.isfile(caminho) and os.path.getmtime(caminho) < limite:
                os.remove(caminho)
        except OSError:
            pass


# ------------------------------- LOG JSON -------------------------------
def _ler_log():
    try:
        with open(ARQUIVO_LOG, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        dados = {}
    dados.setdefault("ultima_execucao", "")
    dados.setdefault("melhorias", [])
    return dados


def _salvar_log(dados):
    """Gravação atômica: nunca deixa o JSON pela metade."""
    temporario = ARQUIVO_LOG + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temporario, ARQUIVO_LOG)


# --------------------------------- TRAVA --------------------------------
def _travar():
    """Evita dois ciclos ao mesmo tempo (tarefa agendada + execução manual)."""
    for _ in range(2):
        try:
            fd = os.open(ARQUIVO_TRAVA, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(ARQUIVO_TRAVA) > TRAVA_MAX_IDADE:
                    os.remove(ARQUIVO_TRAVA)  # trava esquecida de uma execução que morreu
                    continue
            except OSError:
                pass
            return False
    return False


def _destravar():
    try:
        os.remove(ARQUIVO_TRAVA)
    except OSError:
        pass


# ------------------------------- OLLAMA ---------------------------------
def _ollama(prompt):
    corpo = json.dumps({
        "model": MODELO, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.8, "num_predict": 1500},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=corpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_OLLAMA) as resp:
            return json.loads(resp.read().decode("utf-8"))["response"]
    except (OSError, KeyError, ValueError) as e:  # URLError e timeout são OSError
        raise ErroOllama(str(e)) from e


def _extrair_codigo(texto):
    """Pega o bloco ```python```; aceita também cerca que ficou sem fechar."""
    m = re.search(r"```(?:python|py)?[ \t]*\n(.*?)```", texto, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```(?:python|py)?[ \t]*\n(.*)$", texto, re.DOTALL)
    if m:
        return m.group(1).strip()
    return texto.strip()


# ----------------------------- VALIDAÇÃO --------------------------------
def _metadados(arvore):
    """Lê DESCRICAO e FRASES (literais) sem executar nada."""
    valores = {}
    for n in arvore.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in ("DESCRICAO", "FRASES"):
                    try:
                        valores[t.id] = ast.literal_eval(n.value)
                    except Exception:
                        raise ValueError(f"{t.id} precisa ser um valor literal")
    descricao, frases = valores.get("DESCRICAO"), valores.get("FRASES")
    if not isinstance(descricao, str) or not descricao.strip():
        raise ValueError("DESCRICAO deve ser um texto não vazio")
    if (not isinstance(frases, list) or not 1 <= len(frases) <= 6
            or not all(isinstance(f, str) for f in frases)):
        raise ValueError("FRASES deve ser uma lista de 1 a 6 textos")
    if any(len(_normalizar(f)) < 4 for f in frases):
        raise ValueError("cada frase de ativação precisa ter ao menos 4 letras")
    return descricao.strip(), [f.strip() for f in frases]


def _validar_ast(codigo):
    """Valida sem executar. Devolve (descricao, frases)."""
    if not codigo.strip():
        raise ValueError("código vazio")
    if len(codigo) > TAMANHO_MAX_CODIGO:
        raise ValueError("código grande demais")
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as e:
        raise ValueError(f"erro de sintaxe na linha {e.lineno}: {e.msg}")

    # No topo do módulo só pode: imports, funções, docstring e constantes literais.
    for no in arvore.body:
        if isinstance(no, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            continue
        if (isinstance(no, ast.Expr) and isinstance(no.value, ast.Constant)
                and isinstance(no.value.value, str)):
            continue
        if isinstance(no, ast.Assign):
            try:
                ast.literal_eval(no.value)
                continue
            except Exception:
                pass
        raise ValueError("há código solto no topo do módulo (só constantes, imports e funções)")

    for no in ast.walk(arvore):
        if isinstance(no, NOS_PROIBIDOS):
            raise ValueError(f"construção proibida: {type(no).__name__}")
        if isinstance(no, ast.Import):
            for a in no.names:
                if a.name not in IMPORTS_OK:
                    raise ValueError(f"import proibido: {a.name}")
        elif isinstance(no, ast.ImportFrom):
            if no.level or (no.module or "") not in IMPORTS_OK:
                raise ValueError(f"import proibido: {no.module}")
            if any(a.name == "*" for a in no.names):
                raise ValueError("import * proibido")
        elif isinstance(no, ast.Name):
            if no.id in NOMES_PROIBIDOS or no.id.startswith("__"):
                raise ValueError(f"uso proibido: {no.id}")
        elif isinstance(no, ast.Attribute):
            if no.attr in ATRIBUTOS_PROIBIDOS or no.attr.startswith("__"):
                raise ValueError(f"uso proibido: .{no.attr}")

    funcoes = {n.name: n for n in arvore.body if isinstance(n, ast.FunctionDef)}
    if "executar" not in funcoes:
        raise ValueError("falta a função executar(texto)")
    if not funcoes["executar"].args.args:
        raise ValueError("executar precisa receber o parâmetro texto")
    return _metadados(arvore)


_TESTE = r'''
import importlib.util, sys, urllib.request, webbrowser

def _sem_rede(*a, **k):
    raise OSError("rede bloqueada no teste")

webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = lambda *a, **k: True
urllib.request.urlopen = _sem_rede

s = importlib.util.spec_from_file_location("plugin_teste", sys.argv[1])
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
assert isinstance(m.DESCRICAO, str) and m.DESCRICAO.strip(), "DESCRICAO invalida"
assert isinstance(m.FRASES, list) and m.FRASES, "FRASES invalidas"
for entrada in (m.FRASES[0], "", "teste 123"):
    r = m.executar(entrada)
    assert isinstance(r, str) and r.strip(), "executar deve devolver um texto nao vazio"
'''


def _testar_plugin(caminho):
    """Importa e CHAMA executar() num processo isolado (sem rede/navegador, com tempo limite)."""
    try:
        r = subprocess.run(
            [_python_console(), "-I", "-c", _TESTE, caminho],
            capture_output=True, text=True, timeout=TIMEOUT_TESTE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise ValueError("o teste demorou demais (possível laço infinito)")
    if r.returncode != 0:
        ultima = (r.stderr.strip().splitlines() or ["sem detalhe"])[-1]
        raise ValueError("falhou no teste: " + ultima[:200])


# ------------------------------ CATÁLOGO --------------------------------
def _catalogo():
    """Melhorias já instaladas, lidas do disco sem executar nada."""
    itens = []
    for arq in sorted(os.listdir(PASTA_PLUGINS)):
        if not arq.endswith(".py"):
            continue
        try:
            with open(os.path.join(PASTA_PLUGINS, arq), encoding="utf-8") as f:
                descricao, frases = _metadados(ast.parse(f.read()))
        except Exception:
            continue
        itens.append({"arquivo": arq, "descricao": descricao,
                      "frases": {_normalizar(x) for x in frases}})
    return itens


def _checar_duplicidade(descricao, frases, catalogo):
    novas = {_normalizar(f) for f in frases}
    for c in catalogo:
        for a in novas:
            for b in c["frases"]:
                if a == b or f" {a} " in f" {b} " or f" {b} " in f" {a} ":
                    raise ValueError("frase de ativação conflita com outra melhoria")
        if difflib.SequenceMatcher(None, _normalizar(descricao),
                                   _normalizar(c["descricao"])).ratio() > 0.8:
            raise ValueError("descrição muito parecida com uma melhoria existente")


# --------------------------- GERAR MELHORIA -----------------------------
def _prompt(existentes, tema, erro=None, codigo_anterior=""):
    lista = "\n".join(f"- {d}" for d in existentes) or "- (nenhum ainda)"
    base = f"""Você está ajudando a melhorar o Jarvis, um assistente de voz em Python no Windows.
Crie UM comando de voz novo e útil, escrito como módulo Python independente.
Tema desta vez: {tema}

Comandos que já existem (NÃO repita nenhum):
{lista}

Regras obrigatórias do módulo:
- Defina DESCRICAO = "frase curta em português dizendo o que o comando faz"
- Defina FRASES = ["frase que ativa 1", "frase que ativa 2"]  (minúsculas, sem acento, ao menos 4 letras cada)
- Defina def executar(texto): que retorna uma STRING em português para o Jarvis falar.
- executar deve funcionar mesmo com texto vazio e nunca deixar um erro escapar.
- Se usar internet (urllib.request) ou o navegador, trate erros com try/except e devolva uma mensagem.
- No topo do módulo só pode haver imports, constantes simples e funções (nada executado solto).
- Use SOMENTE estes imports: {", ".join(sorted(IMPORTS_OK))}
- Não use open, eval, exec, os, subprocess, while, class, nem nomes começando com __.
- Não apague/renomeie arquivos e não peça entrada ao usuário (sem input()).
Responda APENAS com o código Python dentro de um bloco ```python."""
    if erro:
        base += (f"\n\nSua versão anterior foi REJEITADA com este erro: {erro}\n"
                 f"Código anterior:\n```python\n{codigo_anterior}\n```\n"
                 "Corrija o problema e responda de novo com o código completo.")
    return base


def _gerar_melhoria(catalogo):
    """Tenta criar uma melhoria (com correção guiada). Levanta ErroOllama se o Ollama falhar."""
    agora = datetime.datetime.now()
    nome = f"melhoria_{agora:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}.py"
    temporario = os.path.join(PASTA_QUARENTENA, "tmp_" + nome)
    registro = {"data": agora.strftime("%Y-%m-%d %H:%M"), "arquivo": nome,
                "descricao": "(sem descrição)", "frases": [], "status": "descartada",
                "motivo": "", "tentativas": 0, "anunciada": False}
    tema = random.choice(TEMAS)
    existentes = [c["descricao"] for c in catalogo]
    erro, codigo = None, ""

    for tentativa in range(1, TENTATIVAS_POR_MELHORIA + 1):
        registro["tentativas"] = tentativa
        codigo = _extrair_codigo(_ollama(_prompt(existentes, tema, erro, codigo)))
        try:
            with open(temporario, "w", encoding="utf-8") as f:
                f.write(codigo)
            descricao, frases = _validar_ast(codigo)
            registro["descricao"] = descricao
            _checar_duplicidade(descricao, frases, catalogo)
            _testar_plugin(temporario)

            os.replace(temporario, os.path.join(PASTA_PLUGINS, nome))
            registro.update(status="aplicada", frases=frases, motivo="")
            catalogo.append({"arquivo": nome, "descricao": descricao,
                             "frases": {_normalizar(x) for x in frases}})
            log.info("melhoria aplicada (tentativa %d): %s", tentativa, descricao)
            return registro
        except Exception as e:
            erro = str(e)[:300]
            registro["motivo"] = erro
            log.warning("tentativa %d rejeitada: %s", tentativa, erro)

    if os.path.exists(temporario):
        _mover_quarentena(temporario, nome)
    return registro


# ------------------------------- GITHUB ---------------------------------
def _git(*args):
    r = subprocess.run(
        ["git", *args], cwd=PASTA, capture_output=True, text=True,
        timeout=TIMEOUT_GIT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def _garantir_gitignore():
    caminho = os.path.join(PASTA, ".gitignore")
    ignorar = ["melhorias/quarentena/", "auto_aprimorar.log*", ".auto_aprimorar.lock",
               "melhorias.json.tmp", "config_aprimorar.json", "__pycache__/"]
    atual = ""
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            atual = f.read()
    faltando = [l for l in ignorar if l not in atual]
    if faltando:
        with open(caminho, "a", encoding="utf-8") as f:
            if atual and not atual.endswith("\n"):
                f.write("\n")
            f.write("\n".join(faltando) + "\n")


def salvar_no_github(registros):
    """Commit + push de tudo que foi aprimorado. Nunca derruba o ciclo."""
    aplicadas = [r for r in registros if r["status"] == "aplicada"]
    if not GIT_ATIVO or not aplicadas:
        return False
    try:
        _git("rev-parse", "--is-inside-work-tree")
        _garantir_gitignore()
        _git("add", "--", "melhorias", "melhorias.json", ".gitignore")
        if not _git("status", "--porcelain"):
            return False
        msg = "Jarvis aprimorou: " + "; ".join(r["descricao"] for r in aplicadas)
        _git("commit", "-m", msg[:200])
        _git("push")
        log.info("melhorias enviadas ao GitHub")
        return True
    except Exception as e:
        log.error("falha ao salvar no GitHub: %s", e)
        return False


# --------------------------- MODO POR VOZ -------------------------------
_aguardando_escolha = False


def _ler_config():
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    cfg.setdefault("modo", MODO_PADRAO)
    cfg.setdefault("data", "")
    return cfg


def _salvar_config(modo):
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump({"modo": modo, "data": datetime.date.today().isoformat()}, f)


def _modo_permite_hoje():
    cfg = _ler_config()
    hoje = datetime.date.today().isoformat()
    return cfg["modo"] == "todo_dia" or (cfg["modo"] == "hoje" and cfg["data"] == hoje)


def _rodar_ciclo_agora():
    """Dispara um ciclo em segundo plano (forçado: o usuário acabou de pedir)."""
    threading.Thread(target=executar_ciclo, kwargs={"forcar": True}, daemon=False).start()


def comando_aprimoramento(texto):
    """Conversa de ativação. Devolve a fala do Jarvis ou None se não for com ele."""
    global _aguardando_escolha
    fala = _normalizar(texto)

    if _aguardando_escolha:
        if "todo dia" in fala or "todos os dias" in fala or "diario" in fala:
            _aguardando_escolha = False
            _salvar_config("todo_dia")
            _rodar_ciclo_agora()
            return "Certo, senhor. Vou aprimorar todo dia e salvar tudo no GitHub."
        if "hoje" in fala:
            _aguardando_escolha = False
            _salvar_config("hoje")
            _rodar_ciclo_agora()
            return "Certo, senhor. Vou aprimorar só hoje. Aviso quando terminar."
        if "cancelar" in fala or fala == "nao":
            _aguardando_escolha = False
            return "Tudo bem, senhor. Não vou ativar o aprimoramento."
        return "Não entendi, senhor. Diga: aprimora só hoje, ou aprimora todo dia."

    if "desativar aprimoramento" in fala:
        _salvar_config("desligado")
        return "Aprimoramento automático desativado, senhor."
    if "ativar aprimoramento" in fala:
        _aguardando_escolha = True
        return ("Senhor, tenho duas opções: aprimorar só hoje, "
                "ou aprimorar todo dia. Qual prefere?")
    return None


# ------------------------------- API ------------------------------------
def executar_ciclo(forcar=False):
    """Roda o aprimoramento. Retorna quantas melhorias foram aplicadas."""
    hoje = datetime.date.today().isoformat()
    if not forcar:
        if not _modo_permite_hoje():
            return 0
        if _ler_log().get("ultima_execucao") == hoje:
            return 0
    if not _travar():
        log.info("já existe outro ciclo em andamento; saindo")
        return 0

    novos, aplicadas = [], 0
    try:
        catalogo = _catalogo()
        for _ in range(MELHORIAS_POR_CICLO):
            try:
                registro = _gerar_melhoria(catalogo)
            except ErroOllama as e:
                log.error("Ollama indisponível, ciclo interrompido: %s", e)
                break
            novos.append(registro)
            aplicadas += registro["status"] == "aplicada"
    except Exception:
        log.exception("erro inesperado no ciclo")
    finally:
        # Relê o log: o Jarvis pode ter marcado "anunciada" enquanto o Ollama pensava.
        dados = _ler_log()
        dados["melhorias"].extend(novos)
        if novos:
            dados["ultima_execucao"] = hoje
        _salvar_log(dados)
        salvar_no_github(novos)
        _limpar_quarentena()
        _destravar()
    return aplicadas


def carregar_melhorias():
    """Importa os plugins aprovados (revalidando antes). Plugin que quebrar vai para a quarentena."""
    modulos = []
    for arq in sorted(os.listdir(PASTA_PLUGINS)):
        if not arq.endswith(".py"):
            continue
        caminho = os.path.join(PASTA_PLUGINS, arq)
        try:
            with open(caminho, encoding="utf-8") as f:
                _validar_ast(f.read())  # o arquivo pode ter sido alterado depois de aprovado
            spec = importlib.util.spec_from_file_location(arq[:-3], caminho)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            modulos.append(mod)
        except Exception as e:
            log.warning("plugin %s enviado à quarentena: %s", arq, e)
            try:
                _mover_quarentena(caminho)
            except OSError:
                pass
    return modulos


def _executar_com_limite(mod, texto):
    resultado = {}

    def alvo():
        try:
            resultado["r"] = mod.executar(texto)
        except Exception as e:
            resultado["e"] = e

    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    t.join(TIMEOUT_EXECUCAO)
    r = resultado.get("r")
    if isinstance(r, str) and r.strip():
        return r
    log.warning("plugin %s falhou ou demorou: %s", getattr(mod, "__name__", "?"),
                resultado.get("e", "sem resposta"))
    return "Senhor, essa melhoria não respondeu como esperado."


def responder_comando(texto, modulos=None):
    """Comando de ativação ou melhoria ativada pela fala; senão None."""
    resposta = comando_aprimoramento(texto)
    if resposta:
        return resposta
    fala = f" {_normalizar(texto)} "
    for mod in (modulos if modulos is not None else carregar_melhorias()):
        if any(f" {_normalizar(f)} " in fala for f in mod.FRASES):
            return _executar_com_limite(mod, texto)
    return None


def resumo_para_falar():
    """Texto para o Jarvis falar quando você desbloquear o PC (ou None)."""
    dados = _ler_log()
    novas = [m for m in dados["melhorias"] if not m.get("anunciada")]
    if not novas:
        return None
    ok = []
    for m in novas:
        if m["status"] == "aplicada":
            item = m["descricao"].rstrip(".")
            if m.get("frases"):
                item += f' (diga "{m["frases"][0]}")'
            ok.append(item)
    falhas = sum(1 for m in novas if m["status"] != "aplicada")

    if ok and falhas:
        texto = ("Senhor, eu aprimorei hoje: " + "; ".join(ok) + ". "
                 f"Outras {falhas} tentativa(s) não passaram nos testes e foram descartadas.")
    elif ok:
        texto = "Senhor, eu aprimorei hoje: " + "; ".join(ok) + "."
    else:
        texto = (f"Senhor, tentei {falhas} melhoria(s) hoje, mas nenhuma passou nos testes. "
                 "Foram descartadas.")
    for m in novas:
        m["anunciada"] = True
    _salvar_log(dados)
    return texto


# ------------------------------ COMANDOS --------------------------------
def _listar():
    dados = _ler_log()
    if not dados["melhorias"]:
        print("Nenhuma melhoria registrada ainda.")
        return
    for m in dados["melhorias"]:
        extra = f" - {m['motivo']}" if m.get("motivo") else ""
        print(f"{m['data']}  [{m['status']:<10}] {m['arquivo']}  {m['descricao']}{extra}")


def _desativar(arquivo):
    caminho = os.path.join(PASTA_PLUGINS, os.path.basename(arquivo))
    if not os.path.exists(caminho):
        print("Não achei essa melhoria em melhorias/.")
        return
    _mover_quarentena(caminho)
    dados = _ler_log()
    for m in dados["melhorias"]:
        if m["arquivo"] == os.path.basename(arquivo):
            m["status"] = "desativada"
    _salvar_log(dados)
    print("Melhoria desativada (movida para a quarentena).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Auto aprimoramento do Jarvis")
    ap.add_argument("--forcar", action="store_true", help="roda mesmo já tendo rodado hoje")
    ap.add_argument("--listar", action="store_true", help="mostra o histórico")
    ap.add_argument("--desativar", metavar="ARQUIVO", help="desativa uma melhoria")
    args = ap.parse_args()
    if args.listar:
        _listar()
    elif args.desativar:
        _desativar(args.desativar)
    else:
        n = executar_ciclo(forcar=args.forcar)
        log.info("%d melhoria(s) aplicada(s).", n)
