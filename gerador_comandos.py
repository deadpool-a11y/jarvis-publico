import os, re, ast, difflib, importlib.util, unicodedata
import requests

# ===== CONFIGURAÇÃO =====
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELO = os.environ.get("JARVIS_MODELO", "qwen2.5:7b")   # troque por qwen2.5-coder:7b se errar muito
TENTATIVAS = 3

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comandos_auto")
os.makedirs(PASTA, exist_ok=True)

COMANDOS = []  # (gatilhos, funcao, nome)

# trechos que o Jarvis nunca pode gerar sozinho
PROIBIDOS = [
    "os.remove", "shutil.rmtree", "os.rmdir", "os.unlink", "rmtree", "rd /s",
    "del /f", "format c", "winreg", "shutdown", "taskkill", "reg delete",
]

PROMPT = """Você escreve comandos para um assistente de voz Python no Windows.
Responda SOMENTE com código Python, sem explicações e sem ```.
O arquivo deve ter exatamente esta estrutura:

GATILHOS = ["frase 1", "frase 2"]
def executar(texto, falar):
    ...

Regras:
- GATILHOS: frases em minúsculas e sem acento que ativam o comando. Cada frase deve ter pelo menos 2 palavras.
- 'falar' é uma função: falar("texto") faz o Jarvis falar em voz alta.
- Use só a biblioteca padrão ou bibliotecas comuns (webbrowser, os, datetime, subprocess, pyautogui).
- Nunca apague arquivos, nunca mexa no registro do Windows, nunca desligue o PC.
- Se for abrir pasta ou programa, use os.startfile ou subprocess.Popen.
- Não faça o comando falar nada além de uma confirmação curta (o sistema já avisa que o comando foi criado).

Comando pedido: {pedido}
{erro}"""


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


# ===== CARREGAR / ACHAR =====
def carregar_arquivo(caminho):
    nome = os.path.splitext(os.path.basename(caminho))[0]
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gatilhos = [normalizar(g) for g in mod.GATILHOS if len(normalizar(g).split()) >= 2]
    if not gatilhos:
        raise ValueError("nenhum gatilho com 2 palavras ou mais")
    COMANDOS.append((gatilhos, mod.executar, nome))


def carregar_todos():
    COMANDOS.clear()
    for arq in sorted(os.listdir(PASTA)):
        if arq.endswith(".py"):
            try:
                carregar_arquivo(os.path.join(PASTA, arq))
            except Exception as e:
                print(f"[Jarvis] erro ao carregar {arq}: {e}")
    print(f"[Jarvis] {len(COMANDOS)} comando(s) criado(s) carregado(s).")


def achar(texto):
    t = normalizar(texto)
    t = re.sub(r"^jar\w+\s+", "", t)  # tira o "Jarvis" do começo
    if not t:
        return None
    for gatilhos, func, _ in COMANDOS:
        for g in gatilhos:
            if g in t or difflib.SequenceMatcher(None, g, t).ratio() > 0.85:
                return func
    return None


# ===== CRIAR COMANDO NOVO =====
def _pedir_codigo(pedido, erro=""):
    aviso = f"\nA tentativa anterior falhou com este erro, corrija: {erro}" if erro else ""
    r = requests.post(OLLAMA_URL.rstrip("/") + "/api/generate", json={
        "model": MODELO,
        "prompt": PROMPT.format(pedido=pedido, erro=aviso),
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }, timeout=180)
    r.raise_for_status()
    texto = r.json()["response"].strip()
    return re.sub(r"^```(?:python)?|```$", "", texto, flags=re.M).strip()


def _validar(codigo):
    ast.parse(codigo)  # confere a sintaxe
    if "GATILHOS" not in codigo or "def executar" not in codigo:
        raise ValueError("faltou GATILHOS ou def executar(texto, falar)")
    baixo = codigo.lower()
    for p in PROIBIDOS:
        if p in baixo:
            raise ValueError(f"trecho proibido: {p}")


def criar_comando(texto, falar):
    """Cria, salva, carrega e executa um comando novo."""
    falar("Criando comando")
    erro = ""
    for tentativa in range(1, TENTATIVAS + 1):
        caminho = ""
        try:
            codigo = _pedir_codigo(texto, erro)
            _validar(codigo)

            nome = "cmd_" + re.sub(r"\W+", "_", normalizar(texto))[:30].strip("_")
            caminho = os.path.join(PASTA, nome + ".py")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(codigo)

            carregar_arquivo(caminho)              # aplica na hora
            falar("Novo comando criado, senhor")
            COMANDOS[-1][1](texto, falar)          # já executa
            return True
        except Exception as e:
            erro = str(e)
            print(f"[Jarvis] tentativa {tentativa} falhou: {erro}")
            try:
                if caminho and os.path.exists(caminho):
                    os.remove(caminho)
            except Exception:
                pass
    falar("Não consegui criar o comando, senhor")
    return False