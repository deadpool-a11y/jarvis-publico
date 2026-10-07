# Jarvis

Assistente de voz em Python para **Windows**, inspirado no Jarvis dos filmes. Ele escuta comandos falados, responde com voz, mostra um painel (HUD) na tela e cuida da sua rotina: agenda, clima, notícias, lembretes, e-mails e muito mais.

Roda com IA local pelo [Ollama](https://ollama.com), então não precisa pagar API para o básico.

## O que ele faz

- **Reconhecimento de voz e fala:** você fala, ele entende e responde em voz alta.
- **Só obedece a sua voz:** na primeira execução ele pede para cadastrar a sua voz. Se outra pessoa falar "Jarvis", ele responde que a voz não está cadastrada. Para cadastrar outra pessoa: *"Jarvis, cadastra voz nova"*.
- **Rotina de bom dia:** clima, compromissos do dia (Google Agenda), notícias e música.
- **Agenda e lembretes:** compromissos, avisos antes de começar e aviso de atraso.
- **Alarmes, temporizador, anotações e lista de compras.**
- **Memória de longo prazo:** você pede para ele lembrar de algo e ele guarda.
- **HUD na tela:** painel com hora, cotação do dólar e estado do computador.
- **Telegram e e-mail (opcionais):** controle e avisos pelo celular, leitura de e-mails novos do Gmail.
- **Comandos novos sob demanda (opcional):** se você pedir algo que ele não conhece, ele pode criar o comando com a IA local.
- **Auto aprimoramento (opcional):** ele pode se atualizar e contar o que mudou.

> Os recursos opcionais só funcionam se você configurar as variáveis da tabela abaixo.

## Requisitos

- Windows 10 ou 11
- **Python 3.12** (versões muito novas, como a 3.14, ainda não têm todos os pacotes)
- Microfone e caixas de som
- [Ollama](https://ollama.com) instalado

## Instalação

```powershell
git clone https://github.com/deadpool-a11y/jarvis-publico.git
cd jarvis-publico

py -3.12 -m venv venv
.\venv\Scripts\activate

pip install -r requirements.txt
pip install resemblyzer --no-deps
python -m playwright install

ollama pull qwen2.5:7b
```

O `resemblyzer` é instalado separado, com `--no-deps`, porque ele exige o `webrtcvad` original, que precisa de compilador. O `requirements.txt` já traz o `webrtcvad-wheels`, que faz o mesmo trabalho sem compilar.

## Primeiro uso

```powershell
python jarvis.py
```

Na primeira vez ele pede para cadastrar a sua voz. Fale as frases que ele pedir. O cadastro fica **só no seu computador**.

## Configuração (opcional)

Configure só o que for usar. No PowerShell, rode `setx NOME "valor"` e depois **abra um terminal novo**.

| Variável | Para que serve |
|---|---|
| `JARVIS_MODELO` | Modelo do Ollama (padrão: `qwen2.5:7b`) |
| `OLLAMA_URL` | Endereço do Ollama, se não for o padrão |
| `JARVIS_AGENDA` | Link secreto no formato iCal do Google Agenda (somente leitura) |
| `JARVIS_AGENDA_SCRIPT` e `JARVIS_AGENDA_SENHA` | Criar e alterar compromissos pelo Google Agenda |
| `JARVIS_GOOGLE_CONTA` | Seu e-mail do Google, se o navegador abrir a conta errada |
| `JARVIS_GMAIL_EMAIL` e `JARVIS_GMAIL_SENHA` | Ler e-mails (use **senha de app**, nunca a senha normal) |
| `JARVIS_TELEGRAM_TOKEN` | Controlar o Jarvis pelo Telegram (token do @BotFather) |
| `ANTHROPIC_API_KEY` | Opcional, para usar a API da Anthropic |
| `GEMINI_API_KEY` | Opcional, para usar a API do Gemini |

Exemplo:

```powershell
setx JARVIS_MODELO "qwen2.5:7b"
```

## Estrutura

| Arquivo | Função |
|---|---|
| `jarvis.py` | Ponto de entrada |
| `jarvis_acoes.py` | Comandos e ações principais |
| `jarvis_hud.py` | Painel na tela |
| `jarvis_rotina.py` | Rotina de bom dia, agenda, clima e notícias |
| `jarvis_voz.py`, `jarvis_mic.py` | Fala e escuta |
| `jarvis_voz_id.py` | Reconhecimento e cadastro da voz |
| `jarvis_memoria.py` | Memória de longo prazo |
| `jarvis_mensagens.py`, `jarvis_telegram.py` | Telegram e e-mail |
| `gerador_comandos.py`, `auto_aprimorar.py` | Comandos novos e auto aprimoramento |
| `jarvis_conferir.py` | Diagnóstico para achar o que está faltando |

## Segurança

- **Nunca publique** tokens, senhas, arquivos de voz (`.npz`, `.wav`), memória (`jarvis_dados/`) nem seus compromissos. O `.gitignore` já protege os principais.
- A memória é guardada em **texto simples**. Não peça para o Jarvis guardar senhas ou números de cartão.
- O gerador de comandos e o auto aprimoramento **escrevem e aplicam código** no seu computador sem revisão. Use por sua conta e risco e mantenha cópias dos seus arquivos.
- Se algum token vazar, gere outro: no Telegram use `/revoke` no @BotFather.

## Problemas comuns

- **`ModuleNotFoundError`:** o ambiente virtual não está ativo (`.\venv\Scripts\activate`) ou faltou rodar o `pip install -r requirements.txt`.
- **Erro pedindo Visual C++ ao instalar:** use o Python 3.12 e instale o `resemblyzer` com `--no-deps`, como na seção de instalação.
- **Algo não funciona:** rode `python jarvis_conferir.py`. Ele testa a configuração e gera um relatório **sem senhas**.

## Contribuindo

Sugestões e correções são bem-vindas: abra uma *issue* ou um *pull request*.


