r"""
SAATHI - the checks.

Runs every account, chat, security and AI-context check against a THROWAWAY
database, so your real accounts and conversations are never touched.
(The AI checks need Ollama running, as SAATHI does.)

Run it from the saathi folder:
    .\venv\Scripts\python.exe tests\chat_checks.py

Everything passing ends with "ALL CHECKS PASSED". Anything that fails is
marked [FAIL] with the reason - so after any future change (like connecting
the AI in Step 6) you can run this again and know nothing broke.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import threading
import time
from pathlib import Path

# 1. Point SAATHI at a throwaway database BEFORE any of its code is loaded.
WORK_DIR = Path(tempfile.mkdtemp(prefix="saathi-checks-"))
os.environ["SAATHI_DB"] = str(WORK_DIR / "checks.db")

# 2. Make "import backend" work however this file is started, and let
#    Telugu and Hindi print on a Windows console.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402  (imported after the setup above, on purpose)
import uvicorn   # noqa: E402

from backend import ai, chat, context, letters  # noqa: E402
from backend.database import DB_PATH, DEFAULT_DB_PATH, get_connection  # noqa: E402
from backend.main import app  # noqa: E402

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "Check-Pass-123"

results = []   # (section, label, passed, detail)
current_section = ""


def section(name):
    global current_section
    current_section = name


def check(label, passed, detail=""):
    results.append((current_section, label, bool(passed), detail))


# ---------------------------------------------------------------- helpers

def new_user(name):
    """Create an account and return a session that's logged in as it."""
    session = requests.Session()
    session.post(f"{BASE}/api/signup",
                 json={"saathi_id": name, "password": PASSWORD, "confirm_password": PASSWORD})
    r = session.post(f"{BASE}/api/login", json={"saathi_id": name, "password": PASSWORD})
    assert r.status_code == 200, f"could not log in as {name}: {r.text}"
    return session


def conversation_ids(session):
    return {c["id"] for c in session.get(f"{BASE}/api/conversations").json()}


def send(session, conversation_id, text, reply_to=None):
    return session.post(f"{BASE}/api/conversations/{conversation_id}/messages",
                        json={"content": text, "reply_to_message_id": reply_to})


def ask_reply(session, conversation_id):
    return session.post(f"{BASE}/api/conversations/{conversation_id}/reply")


def messages_in(session, conversation_id):
    return session.get(f"{BASE}/api/conversations/{conversation_id}").json()["messages"]


def written_in(text, first, last):
    """Does this text use letters from one script? (Telugu, Devanagari...)"""
    return any(first <= ord(letter) <= last for letter in text)


TELUGU = (0x0C00, 0x0C7F)
DEVANAGARI = (0x0900, 0x097F)
ANY_INDIAN_SCRIPT = (0x0900, 0x0D7F)


def file_contains(text):
    """Is this text anywhere in the raw bytes of the database file?"""
    return text.encode("utf-8") in DB_PATH.read_bytes()


# ---------------------------------------------------------------- the checks

def run_checks():
    anyone = requests.Session()   # never logs in
    a = new_user("user_a")
    b = new_user("user_b")

    section("ACCOUNTS AND LOGIN (Step 4 still works)")
    r = anyone.get(f"{BASE}/home", allow_redirects=False)
    check("Chat page without logging in -> sent to login",
          r.status_code == 303 and r.headers.get("location") == "/", r.status_code)
    check("Chat data without logging in -> refused",
          anyone.get(f"{BASE}/api/conversations").status_code == 401)
    check("Chat page when logged in -> opens",
          a.get(f"{BASE}/home", allow_redirects=False).status_code == 200)
    check("'Who am I' shares only the name and join date, never a password hash",
          set(a.get(f"{BASE}/api/me").json()) == {"saathi_id", "member_since"})

    section("CONVERSATIONS")
    r = a.post(f"{BASE}/api/conversations")
    first = r.json()
    check("Create a new conversation", r.status_code == 201, r.status_code)
    check("Only the needed fields are sent back (no owner id)",
          set(first) == {"id", "title", "created_at", "updated_at"}, sorted(first))
    second = a.post(f"{BASE}/api/conversations").json()["id"]
    newest_first = [c["id"] for c in a.get(f"{BASE}/api/conversations").json()]
    check("Create another conversation (newest shows first)",
          newest_first[:2] == [second, first["id"]], newest_first)
    first = first["id"]

    section("SENDING MESSAGES")
    r = send(a, first, "  How was your day?  ")
    m1 = r.json()
    check("Send a message", r.status_code == 201 and m1["sender"] == "user", r.status_code)
    check("Spaces around it are trimmed", m1["content"] == "How was your day?", repr(m1["content"]))
    check("Message fields sent back: no owner, no hidden timestamps",
          set(m1) == {"id", "sender", "content", "created_at", "saved", "reply_to"}, sorted(m1))
    r = ask_reply(a, first)
    s1 = r.json()
    check("SAATHI replies, from the local AI",
          r.status_code == 201 and s1["sender"] == "saathi"
          and len(s1.get("content", "").strip()) > 1
          and "connected in the next phase" not in s1.get("content", ""),   # the old placeholder
          (s1.get("content") or "")[:60])
    check("Asking for a second reply to the same message is refused",
          ask_reply(a, first).status_code == 409)

    section("REFRESH, REOPEN, SWITCH")
    check("Refresh the page -> the messages are still there",
          [m["id"] for m in messages_in(a, first)] == [m1["id"], s1["id"]])
    send(a, second, "Something in the other conversation")
    check("Switch between conversations -> each shows only its own messages",
          [m["content"] for m in messages_in(a, second)] == ["Something in the other conversation"]
          and all(m["content"] != "Something in the other conversation" for m in messages_in(a, first)))
    check("Close and reopen -> same messages, same order",
          [m["id"] for m in messages_in(a, first)] == [m1["id"], s1["id"]])
    check("The conversation with new activity moves to the top",
          a.get(f"{BASE}/api/conversations").json()[0]["id"] == second)

    section("EMPTY AND LONG MESSAGES")
    for label, text in [("An empty message", ""), ("Only spaces", "     "), ("Only line breaks", "\n\n\n")]:
        r = send(a, first, text)
        check(f"{label} is refused", r.status_code == 400, r.status_code)
    check("4,000 characters is accepted", send(a, first, "x" * 4000).status_code == 201)
    r = send(a, first, "x" * 4001)
    check("4,001 characters is refused with a clear reason", r.status_code == 400, r.json().get("detail"))
    long_link = "https://example.com/" + "a" * 2500
    r = send(a, first, long_link)
    check("A very long unbroken link is stored exactly", r.status_code == 201 and r.json()["content"] == long_link)
    check("A request with no message at all is refused",
          a.post(f"{BASE}/api/conversations/{first}/messages", json={}).status_code == 422)

    section("TRICKY TEXT IS STORED AS PLAIN TEXT")
    for label, text in [("Telugu, Hindi and an emoji", "ఈరోజు నాకు బాగోలేదు 😔 आज मन उदास है"),
                        ("A <script> tag", "<script>alert('hi')</script>"),
                        ("An SQL-injection attempt", "'); DROP TABLE messages; --")]:
        r = send(a, first, text)
        check(f"{label} comes back exactly as typed", r.status_code == 201 and r.json()["content"] == text)
    check("...and the messages table is still there", len(messages_in(a, first)) > 0)

    section("REPLIES")
    r = send(a, first, "Honestly, it was difficult.", reply_to=m1["id"])
    reply_to_user = r.json()
    check("Reply to a user message",
          r.status_code == 201 and reply_to_user["reply_to"]["available"]
          and reply_to_user["reply_to"]["sender"] == "user", reply_to_user.get("reply_to"))
    r = send(a, first, "Thank you for asking.", reply_to=s1["id"])
    check("Reply to a SAATHI message", r.status_code == 201 and r.json()["reply_to"]["sender"] == "saathi")
    r = send(a, first, "A normal message after cancelling a reply.")
    check("Cancel a reply -> the message goes out with no link", r.status_code == 201 and r.json()["reply_to"] is None)
    elsewhere = messages_in(a, second)[0]["id"]
    r = send(a, first, "x", reply_to=elsewhere)
    check("A reply pointing into a DIFFERENT conversation is refused", r.status_code == 400, r.status_code)

    section("DELETE THE ORIGINAL OF A REPLY")
    check("Delete the original message",
          a.delete(f"{BASE}/api/conversations/{first}/messages/{m1['id']}").status_code == 204)
    page = a.get(f"{BASE}/api/conversations/{first}")
    reply_now = next(m for m in page.json()["messages"] if m["id"] == reply_to_user["id"])
    check("The reply now says 'Original message unavailable'",
          reply_now["reply_to"] == {"id": m1["id"], "available": False}, reply_now["reply_to"])
    check("The deleted words appear nowhere in the answer", "How was your day?" not in page.text)
    check("The deleted words are gone from the database FILE too", not file_contains("How was your day?"))
    check("Deleting it a second time -> not found",
          a.delete(f"{BASE}/api/conversations/{first}/messages/{m1['id']}").status_code == 404)

    section("SAVE AND RENAME")
    r = a.put(f"{BASE}/api/conversations/{first}/messages/{s1['id']}/saved", json={"saved": True})
    check("Save (star) a message", r.status_code == 200 and r.json()["saved"] is True)
    check("The star survives a refresh",
          next(m for m in messages_in(a, first) if m["id"] == s1["id"])["saved"] is True)
    r = a.patch(f"{BASE}/api/conversations/{first}", json={"title": "  A   difficult day  "})
    check("Rename a conversation (extra spaces tidied)",
          r.status_code == 200 and r.json()["title"] == "A difficult day", r.json().get("title"))
    check("An empty name is refused",
          a.patch(f"{BASE}/api/conversations/{first}", json={"title": "   "}).status_code == 400)

    section("USER A vs USER B - COMPLETELY SEPARATE")
    b_conv = b.post(f"{BASE}/api/conversations").json()["id"]
    b_msg = send(b, b_conv, "B's private words").json()["id"]
    a_ids, b_ids = conversation_ids(a), conversation_ids(b)
    check("A's list and B's list share nothing", a_ids and b_ids and not (a_ids & b_ids),
          f"A={sorted(a_ids)} B={sorted(b_ids)}")
    attempts = {
        "open B's conversation": a.get(f"{BASE}/api/conversations/{b_conv}"),
        "send a message into it": send(a, b_conv, "sneaking in"),
        "ask for a SAATHI reply in it": ask_reply(a, b_conv),
        "rename it": a.patch(f"{BASE}/api/conversations/{b_conv}", json={"title": "hacked"}),
        "star B's message": a.put(f"{BASE}/api/conversations/{b_conv}/messages/{b_msg}/saved",
                                  json={"saved": True}),
        "star B's message through A's own conversation":
            a.put(f"{BASE}/api/conversations/{first}/messages/{b_msg}/saved", json={"saved": True}),
        "delete B's message": a.delete(f"{BASE}/api/conversations/{b_conv}/messages/{b_msg}"),
        "delete B's message through A's own conversation":
            a.delete(f"{BASE}/api/conversations/{first}/messages/{b_msg}"),
        "delete B's whole conversation": a.delete(f"{BASE}/api/conversations/{b_conv}"),
    }
    for label, r in attempts.items():
        check(f"A tries to {label} -> not found", r.status_code == 404, r.status_code)
    check("A tries to reply to B's message -> refused", send(a, first, "x", reply_to=b_msg).status_code == 400)
    b_view = b.get(f"{BASE}/api/conversations/{b_conv}").json()
    check("...and B's conversation is completely unchanged",
          b_view["conversation"]["title"] == "New conversation"
          and [(m["content"], m["saved"]) for m in b_view["messages"]] == [("B's private words", False)])
    check("B's words never appeared in any answer to A",
          all("B's private words" not in r.text for r in attempts.values()))
    theirs = a.get(f"{BASE}/api/conversations/{b_conv}")
    nothing = a.get(f"{BASE}/api/conversations/999999")
    check("Someone else's conversation looks exactly like one that doesn't exist",
          (theirs.status_code, theirs.json()) == (nothing.status_code, nothing.json()))
    with get_connection() as c:
        b_user_id = c.execute("SELECT id FROM users WHERE saathi_id = 'user_b'").fetchone()[0]
    sneaky = a.post(f"{BASE}/api/conversations", json={"user_id": b_user_id}).json()["id"]
    check("A sends B's user id while creating a conversation -> ignored, it stays A's",
          sneaky in conversation_ids(a) and sneaky not in conversation_ids(b))

    section("DELETE A WHOLE CONVERSATION")
    send(a, second, "UNIQUE-DELETE-MARKER " * 5)
    check("Delete a conversation", a.delete(f"{BASE}/api/conversations/{second}").status_code == 204)
    check("Opening it now -> not found", a.get(f"{BASE}/api/conversations/{second}").status_code == 404)
    check("It's gone from the list", second not in conversation_ids(a))
    with get_connection() as c:
        left = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (second,)).fetchone()[0]
    check("All its messages are gone from the database", left == 0, left)
    check("...and from the database FILE's raw bytes", not file_contains("UNIQUE-DELETE-MARKER"))

    section("WHAT THE AI IS GIVEN (conversation context)")
    talk = [{"sender": "user", "content": "My exam is tomorrow."},
            {"sender": "saathi", "content": "That sounds stressful."},
            {"sender": "user", "content": "I'm nervous about it."}]
    given = context.to_ai_messages(talk)
    check("The whole recent conversation is sent, not just the last message", len(given) == 3, len(given))
    check("SAATHI's own past replies are marked as the assistant",
          [m["role"] for m in given] == ["user", "assistant", "user"], [m["role"] for m in given])
    check("Oldest first, newest last, like reading",
          given[0]["content"] == talk[0]["content"] and given[-1]["content"] == talk[-1]["content"])

    huge = [{"sender": "user", "content": f"{i:02d} " + "x" * 497} for i in range(30)]
    trimmed = context.to_ai_messages(huge)
    spent = sum(context.estimate_tokens(m["content"]) for m in trimmed)
    check("A very long conversation is trimmed to the budget",
          spent <= context.CONTEXT_TOKENS, f"{spent} of {context.CONTEXT_TOKENS} tokens")
    check("...and it keeps the NEWEST messages, dropping the oldest",
          trimmed[-1]["content"].startswith("29") and not trimmed[0]["content"].startswith("00"))
    check("One huge message still gets through on its own",
          len(context.to_ai_messages([{"sender": "user", "content": "y" * 20000}])) == 1)
    check("An empty conversation doesn't break it", context.to_ai_messages([]) == [])

    section("COUNTING THE COST (tokens, not characters)")
    english = "I had a really long day at college today and I am very tired now."
    telugu = "ఈ రోజు కాలేజీలో చాలా అలసటగా అనిపించింది."
    check("Telugu costs more per letter than English",
          context.estimate_tokens(telugu) / len(telugu)
          > context.estimate_tokens(english) / len(english))
    check("A mixed message is counted letter by letter",
          context.estimate_tokens("Ela unnavu? ఈ రోజు బాగుంది.")
          > context.estimate_tokens("Ela unnavu? today is fine."))
    check("An empty message costs only the who-said-it marks",
          context.estimate_tokens("") == context.PER_MESSAGE_COST)

    def prompt_tokens(text):
        """Ask the model itself how many tokens a message really costs."""
        body = {"model": ai.MODEL, "stream": False, "keep_alive": ai.KEEP_LOADED,
                "options": {"num_predict": 1, "num_ctx": context.CONTEXT_TOKENS},
                # a unique marker, so Ollama's memory of earlier prompts
                # can never make this count come out low
                "messages": [{"role": "user", "content": uuid.uuid4().hex + text}]}
        return requests.post(ai.OLLAMA_URL, json=body, timeout=120).json()["prompt_eval_count"]

    overhead = prompt_tokens("")
    for name, sample in (("English", english), ("Telugu", telugu)):
        guess = context.estimate_tokens(sample)
        really = prompt_tokens(sample) - overhead
        check(f"The guess is never below what the model really uses ({name})",
              guess >= really, f"guessed {guess}, really {really}")

    telugu_chat = [{"sender": "user", "content": telugu} for _ in range(200)]
    kept = context.to_ai_messages(telugu_chat)
    spent = sum(context.estimate_tokens(m["content"]) for m in kept)
    check("A long Telugu conversation is trimmed by tokens, not letters",
          spent <= context.CONTEXT_TOKENS, f"{spent} of {context.CONTEXT_TOKENS} tokens")
    check("...and it still keeps a useful number of messages", len(kept) > 5, f"{len(kept)} messages")

    section("REPLYING TO ONE MESSAGE (what the AI is told)")
    pair = [{"role": "user", "content": "older"}, {"role": "user", "content": "That part was hard."}]
    note = context.with_reply_context(pair, {"available": True, "sender": "saathi",
                                          "content": "How was your presentation?"})
    check("The quoted message is attached to the NEWEST message",
          "How was your presentation?" in note[-1]["content"]
          and note[-1]["content"].endswith("That part was hard."))
    check("Earlier messages are left untouched", note[0] == pair[0])
    check("It says whose message it was",
          "of yours" in note[-1]["content"]
          and "of their own" in context.with_reply_context(
              pair, {"available": True, "sender": "user", "content": "x"})[-1]["content"])
    deleted = context.with_reply_context(pair, {"id": 7, "available": False})[-1]["content"]
    check("A deleted original says so, and carries no words from it",
          "Original message unavailable" in deleted and len(deleted.splitlines()[0]) < 100, deleted.splitlines()[0])
    check("A message that isn't a reply is left alone", context.with_reply_context(pair, None) == pair)
    long_note = context.with_reply_context(
        pair, {"available": True, "sender": "user", "content": "long " * 400})[-1]["content"].splitlines()[0]
    check("A very long original is shortened", len(long_note) < context.QUOTE_LENGTH + 80, f"{len(long_note)} characters")

    section("STREAMING (words arrive as they are written)")
    live = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, live, "Say hello in one short sentence.")
    chunks, saved, status = [], None, None
    with a.post(f"{BASE}/api/conversations/{live}/reply/stream", stream=True, timeout=300) as r:
        status = r.status_code
        for line in r.iter_lines():
            if not line:
                continue
            part = json.loads(line)
            if "chunk" in part:
                chunks.append(part["chunk"])
            elif "message" in part:
                saved = part["message"]
    check("The reply arrives as many small pieces", status == 200 and len(chunks) > 1, f"{len(chunks)} pieces")
    check("The finished message comes at the end", saved is not None and saved["sender"] == "saathi")
    check("What was streamed is exactly what was saved",
          bool(saved) and saved["content"] == "".join(chunks).strip())
    check("...and it is really in the conversation afterwards",
          bool(saved) and messages_in(a, live)[-1]["id"] == saved["id"])

    section("WHEN THE AI IS UNAVAILABLE (kind words, not crashes)")
    kept = (ai.OLLAMA_URL, ai.MODEL, ai.REPLY_TIMEOUT, ai.ask_ollama)
    broken = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, broken, "Are you there?")
    try:
        ai.OLLAMA_URL = "http://127.0.0.1:1/api/chat"        # nothing is listening there
        r = ask_reply(a, broken)
        check("AI not running -> 503 and a kind message",
              r.status_code == 503 and r.json()["detail"] == ai.CANT_REACH, r.status_code)
        check("...and the person's own message is still saved",
              [m["content"] for m in messages_in(a, broken)] == ["Are you there?"])
        ai.OLLAMA_URL = kept[0]

        ai.MODEL = "this-model-does-not-exist:1b"
        r = ask_reply(a, broken)
        check("Model missing -> tells them how to install it",
              r.status_code == 503 and r.json()["detail"] == ai.NO_MODEL, r.json().get("detail", "")[:45])
        ai.MODEL = kept[1]

        ai.REPLY_TIMEOUT = 0.5                                # far too little time to think
        r = ask_reply(a, broken)
        check("Thinking too long -> asks them to try again",
              r.status_code == 503 and r.json()["detail"] == ai.TOO_SLOW, r.json().get("detail", "")[:45])
        ai.REPLY_TIMEOUT = kept[2]

        empty = type("Empty", (), {"status_code": 200, "json": lambda self: {"message": {"content": "   "}}})
        ai.ask_ollama = lambda messages, stream, background=None: empty()
        r = ask_reply(a, broken)
        check("Empty reply -> asks them to try again",
              r.status_code == 503 and r.json()["detail"] == ai.NO_WORDS, r.json().get("detail", "")[:45])
        ai.ask_ollama = kept[3]

        ai.MODEL = "gemma3:4b-cloud"
        r = ask_reply(a, broken)
        check("A cloud model is refused outright",
              r.status_code == 503 and "local models" in r.json()["detail"], r.json().get("detail", "")[:45])
    finally:
        ai.OLLAMA_URL, ai.MODEL, ai.REPLY_TIMEOUT, ai.ask_ollama = kept

    check("None of these messages leak technical details",
          all("Traceback" not in m and "http" not in m
              for m in (ai.CANT_REACH, ai.TOO_SLOW, ai.NO_MODEL, ai.NO_MEMORY, ai.NO_WORDS, ai.UNKNOWN)))

    section("NOTHING LEAVES THIS COMPUTER")
    backend_code = " ".join(f.read_text(encoding="utf-8")
                            for f in (Path(__file__).resolve().parent.parent / "backend").glob("*.py"))
    addresses = []
    for word in backend_code.split():
        for scheme in ("http://", "https://"):
            if scheme in word:
                addresses.append(word[word.index(scheme):].strip(chr(34) + chr(39) + "),"))
    outside = [where for where in addresses
               if not where.startswith(("http://127.0.0.1", "http://localhost"))]
    check("Every web address in SAATHI's code points at this computer",
          not outside, ", ".join(outside[:3]))
    check("The AI is reached at 127.0.0.1 (this computer)",
          ai.OLLAMA_URL.startswith("http://127.0.0.1:"), ai.OLLAMA_URL)
    check("The model is a local one, not a cloud one", "cloud" not in ai.MODEL, ai.MODEL)
    check("No API key or token is stored in the code",
          not re.search("api[_-]?key|bearer |sk-[A-Za-z0-9]{8}", backend_code, re.I))

    section("SAME LANGUAGE BACK (the real model answers)")
    telugu_chat = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, telugu_chat, "ఈ రోజు నాకు చాలా అలసటగా ఉంది.")
    r = ask_reply(a, telugu_chat)
    telugu_reply = r.json().get("content", "") if r.status_code == 201 else ""
    check("Telugu in -> Telugu back", written_in(telugu_reply, *TELUGU), telugu_reply[:40])

    hindi_chat = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, hindi_chat, "आज मैं बहुत थक गया हूँ।")
    r = ask_reply(a, hindi_chat)
    hindi_reply = r.json().get("content", "") if r.status_code == 201 else ""
    check("Hindi in -> Hindi back", written_in(hindi_reply, *DEVANAGARI), hindi_reply[:40])

    english_chat = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, english_chat, "I am feeling tired today.")
    r = ask_reply(a, english_chat)
    english_reply = r.json().get("content", "") if r.status_code == 201 else ""
    check("English in -> English back, with no other script",
          bool(english_reply) and not written_in(english_reply, *ANY_INDIAN_SCRIPT), english_reply[:40])

    section("A VERY LONG MESSAGE STILL GETS A REPLY")
    long_chat = a.post(f"{BASE}/api/conversations").json()["id"]
    long_text = ("There is a lot on my mind today and I want to write all of it down. " * 60)[:4000]
    check("A 4,000-character message is accepted", send(a, long_chat, long_text).status_code == 201)
    r = ask_reply(a, long_chat)
    check("...and SAATHI still answers it",
          r.status_code == 201 and len(r.json().get("content", "")) > 0, r.status_code)

    section("RESTART SAATHI (nothing is lost)")
    conversations_before = conversation_ids(a)
    messages_before = [m["content"] for m in messages_in(a, telugu_chat)]
    stop_server()
    start_server()
    check("Every conversation is still there after a restart",
          conversation_ids(a) == conversations_before)
    check("The messages are still there, in the same order",
          [m["content"] for m in messages_in(a, telugu_chat)] == messages_before)
    check("You are still logged in (the session survived)",
          a.get(f"{BASE}/api/me").status_code == 200)
    send(a, telugu_chat, "Are you still here?")
    r = ask_reply(a, telugu_chat)
    check("SAATHI can still reply after the restart",
          r.status_code == 201 and len(r.json().get("content", "")) > 0, r.status_code)

    section("TRY AGAIN AFTER A FAILURE")
    recover = a.post(f"{BASE}/api/conversations").json()["id"]
    send(a, recover, "Are you there?")
    kept_url = ai.OLLAMA_URL
    try:
        ai.OLLAMA_URL = "http://127.0.0.1:1/api/chat"        # nothing is listening there
        ask_reply(a, recover)
        complaint, stream_status = None, None
        with a.post(f"{BASE}/api/conversations/{recover}/reply/stream", stream=True, timeout=60) as r:
            stream_status = r.status_code
            for line in r.iter_lines():
                if line:
                    part = json.loads(line)
                    if "error" in part:
                        complaint = part["error"]
        check("Streaming fails kindly too, instead of crashing",
              stream_status == 200 and complaint == ai.CANT_REACH, str(complaint)[:45])
    finally:
        ai.OLLAMA_URL = kept_url
    check("The failed attempts saved nothing",
          [m["sender"] for m in messages_in(a, recover)] == ["user"])
    r = ask_reply(a, recover)
    check("Try again, once the AI is back -> a real reply",
          r.status_code == 201 and len(r.json().get("content", "")) > 0, r.status_code)
    check("...and only ONE reply was kept in the end",
          [m["sender"] for m in messages_in(a, recover)] == ["user", "saathi"])

    section("LONG CONVERSATIONS (the notes SAATHI keeps)")
    one_message = [{"sender": "user", "content": "hello"}]
    notes_text = "They are building a project called SAATHI with their friend Ravi."
    plan = context.build(one_message, None, notes_text)
    check("The notes travel beside the messages, never as one of them",
          plan["background"] == notes_text
          and all("SAATHI" not in m["content"] for m in plan["messages"]))
    check("No notes means nothing extra is sent", context.build(one_message)["background"] is None)

    many = [{"sender": "user", "content": "y" * 200} for _ in range(40)]
    check("Long notes leave less room for messages",
          len(context.build(many, None, "x" * 4000)["messages"]) < len(context.build(many)["messages"]))

    with get_connection() as connection:
        user_a_id = connection.execute(
            "SELECT id FROM users WHERE saathi_id = 'user_a'").fetchone()["id"]
        user_b_id = connection.execute(
            "SELECT id FROM users WHERE saathi_id = 'user_b'").fetchone()["id"]

    long_chat = chat.create_conversation(user_a_id)["id"]
    chat.add_user_message(user_a_id, long_chat,
                          "My project is called ZEPHYR-9 and I build it with my friend Ravi.")
    chat.save_saathi_message(user_a_id, long_chat, "That sounds like a lot of work.")
    check("A short conversation needs no notes",
          chat.update_summary_if_needed(user_a_id, long_chat) is None)

    for number in range(30):      # push those first messages far out of sight
        chat.add_user_message(user_a_id, long_chat, f"Day {number}: classes were long today.")
        chat.save_saathi_message(user_a_id, long_chat, f"That sounds tiring, day {number}.")

    check("A stranger cannot make notes on someone else's conversation",
          chat.update_summary_if_needed(user_b_id, long_chat) is None)

    written = chat.update_summary_if_needed(user_a_id, long_chat)
    check("Once it grows, the notes get written", bool(written), str(written)[:40])
    check("The notes keep a fact that has scrolled out of sight",
          bool(written) and "ZEPHYR" in written.upper(), str(written)[:60])

    chat.add_user_message(user_a_id, long_chat, "What was my project called?")
    grown = chat.prepare_reply(user_a_id, long_chat)
    sent_now = " ".join(m["content"] for m in grown["messages"])
    check("...a fact the recent messages no longer carry on their own",
          "ZEPHYR" not in sent_now.upper()
          or any("ZEPHYR" in m["content"].upper() for m in grown["recalled"]))
    check("...and the notes are handed to the AI with the next reply",
          grown["background"] == written)

    check("The notes are written only once, not on every message",
          chat.update_summary_if_needed(user_a_id, long_chat) is None)

    with get_connection() as connection:
        a_message = connection.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id LIMIT 1",
            (long_chat,)).fetchone()["id"]
    chat.delete_message(user_a_id, long_chat, a_message)
    with get_connection() as connection:
        after_delete = connection.execute(
            "SELECT summary FROM conversations WHERE id = ?", (long_chat,)).fetchone()["summary"]
    check("Deleting a message throws the notes away with it", after_delete is None, str(after_delete)[:40])

    section("BRINGING BACK AN OLDER MESSAGE (7D)")
    check("Everyday words are not matched on",
          context.meaningful_words("What was that about the thing") == set(),
          context.meaningful_words("What was that about the thing"))
    check("Real words are kept, punctuation ignored",
          context.meaningful_words("My viva is in room B-214!") == {"viva", "room", "214"},
          context.meaningful_words("My viva is in room B-214!"))
    check("Telugu words are matched too",
          "ప్రాజెక్ట్" in context.meaningful_words("నా ప్రాజెక్ట్ పేరు సాథి"))

    older_messages = [
        {"id": 1, "sender": "user", "content": "I lent my Data Structures book to Sravani."},
        {"id": 2, "sender": "saathi", "content": "Got it, I will remember that."},
        {"id": 3, "sender": "user", "content": "My viva is in room B-214 with Professor Meena."},
        {"id": 4, "sender": "user", "content": "The weather was nice today."},
    ]
    found = context.find_relevant("Who did I lend my Data Structures book to?", older_messages)
    check("The message that shares real words is found",
          bool(found) and found[0]["id"] == 1, [m["id"] for m in found])
    check("One shared word alone is not enough",
          context.find_relevant("Tell me about the weather", older_messages) == [],
          [m["id"] for m in context.find_relevant("Tell me about the weather", older_messages)])
    check("A question about nothing in particular brings nothing back",
          context.find_relevant("okay thanks", older_messages) == [])
    check("At most two messages come back",
          len(context.find_relevant("book room viva Sravani Data Structures Meena",
                                    older_messages)) <= context.RECALL_MESSAGES)

    pair = [{"role": "user", "content": "older one"}, {"role": "user", "content": "Which room?"}]
    noted = context.with_recalled(pair, [older_messages[2]])
    check("It is attached to the NEWEST message only",
          "B-214" in noted[-1]["content"] and "B-214" not in noted[0]["content"])
    check("It says who said it", "They said earlier" in noted[-1]["content"])
    check("SAATHI's own older words are marked as its own",
          "You said earlier" in context.with_recalled(pair, [older_messages[1]])[-1]["content"])
    long_one = [{"id": 9, "sender": "user", "content": "viva room " + "z" * 3000}]
    check("A very long older message is shortened",
          len(context.with_recalled(pair, long_one)[-1]["content"]) < 700,
          len(context.with_recalled(pair, long_one)[-1]["content"]))
    check("Nothing is brought back when there is nothing older",
          context.build([{"sender": "user", "content": "Which room?"}])["recalled"] == [])

    # end to end, through the database, with no AI needed
    recall_chat = chat.create_conversation(user_a_id)["id"]
    chat.add_user_message(user_a_id, recall_chat,
                          "The hostel wifi password is bluepeak77 by the way.")
    chat.save_saathi_message(user_a_id, recall_chat, "Noted.")
    for number in range(40):
        chat.add_user_message(user_a_id, recall_chat, f"Day {number}: nothing much happened.")
        chat.save_saathi_message(user_a_id, recall_chat, f"Quiet day {number}, then.")
    chat.add_user_message(user_a_id, recall_chat, "What is the hostel wifi password again?")

    recall_plan = chat.prepare_reply(user_a_id, recall_chat)
    sent_text = " ".join(m["content"] for m in recall_plan["messages"])
    check("An old message is brought back out of the database",
          any("bluepeak77" in m["content"] for m in recall_plan["recalled"]),
          [m["content"][:40] for m in recall_plan["recalled"]])
    check("...and it reaches what the AI is told", "bluepeak77" in sent_text)

    # the same conversation, with that old message deleted
    with get_connection() as connection:
        secret_id = connection.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id LIMIT 1",
            (recall_chat,)).fetchone()["id"]
    chat.delete_message(user_a_id, recall_chat, secret_id)
    after_delete_plan = chat.prepare_reply(user_a_id, recall_chat)
    check("A deleted message is never brought back",
          all("bluepeak77" not in m["content"] for m in after_delete_plan["messages"]))

    section("TELUGU IN THE LETTERS YOU WRITE IN")
    check("Telugu script becomes a-z letters",
          letters.to_english_letters("చాలా బాధగా ఉంది") == "chala badhaga undi",
          letters.to_english_letters("చాలా బాధగా ఉంది"))
    check("The dot is n before most sounds, m at the end of a word",
          letters.to_english_letters("ఉంది") == "undi"
          and letters.to_english_letters("సిద్ధం") == "siddham",
          letters.to_english_letters("ఉంది") + " / " + letters.to_english_letters("సిద్ధం"))
    check("English inside a Telugu sentence is left alone",
          "exam" in letters.to_english_letters("exam గురించి"))
    check("A message with no Telugu is untouched",
          letters.to_english_letters("I am fine, thanks.") == "I am fine, thanks.")

    check("Telugu typed in a-z letters is recognised",
          letters.wants_english_letters("Repu exam undi, konchem nervous ga undi."))
    check("Telugu script is left as script",
          not letters.wants_english_letters("ఈ రోజు కష్టంగా ఉంది"))
    check("Plain English is not mistaken for Telugu",
          not letters.wants_english_letters("I had a long day at college."))

    typed_in_english = [{"sender": "user", "content": "Ela unnavu?"},
                        {"sender": "saathi", "content": "నేను బాగున్నాను."}]
    check("Someone who types in a-z letters is shown a-z letters",
          letters.person_writes_in_english(typed_in_english))
    check("...and SAATHI's Telugu is converted for them",
          chat.shown_as_they_write(typed_in_english)[1]["content"] == "nenu bagunnanu.",
          chat.shown_as_they_write(typed_in_english)[1]["content"])
    check("...while their own words are never touched",
          chat.shown_as_they_write(typed_in_english)[0]["content"] == "Ela unnavu?")

    typed_in_script = [{"sender": "user", "content": "ఈ రోజు ఎలా ఉంది?"},
                       {"sender": "saathi", "content": "నేను బాగున్నాను."}]
    check("Someone who types Telugu script keeps seeing Telugu script",
          chat.shown_as_they_write(typed_in_script)[1]["content"] == "నేను బాగున్నాను.")

    streamed = letters.AsTheyWrite(True)
    out = "".join(streamed.feed(piece) for piece in ["చా", "లా బాధ", "గా ఉం", "ది."]) + streamed.finish()
    check("Streaming converts whole words, even when pieces split them",
          out == "chala badhaga undi.", out)
    untouched = letters.AsTheyWrite(False)
    check("...and an English reply streams through unchanged",
          "".join(untouched.feed(p) for p in ["That ", "sounds ", "hard."]) == "That sounds hard.")

    section("LOGOUT")
    copied_token = a.cookies.get("saathi_session")
    a.post(f"{BASE}/api/logout")
    check("After logout, chat data -> refused", a.get(f"{BASE}/api/conversations").status_code == 401)
    check("After logout, the chat page -> sent to login",
          a.get(f"{BASE}/home", allow_redirects=False).status_code == 303)
    check("A copy of the old login token is useless",
          requests.get(f"{BASE}/api/conversations",
                       cookies={"saathi_session": copied_token}).status_code == 401)


# ---------------------------------------------------------------- running it

running = {}   # the test server, so the restart check can stop and start it


def start_server():
    """Run SAATHI inside this script, on its own port, until the checks finish."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            running["server"], running["thread"] = server, thread
            return server, thread
        time.sleep(0.1)
    raise RuntimeError(f"The test server didn't start on port {PORT}. Is something else using it?")


def stop_server():
    """Shut SAATHI down, the way closing its terminal window would."""
    running["server"].should_exit = True
    running["thread"].join(timeout=10)
    time.sleep(0.3)   # let the port go free before anything starts again


def main():
    # The safety lock: never, ever run the checks on the real database.
    if DB_PATH.resolve() == DEFAULT_DB_PATH.resolve():
        sys.exit("Refusing to run: this would use your REAL database.")

    print(f"SAATHI checks, on a throwaway database:\n  {DB_PATH}")
    start_server()
    try:
        run_checks()
    except Exception as error:   # a crash counts as a failure, never a silent stop
        results.append(("CRASHED", f"{type(error).__name__}: {error}", False, ""))
    finally:
        stop_server()

    shown = None
    for sec, label, passed, detail in results:
        if sec != shown:
            print(f"\n{sec}")
            shown = sec
        line = f"  [{'PASS' if passed else 'FAIL'}] {label}"
        if not passed and detail != "":
            line += f"   <- got: {detail}"
        print(line)

    failed = [r for r in results if not r[2]]
    print("\n" + "-" * 64)
    print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
    print("ALL CHECKS PASSED" if not failed else "SOME CHECKS FAILED - see [FAIL] above")

    shutil.rmtree(WORK_DIR, ignore_errors=True)   # the throwaway database goes too
    if WORK_DIR.exists():
        print(f"(note: couldn't remove the throwaway folder {WORK_DIR})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
