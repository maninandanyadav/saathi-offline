"""
SAATHI - Step 5
Now with short-term memory: SAATHI remembers the whole conversation.
"""

import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "llama3.2:3b"


def ask(messages):
    """Send the whole conversation so far and return SAATHI's next reply."""
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


# This list IS the memory. It starts empty.
conversation = []

print("SAATHI is here. Type 'bye' when you want to stop.\n")

while True:
    you = input("You: ").strip()

    if you.lower() in ("bye", "quit", "exit"):
        print("\nSAATHI: Take care. I'm here whenever you need me.")
        break

    if not you:
        continue

    # Remember what you said.
    conversation.append({"role": "user", "content": you})

    try:
        reply = ask(conversation)
    except requests.exceptions.ConnectionError:
        print("\nERROR: Could not reach Ollama. Is it running?\n")
        conversation.pop()  # drop the message that never got answered
        continue

    # Remember what SAATHI said.
    conversation.append({"role": "assistant", "content": reply})

    print("\nSAATHI:", reply, "\n")
