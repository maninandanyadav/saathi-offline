r"""
SAATHI - the language test matrix.

Runs every language and style through the whole pipeline and shows, for each:
  1. the profile SAATHI worked out
  2. the instruction the model was given
  3. what the model actually wrote
  4. what you would see on screen
  5. whether the style was kept

Run it from the saathi folder:
    .\venv\Scripts\python.exe tests\language_matrix.py

It uses a throwaway database and refuses to touch your real one. It needs
Ollama running, and takes a few minutes: every case is a real reply.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

WORK = Path(tempfile.mkdtemp(prefix="saathi-languages-"))
os.environ["SAATHI_DB"] = str(WORK / "languages.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from backend import auth, chat, database, letters          # noqa: E402
from backend.database import DB_PATH, DEFAULT_DB_PATH, get_connection   # noqa: E402

LINE = "-" * 78
PASSWORD = "Matrix-Pass-123"


def shape(text):
    if letters.has_telugu(text):
        return "Telugu script"
    if letters.has_devanagari(text):
        return "Devanagari"
    return "a-z letters"


def raw_reply(conversation_id):
    """What the model actually wrote, before the letters were changed."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND sender = 'saathi' "
            "ORDER BY id DESC LIMIT 1", (conversation_id,)).fetchone()
    return row["content"] if row else ""


def run_case(user_id, name, messages, expected):
    """One case: send the messages, report what happened to the last one."""
    conversation = chat.create_conversation(user_id)["id"]
    for text in messages[:-1]:                       # set the scene, quietly
        chat.add_user_message(user_id, conversation, text)
        chat.add_saathi_reply(user_id, conversation)

    asked = messages[-1]
    earlier = chat.get_conversation(user_id, conversation)["messages"]
    found = letters.profile(asked, earlier)
    instruction = letters.instruction(found)

    chat.add_user_message(user_id, conversation, asked)
    shown = chat.add_saathi_reply(user_id, conversation)["content"]
    written = raw_reply(conversation)
    kept = letters.reply_matches(found, written)

    print(f"{name}")
    if len(messages) > 1:
        for earlier_text in messages[:-1]:
            print(f"   before      : {earlier_text}")
    print(f"   you         : {asked}")
    print(f"   1 profile   : {found['style']}  ({found['confidence']} confidence"
          f"{', borrowed from the conversation' if found['from_earlier'] else ''})")
    print(f"   2 told      : {instruction[:96]}")
    if written != shown:
        print(f"   3 model     : {written[:88]}")
    print(f"   4 you see   : {shown[:88]}")
    print(f"   5 style kept: {'yes' if kept else 'NO'}   (wanted {expected}, got {shape(shown)})")
    print()
    return kept


CASES = [
    ("English",            ["How was your day?"],                       "a-z letters"),
    ("Telugu script",      ["ఈ రోజు ఎలా ఉంది?"],                          "Telugu script"),
    ("Roman Telugu",       ["Eeroju ela undi?"],                        "a-z letters"),
    ("Hindi script",       ["आज दिन कैसा था?"],                           "Devanagari"),
    ("Hinglish",           ["Aaj din kaisa tha?"],                      "a-z letters"),
    ("Telugu-English mix", ["Today college lo presentation undi"],      "a-z letters"),
    ("Hindi-English mix",  ["Aaj college mein presentation hai bro"],   "a-z letters"),
    ("Short: bro",         ["Ela unnav?", "bro"],                       "a-z letters"),
    ("Short Telugu: haa",  ["Repu exam undi, tension ga undi.", "haa"], "a-z letters"),
    ("Short Hindi: haan",  ["Aaj bahut thak gaya hoon", "haan"],        "a-z letters"),
    ("Switching language", ["Hi, how are you?", "I had a long day at college.",
                            "Ela unnav bro?"],                          "a-z letters"),
    ("A mixed conversation", ["Hi there", "Ela unnav bro?", "Aaj bahut thak gaya hoon",
                              "Today college lo presentation undi",
                              "I think I will sleep early."],           "a-z letters"),
]


def main():
    if DB_PATH.resolve() == DEFAULT_DB_PATH.resolve():
        sys.exit("Refusing to run: this would use your REAL database.")
    print(f"SAATHI language matrix, on a throwaway database:\n  {DB_PATH}\n")
    print(LINE)

    database.init_db()
    auth.create_user("matrix", PASSWORD, PASSWORD)
    user_id = auth.login_user("matrix", PASSWORD)

    kept = 0
    for name, messages, expected in CASES:
        kept += run_case(user_id, name, messages, expected)

    print(LINE)
    print(f"{kept} of {len(CASES)} kept the style that was asked for")
    print()
    print("Language detection is a guess, not a certainty. Short messages are")
    print("marked low confidence and borrow the conversation's language; a reply")
    print("that comes back in the wrong language is asked for once more.")
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
