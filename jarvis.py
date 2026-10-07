"""
Jarvis - Passo 1: chat só de texto no terminal.

Antes de rodar:
    pip install anthropic
    Defina a variável de ambiente ANTHROPIC_API_KEY com a sua chave.
"""

import anthropic

MODELO = "claude-sonnet-5-5"

PERSONALIDADE = (
    "Você é o Jarvis, o assistente de IA de Tony Stark: educado, elegante, "
    "eficiente e com um toque sutil de humor britânico. Chame o usuário de "
    "'senhor' ou 'senhora'. Responda sempre em português do Brasil, de forma "
    "curta e direta."
)

# Lê a chave automaticamente da variável ANTHROPIC_API_KEY
client = anthropic.Anthropic()

# Guarda a conversa para o Jarvis lembrar do que já foi dito
historico = []


def perguntar(texto: str) -> str:
    historico.append({"role": "user", "content": texto})

    resposta = client.messages.create(
        model=MODELO,
        max_tokens=400,
        system=PERSONALIDADE,
        messages=historico,
    )

    texto_resposta = resposta.content[0].text
    historico.append({"role": "assistant", "content": texto_resposta})
    return texto_resposta


def main():
    print("Jarvis online. Digite 'sair' para encerrar.\n")

    while True:
        entrada = input("Você: ").strip()

        if not entrada:
            continue

        if entrada.lower() in ("sair", "desligar", "exit"):
            print("Jarvis: Até logo, senhor.")
            break

        try:
            print("Jarvis:", perguntar(entrada), "\n")
        except anthropic.AuthenticationError:
            print("Jarvis: Chave da API inválida ou não configurada.")
            break
        except Exception as erro:
            # Remove a última pergunta para não corromper o histórico
            historico.pop()
            print(f"Jarvis: Tive um problema ({erro}). Tente de novo.\n")


if __name__ == "__main__":
    main()