"""
SAATHI - Step 3
The first bridge: Python talking to the local AI model.
"""

import requests

# 127.0.0.1 means "this computer, right here". Nothing leaves your laptop.
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


question = "Say hello, then tell me one interesting fact about the moon."

print("Question:", question)
print("Thinking...\n")

try:
    print(ask(question))
except requests.exceptions.ConnectionError:
    print("ERROR: Could not reach Ollama.")
    print("Is it running? Open the Start menu, launch Ollama, then try again.")
