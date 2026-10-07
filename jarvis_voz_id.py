"""
Jarvis - identificação de voz (só obedece a voz cadastrada).

Coloque este arquivo na MESMA pasta do jarvis_acoes.py.

Instalar (uma vez) - SEM precisar do compilador C++ (webrtcvad não é necessário):
    pip install sounddevice numpy scipy librosa torch
    pip install resemblyzer --no-deps

Como funciona:
    - No cadastro ele grava frases longas, frases curtas de comando E as mesmas frases em
      vários "jeitos de falar" (baixo, alto, grave, agudo, rápido, devagar). Cada gravação
      ainda ganha cópias com o tom um pouco mais grave/agudo e com ruído. Assim ele conhece a
      sua voz em vários estilos, não só no tom normal.
    - Na conferência, a nota de semelhança é a MELHOR entre o perfil médio e as amostras mais
      parecidas do cadastro. Se o senhor falar baixo, ele compara com as amostras em que o
      senhor falou baixo.
    - Comandos curtos (menos de ~1,6 s de voz) são difíceis de identificar para qualquer modelo:
      o cadastro também grava trechos curtos da sua voz e, nesses comandos, o limiar desce um pouco
      e conta junto a nota do "Jarvis" que acabou de ser dito.
    - Quando alguém diz "Jarvis", ele confere a voz na hora: se for claramente outra pessoa,
      responde "Essa voz não está cadastrada no meu banco de dados".
    - Depois confere de novo no comando (mais rigoroso, com mais áudio).
    - Cada comando aceito com folga ensina um pouco mais sobre a sua voz (aprende sozinho).
    - Outra voz só entra quando uma voz JÁ cadastrada diz: "Jarvis, cadastra voz nova".

Ferramentas de terminal (com o Jarvis FECHADO):
    python jarvis_voz_id.py refazer     apaga o cadastro e cadastra de novo, mostrando as notas
    python jarvis_voz_id.py testar      testa a sua voz em vários jeitos de falar e mostra as notas

IMPORTANTE: quem já tinha cadastro deve rodar "refazer" uma vez para gravar os novos estilos.

Para investigar: a cada conferência ele salva o que ouviu em voz_debug.wav (dá para escutar).
Se mesmo assim ele rejeitar a sua voz em algum estilo, veja a nota no "testar" e baixe um pouco
o LIMIAR_MIN abaixo (quanto menor, mais fácil de aceitar, mas outra pessoa parecida também passa).
"""

import os
import re
import sys
import threading
import time
import unicodedata
import wave
from collections import deque

import numpy as np

# ---------------- Configurações ----------------
ARQUIVO_VOZES = "voz_cadastrada.npz"
LIMIAR = 0.62            # usado só se o cálculo automático não puder rodar
LIMIAR_MIN = 0.55        # a sensibilidade automática nunca fica abaixo disso
LIMIAR_MAX = 0.75        # nem acima disso
MARGEM_CHAMADO = 0.10    # no "Jarvis" (palavra curta) só rejeita se estiver bem abaixo do limiar
NOTA_MINIMA_CHAMADO = 0.40
TOP_K = 5                # a nota usa a média das K amostras do cadastro mais parecidas com a fala
CURTO_ATE = 1.6          # falas com menos voz que isso (s) são "curtas": o modelo erra mais nelas
DESCONTO_CURTO = 0.15    # quanto o limiar desce, no máximo, para falas bem curtas (~0,5 s)
CURTO_PISO = 0.52        # nenhuma fala curta é aceita com nota abaixo disso
TRECHO_SEG = 0.9         # no cadastro, cada gravação também vira trechos curtos deste tamanho
TRECHO_PASSO = 0.45
TRECHOS_POR_GRAVACAO = 8
VARIANTES_TOM = (-2.5, -1.2, 1.2, 2.5)   # semitons: cópias mais graves/agudas de cada gravação
TAXA = 16000             # taxa de amostragem usada na gravação
MIN_SEGUNDOS_VOZ = 0.5   # menos voz que isso é "curto demais" para julgar
JANELA_ESCUTA = 10       # segundos de áudio guardados
JANELA_VERIFICACAO = 8   # segundos finais usados para conferir quem falou
CONVERSA_SEGUNDOS = 20   # depois de uma aprovação, áudio curtíssimo ainda passa por este tempo
APRENDER_FOLGA = 0.08    # só aprende com comandos aceitos com esta folga acima do limiar
MAX_AMOSTRAS = 420       # limite de amostras guardadas por voz
AMOSTRAS_FIXAS = 240     # as primeiras (do cadastro) nunca são descartadas
SEGUNDOS_FRASE_LONGA = 7
SEGUNDOS_FRASE_CURTA = 3.5
SEGUNDOS_FRASE_ESTILO = 4.0
FRASES_LONGAS = [
    "Olá Jarvis, eu sou o dono deste computador e estou cadastrando a minha voz para você me reconhecer sempre.",
    "Hoje o dia está bonito, eu quero ouvir a previsão do tempo, as principais notícias e depois uma boa música.",
    "Um, dois, três, quatro, cinco, seis, sete, oito, nove, dez. Agora estou falando em voz normal.",
]
FRASES_CURTAS = [   # parecidas com os comandos de verdade
    "Jarvis, que horas são?",
    "Jarvis, abrir o navegador.",
    "Jarvis, tocar música.",
    "Jarvis, qual a previsão do tempo?",
    "Jarvis, bloquear o computador.",
    "Jarvis, limpar o armazenamento.",
]
# (jeito de falar, frase): o mesmo comando dito de formas bem diferentes
FRASES_ESTILOS = [
    ("BAIXO, quase sussurrando", "Jarvis, que horas são?"),
    ("bem ALTO, como se estivesse chamando de longe", "Jarvis, abrir o navegador."),
    ("com a voz mais GROSSA e grave que o senhor conseguir", "Jarvis, tocar música."),
    ("com a voz mais FINA e aguda que o senhor conseguir", "Jarvis, qual a previsão do tempo?"),
    ("RÁPIDO", "Jarvis, bloquear o computador."),
    ("bem DEVAGAR e arrastado", "Jarvis, limpar o armazenamento."),
]
FRASE_TESTE = "Jarvis, que horas são agora?"
# frases que o Jarvis diz quando você o chama (a conferência do "Jarvis" acontece nelas)
RESPOSTAS_DE_CHAMADO = ("sim, senhor", "sim senhor", "pois nao", "as suas ordens", "ao seu dispor",
                        "estou ouvindo", "diga, senhor", "pode falar")
# -----------------------------------------------

_PASTA = os.path.dirname(os.path.abspath(__file__))
_CAMINHO = os.path.join(_PASTA, ARQUIVO_VOZES)
_CAMINHO_DEBUG = os.path.join(_PASTA, "voz_debug.wav")
_encoder = []   # cache do modelo (demora alguns segundos para carregar)
_trava_modelo = threading.Lock()
_ultima = {"aprovado": 0.0, "rejeitado": 0.0, "nota_chamado": 0.0, "t_chamado": 0.0}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def _importar_resemblyzer():
    """Importa o resemblyzer sem exigir o webrtcvad (que não instala sem compilador C++)."""
    import types
    try:
        import webrtcvad  # noqa: F401
    except ImportError:
        sys.modules["webrtcvad"] = types.ModuleType("webrtcvad")  # só para o import passar
    try:
        import scipy.ndimage.morphology  # noqa: F401
    except ImportError:  # versões novas do scipy removeram esse caminho antigo
        import scipy.ndimage
        sys.modules["scipy.ndimage.morphology"] = scipy.ndimage
    from resemblyzer import VoiceEncoder
    return VoiceEncoder


def _modelo():
    with _trava_modelo:
        if not _encoder:
            VoiceEncoder = _importar_resemblyzer()
            _encoder.append(VoiceEncoder(verbose=False))
        return _encoder[0]


def _unitario(v):
    return v / (np.linalg.norm(v) + 1e-9)


def _preparar(audio_pcm16: bytes):
    """PCM 16 bits -> float, volume normalizado e só os trechos em que há voz."""
    onda = np.frombuffer(audio_pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    if len(onda) == 0:
        return onda
    rms = float(np.sqrt(np.mean(onda ** 2)))
    if rms < 1e-4:
        return onda[:0]
    onda = np.clip(onda * (10 ** (-30 / 20) / rms), -1.0, 1.0)   # volume padrão de -30 dBFS

    janela = int(TAXA * 0.03)
    n = len(onda) // janela
    if n < 3:
        return onda
    quadros = onda[:n * janela].reshape(n, janela)
    energia = np.sqrt(np.mean(quadros ** 2, axis=1))
    limite = max(float(np.percentile(energia, 25)) * 3.0, float(energia.max()) * 0.08)
    ativos = energia > limite
    ativos = ativos | np.roll(ativos, 1) | np.roll(ativos, -1)   # um respiro de cada lado
    return quadros[ativos].reshape(-1)


def _embedding_onda(onda):
    return _unitario(_modelo().embed_utterance(onda.astype(np.float32)))


def _embeddings_segmentos(onda):
    """Quebra a voz em pedaços de ~1,6 s (como os comandos curtos de verdade) e tira a impressão de cada um."""
    tam, passo = int(TAXA * 1.6), int(TAXA * 0.8)
    if len(onda) <= tam:
        return [_embedding_onda(onda)]
    return [_embedding_onda(onda[i:i + tam]) for i in range(0, len(onda) - tam + 1, passo)]


def _embeddings_variantes(onda):
    """Mesma fala com o tom um pouco mais grave/agudo e com ruído, para o reconhecimento aguentar variações."""
    saidas = []
    try:
        import librosa
        base = onda.astype(np.float32)
        for passos in VARIANTES_TOM:
            v = librosa.effects.pitch_shift(base, sr=TAXA, n_steps=passos)
            saidas.append(_embedding_onda(np.clip(v, -1.0, 1.0)))
    except Exception as erro:
        print(f"[voz] não consegui criar variações de tom ({erro}); sigo sem elas")
    try:
        rms = float(np.sqrt(np.mean(onda ** 2)))
        ruido = onda + np.random.normal(0.0, rms * 0.2, len(onda)).astype(np.float32)
        saidas.append(_embedding_onda(np.clip(ruido, -1.0, 1.0)))
    except Exception:
        pass
    return saidas


def _embeddings_trechos_curtos(onda):
    """Trechos de ~0,9 s da gravação: ensinam como a SUA voz soa em falas curtas (comandos de verdade)."""
    tam, passo = int(TAXA * TRECHO_SEG), int(TAXA * TRECHO_PASSO)
    if len(onda) <= tam:
        return []
    inicios = list(range(0, len(onda) - tam + 1, passo))
    if len(inicios) > TRECHOS_POR_GRAVACAO:
        idx = np.linspace(0, len(inicios) - 1, TRECHOS_POR_GRAVACAO).astype(int)
        inicios = [inicios[i] for i in idx]
    try:
        return [_embedding_onda(onda[i:i + tam]) for i in inicios]
    except Exception as erro:
        print(f"[voz] não consegui criar trechos curtos ({erro}); sigo sem eles")
        return []


def _limiar_efetivo(limiar: float, util: float) -> float:
    """Falas curtas dão notas mais baixas mesmo sendo a mesma pessoa: o limiar desce junto."""
    if util >= CURTO_ATE:
        return limiar
    f = min(1.0, (CURTO_ATE - util) / (CURTO_ATE - 0.5))
    return max(min(CURTO_PISO, limiar), limiar - DESCONTO_CURTO * f)


def _salvar_debug(onda):
    """Guarda o que o modelo 'ouviu' para você poder escutar (voz_debug.wav)."""
    try:
        pcm = (np.clip(onda, -1, 1) * 32767).astype(np.int16)
        with wave.open(_CAMINHO_DEBUG, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TAXA)
            w.writeframes(pcm.tobytes())
    except Exception:
        pass


def audio_de_speech_recognition(audio) -> bytes:
    """Converte o objeto 'audio' do speech_recognition (recognizer.listen) para o formato daqui."""
    return audio.get_raw_data(convert_rate=TAXA, convert_width=2)


def gravar(segundos: float) -> bytes:
    """Grava do microfone padrão. Devolve PCM 16 bits, 16 kHz, mono."""
    import sounddevice as sd
    dados = sd.rec(int(segundos * TAXA), samplerate=TAXA, channels=1, dtype="int16")
    sd.wait()
    return dados.tobytes()


# ======================= ESCUTA CONTÍNUA =======================
# Guarda os últimos segundos do microfone numa memória circular. Enquanto o Jarvis fala, o que o
# microfone pega é trocado por silêncio (assim ele não "ouve" a si mesmo, mas o seu "Jarvis" e o
# comando continuam juntos no mesmo trecho). O cadastro usa essa MESMA memória.

_blocos = deque(maxlen=JANELA_ESCUTA * 10)   # blocos de 0,1 s
_trava = threading.Lock()
_escuta = {"stream": None, "ok": False}
_falando = {"v": False, "ate": 0.0}


def marcar_fala(ativo: bool):
    """O Jarvis avisa quando começa/termina de falar."""
    _falando["v"] = ativo
    if not ativo:
        _falando["ate"] = time.time() + 0.4   # sobra do eco da caixa de som


def iniciar_escuta_continua():
    """Liga a escuta em segundo plano (chame uma vez). Também já carrega o modelo de voz."""
    if _escuta["stream"] is not None:
        return
    try:
        import sounddevice as sd

        def _callback(indata, frames, tempo, status):
            bloco = indata[:, 0].copy()
            if _falando["v"] or time.time() < _falando["ate"]:
                bloco[:] = 0
            with _trava:
                _blocos.append(bloco)

        stream = sd.InputStream(samplerate=TAXA, channels=1, dtype="int16",
                                blocksize=TAXA // 10, callback=_callback)
        stream.start()
        _escuta["stream"], _escuta["ok"] = stream, True
        print("[voz] escuta contínua ligada")
    except Exception as erro:
        print(f"[voz] não consegui abrir o microfone para identificar a voz ({erro}). "
              "A verificação de voz ficará desligada.")
    threading.Thread(target=_modelo_seguro, daemon=True).start()


def _modelo_seguro():
    try:
        _modelo()
    except Exception as erro:
        print(f"[voz] erro ao carregar o modelo de voz: {erro}")


def limpar_buffer():
    """Esquece o áudio recente."""
    with _trava:
        _blocos.clear()


def _audio_recente(segundos=None) -> bytes:
    with _trava:
        blocos = list(_blocos)
    if segundos:
        blocos = blocos[-int(segundos * 10):]
    return b"".join(b.tobytes() for b in blocos)


def _capturar(segundos: float) -> bytes:
    """Toca um sinal e grava os próximos segundos (pela escuta contínua, se estiver ligada)."""
    try:
        import winsound
        winsound.Beep(900, 150)
    except Exception:
        pass
    if _escuta["ok"]:
        limpar_buffer()
        time.sleep(segundos)
        return _audio_recente()
    return gravar(segundos)


# ======================= PERFIS =======================
# Arquivo: "voz_N" = impressão digital média; "lim_voz_N" = sensibilidade; "amo_voz_N" = amostras.

def _carregar() -> dict:
    if not os.path.exists(_CAMINHO):
        return {}
    with np.load(_CAMINHO) as f:
        return {nome: f[nome] for nome in f.files}


def _vozes(dados: dict) -> dict:
    """{'voz_1': (perfil, limiar, amostras ou None)}"""
    return {n: (a, float(dados.get("lim_" + n, LIMIAR)), dados.get("amo_" + n))
            for n, a in dados.items() if n.startswith("voz_")}


def _salvar(dados: dict):
    np.savez(_CAMINHO, **dados)


def tem_cadastro() -> bool:
    return bool(_vozes(_carregar()))


def _nota_voz(emb, perfil, amostras=None) -> float:
    """Semelhança com a voz cadastrada: a MELHOR entre o perfil médio e a média das amostras mais parecidas.

    Assim, quem fala baixo é comparado com as amostras em que falou baixo, e não só com a média de tudo.
    """
    nota = float(np.dot(emb, perfil))
    if amostras is None or len(amostras) < 5:
        return nota
    sims = np.sort(np.asarray(amostras) @ emb)[::-1]
    return max(nota, float(np.mean(sims[:min(TOP_K, len(sims))])))


def _calcular_limiar(registros) -> float:
    """Sensibilidade automática: cada gravação é testada contra o cadastro feito SEM ela.

    registros = [(tipo, [embeddings originais], [embeddings de variações])]
    """
    todas, de_uso = [], []
    for i, (tipo, origs, _) in enumerate(registros):
        resto = [e for j, (_, o, a) in enumerate(registros) if j != i for e in o + a]
        if len(resto) < 5:
            continue
        amo = np.array(resto)
        perfil = _unitario(amo.mean(axis=0))
        for e in origs:
            n = _nota_voz(e, perfil, amo)
            todas.append(n)
            if tipo != "longa":
                de_uso.append(n)
    if not todas:
        return LIMIAR
    # frases curtas e de estilos parecem mais com o uso real: pesam mais no cálculo
    alvo = de_uso if len(de_uso) >= 4 else todas
    base = float(np.percentile(alvo, 10))
    print(f"[voz] consistência do cadastro: pior nota {min(todas):.2f}, típica {np.median(todas):.2f}, "
          f"nas frases curtas/estilos {np.median(de_uso) if de_uso else 0:.2f}")
    return float(np.clip(base - 0.05, LIMIAR_MIN, LIMIAR_MAX))


def _pedir(J, instrucao: str, segundos: float, minimo: float):
    """Fala a instrução, grava e devolve a voz limpa (ou None se não ouviu depois de 3 tentativas)."""
    for _ in range(3):
        J.falar(instrucao)
        onda = _preparar(_capturar(segundos))
        if len(onda) >= TAXA * minimo:
            return onda
        J.falar("Não ouvi direito. Fale perto do microfone. Vamos de novo.")
    return None


def _gravar_perfil(J, quem: str):
    """Grava as frases, monta o perfil e confere com uma frase de teste. Devolve (perfil, limiar, amostras) ou None."""
    registros = []   # (tipo, [originais], [variações])
    total = len(FRASES_LONGAS)
    for i, frase in enumerate(FRASES_LONGAS, 1):
        onda = _pedir(J, f"Frase {i} de {total}. Depois do sinal, leia: {frase}", SEGUNDOS_FRASE_LONGA, 2.5)
        if onda is None:
            J.falar(f"Não consegui captar a voz de {quem}, senhor. Cadastro cancelado.")
            return None
        registros.append(("longa", _embeddings_segmentos(onda) + [_embedding_onda(onda)],
                          _embeddings_trechos_curtos(onda)))

    J.falar("Agora algumas frases curtas, como os comandos de verdade.")
    for frase in FRASES_CURTAS:
        onda = _pedir(J, f"Diga depois do sinal: {frase}", SEGUNDOS_FRASE_CURTA, 0.6)
        if onda is None:
            continue
        registros.append(("curta", [_embedding_onda(onda)],
                          _embeddings_variantes(onda) + _embeddings_trechos_curtos(onda)))

    J.falar("Agora vou pedir comandos de jeitos diferentes: baixo, alto, grave, agudo, rápido e devagar. "
            "Pode exagerar um pouco, é assim que eu aprendo a reconhecer o senhor de qualquer jeito.")
    for estilo, frase in FRASES_ESTILOS:
        onda = _pedir(J, f"Diga {estilo}, depois do sinal: {frase}", SEGUNDOS_FRASE_ESTILO, 0.5)
        if onda is None:
            continue
        registros.append(("estilo", [_embedding_onda(onda)],
                          _embeddings_variantes(onda) + _embeddings_trechos_curtos(onda)))

    if sum(1 for r in registros if r[0] != "longa") < 4:
        J.falar(f"Não consegui captar frases suficientes de {quem}, senhor. Cadastro cancelado.")
        return None

    amostras = np.array([e for _, o, a in registros for e in o + a])
    perfil = _unitario(amostras.mean(axis=0))
    limiar = _calcular_limiar(registros)

    # frase de teste: passa pelo mesmo caminho dos comandos de verdade
    melhor = None
    for _ in range(3):
        J.falar(f"Para confirmar, diga depois do sinal: {FRASE_TESTE}")
        onda = _preparar(_capturar(SEGUNDOS_FRASE_CURTA + 1))
        if len(onda) < TAXA * MIN_SEGUNDOS_VOZ:
            J.falar("Não ouvi direito.")
            continue
        nota = _nota_voz(_embedding_onda(onda), perfil, amostras)
        print(f"[voz] teste de confirmação: nota {nota:.2f} (precisa de {limiar:.2f})")
        melhor = nota if melhor is None else max(melhor, nota)
        if nota >= limiar:
            break
        J.falar("Não reconheci, vamos de novo.")
    else:
        if melhor is not None:   # não passou: ajusta a sensibilidade para a sua voz passar
            limiar = float(np.clip(melhor - 0.05, 0.45, limiar))
            print(f"[voz] sensibilidade ajustada para {limiar:.2f}")
            J.falar("Ajustei a sensibilidade para a sua voz.")
    print(f"[voz] cadastro pronto: {len(amostras)} amostras, sensibilidade {limiar:.2f}")
    return perfil, limiar, amostras


def _guardar_voz(dados: dict, n: int, perfil, limiar, amostras):
    dados[f"voz_{n}"] = perfil
    dados[f"lim_voz_{n}"] = np.array(limiar)
    dados[f"amo_voz_{n}"] = amostras
    _salvar(dados)


# ======================= USO PELO JARVIS =======================

def cadastro_inicial(J) -> bool:
    """Chame uma vez ao iniciar. Se ainda não há voz cadastrada, pede o cadastro."""
    if tem_cadastro():
        return True
    J.falar("Olá, senhor. Ainda não conheço a sua voz. Vou gravar algumas frases para cadastrá-la. "
            "Fique em um lugar silencioso. Primeiro em voz normal, depois de outros jeitos.")
    r = _gravar_perfil(J, "o senhor")
    if r is None:
        return False
    _guardar_voz({}, 1, *r)
    J.falar("Voz cadastrada. A partir de agora só obedeço a ela, senhor.")
    return True


def cadastrar_voz_nova(J) -> bool:
    """Adiciona mais uma pessoa. Só chame depois de a voz de quem pediu ter sido aprovada."""
    dados = _carregar()
    n = len(_vozes(dados)) + 1
    J.falar(f"Certo, senhor. Peça para a nova pessoa ficar perto do microfone. Cadastrando a voz {n}.")
    r = _gravar_perfil(J, f"a voz {n}")
    if r is None:
        return False
    _guardar_voz(dados, n, *r)
    J.falar(f"Voz {n} cadastrada com sucesso.")
    return True


def _avaliar(audio_pcm16: bytes):
    """(nome, nota, limiar, emb, segundos de voz) da voz que melhor combina; nome None se voz curta demais."""
    vozes = _vozes(_carregar())
    onda = _preparar(audio_pcm16)
    _salvar_debug(onda)
    util = len(onda) / TAXA
    if util < MIN_SEGUNDOS_VOZ:
        return None, 0.0, 0.0, None, util
    emb = _embedding_onda(onda)
    melhor = None   # (folga, nome, nota, limiar)
    for nome, (perfil, limiar, amostras) in vozes.items():
        nota = _nota_voz(emb, perfil, amostras)
        if melhor is None or nota - limiar > melhor[0]:
            melhor = (nota - limiar, nome, nota, limiar)
    _, nome, nota, limiar = melhor
    return nome, nota, limiar, emb, util


def _aprender(nome: str, emb):
    """Acrescenta este comando (aceito com folga) às amostras da voz, para o perfil acompanhar o uso real."""
    try:
        dados = _carregar()
        perfil = dados[nome]
        amostras = dados.get("amo_" + nome)
        amostras = np.vstack([perfil, emb]) if amostras is None else np.vstack([amostras, emb])
        if len(amostras) > MAX_AMOSTRAS:
            amostras = np.vstack([amostras[:AMOSTRAS_FIXAS], amostras[-(MAX_AMOSTRAS - AMOSTRAS_FIXAS):]])
        dados["amo_" + nome] = amostras
        dados[nome] = _unitario(amostras.mean(axis=0))
        _salvar(dados)
    except Exception as erro:
        print(f"[voz] não consegui atualizar o perfil: {erro}")


def autorizado(audio_pcm16: bytes, aprender: bool = True) -> bool:
    """True se a voz do áudio bate com alguma voz cadastrada (conferência do comando)."""
    if not tem_cadastro():
        return True   # nada cadastrado ainda: não trava (o cadastro inicial resolve)
    try:
        nome, nota, limiar, emb, util = _avaliar(audio_pcm16)
    except ImportError:
        print("[voz] resemblyzer não instalado: pip install resemblyzer --no-deps  (verificação desligada)")
        return True
    agora = time.time()
    if nome is None:
        if agora - _ultima["aprovado"] < CONVERSA_SEGUNDOS:
            print(f"[voz] voz curtíssima ({util:.1f}s), mas a conversa já foi aprovada -> aceito")
            return True
        print(f"[voz] voz curtíssima ({util:.1f}s) para identificar -> REJEITADO")
        return False
    lim_ef = _limiar_efetivo(limiar, util)
    nota_final = nota
    if util < CURTO_ATE and agora - _ultima["t_chamado"] < 20 and _ultima["nota_chamado"] > 0:
        # comando curto: soma a evidência do "Jarvis" que acabou de ser dito
        nota_final = max(nota, (nota + _ultima["nota_chamado"]) / 2)
    ok = nota_final >= lim_ef
    junto = f", com o 'Jarvis' {nota_final:.2f}" if nota_final != nota else ""
    print(f"[voz] semelhança {nota:.2f}{junto} (precisa de {lim_ef:.2f}), {util:.1f}s de voz -> "
          f"{'aceito' if ok else 'REJEITADO'}")
    if ok:
        _ultima["aprovado"] = agora
        if aprender and nota >= lim_ef + APRENDER_FOLGA:
            _aprender(nome, emb)
    return ok


def autorizado_agora() -> bool:
    """Confere a voz do que foi falado nos últimos segundos (use no comando)."""
    if not _escuta["ok"] or not tem_cadastro():
        return True
    return autorizado(_audio_recente(JANELA_VERIFICACAO))


def eh_chamado(texto: str) -> bool:
    """True se o texto é o Jarvis respondendo a 'Jarvis' (ex.: 'Sim, senhor.')."""
    t = _normalizar(texto).strip(" .!?,")
    return any(t == r or t.startswith(r) for r in RESPOSTAS_DE_CHAMADO)


def autorizado_chamado() -> bool:
    """Conferência rápida no 'Jarvis': só reprova se for CLARAMENTE outra pessoa."""
    if not _escuta["ok"] or not tem_cadastro():
        return True
    try:
        nome, nota, limiar, emb, util = _avaliar(_audio_recente(JANELA_VERIFICACAO))
    except Exception as erro:
        print(f"[voz] erro na conferência do chamado: {erro}")
        return True
    if nome is None:
        print(f"[voz] chamado curto ({util:.1f}s): deixo passar e confiro no comando")
        return True
    lim_ef = _limiar_efetivo(limiar, util)
    corte = max(NOTA_MINIMA_CHAMADO, lim_ef - MARGEM_CHAMADO)
    ok = nota >= corte
    _ultima["nota_chamado"], _ultima["t_chamado"] = nota, time.time()
    print(f"[voz] chamado: semelhança {nota:.2f} (reprova abaixo de {corte:.2f}) -> "
          f"{'ok' if ok else 'REJEITADO'}")
    if ok and nota >= lim_ef:
        _ultima["aprovado"] = time.time()
    if not ok:
        _ultima["rejeitado"] = time.time()
    return ok


def rejeicao_recente(segundos: float = 25) -> bool:
    """True se acabamos de avisar 'voz não cadastrada' (evita repetir a mensagem no comando)."""
    return time.time() - _ultima["rejeitado"] < segundos


def eh_pedido_de_cadastro(texto: str) -> bool:
    """Reconhece 'Jarvis, cadastra voz nova' (e variações como 'jarves cadastrar nova voz')."""
    palavras = set(re.findall(r"[a-z0-9]+", _normalizar(texto)))
    cadastra = any(w.startswith("cadastr") for w in palavras)
    return cadastra and "voz" in palavras and bool(palavras & {"nova", "novo", "outra", "outro"})


# ======================= FERRAMENTAS DE TERMINAL =======================

class _Console:
    """Faz de conta de Jarvis só para cadastrar/testar pelo terminal."""

    def falar(self, texto):
        print("\nJarvis:", texto)
        time.sleep(min(8.0, 1.5 + len(texto) / 25))   # tempo de ler antes do sinal


def _principal():
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo not in ("refazer", "testar"):
        print(__doc__)
        return
    iniciar_escuta_continua()
    time.sleep(1.5)
    if modo == "refazer":
        if os.path.exists(_CAMINHO):
            os.remove(_CAMINHO)
        if cadastro_inicial(_Console()):
            print("\nPronto! Agora abra o Jarvis normalmente.")
    else:
        if not tem_cadastro():
            print("Ainda não há voz cadastrada. Rode: python jarvis_voz_id.py refazer")
            return
        jeitos = [("em voz NORMAL", FRASE_TESTE)] + [(e, FRASE_TESTE) for e, _ in FRASES_ESTILOS]
        for i, (estilo, frase) in enumerate(jeitos, 1):
            input(f"\n[{i}/{len(jeitos)}] Aperte ENTER e fale {estilo}: '{frase}' ")
            time.sleep(0.2)
            autorizado(_capturar(4), aprender=False)


if __name__ == "__main__":
    _principal()