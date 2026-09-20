"""
SAATHI - Step 4
A conversation loop: keep talking until you say goodbye.

Note: this version has NO memory yet. Every message is sent alone.
"""

import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "llama3.2:3b"


def ask(question):
    """Send one question to the local model and return its answer as text."""
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": question,
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]


print("SAATHI is here. Type 'bye' when you want to stop.\n")

while True:
    you = input("You: ").strip()

    if you.lower() in ("bye", "quit", "exit"):
        print("\nSAATHI: Take care. I'm here whenever you need me.")
        break

    if not you:
        continue

    try:
        print("\nSAATHI:", ask(you), "\n")
    except requests.exceptions.ConnectionError:
        print("\nERROR: Could not reach Ollama. Is it running?\n")
