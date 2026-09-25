"""
SAATHI - talking to the local AI model.

Every request here goes to 127.0.0.1, which is this computer. Nothing is ever
sent to OpenAI, Claude, Gemini or anyone else's servers.
"""

import json
import logging

import requests

from backend import letters
from backend.letters import (HINDI_IN_ENGLISH, TELUGU_IN_ENGLISH,
                            written_in_english_letters)

log = logging.getLogger("saathi.ai")

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "gemma3:4b"
REPLY_TIMEOUT = 120        # seconds to wait for a whole reply
MAX_REPLY_TOKENS = 400     # a gentle cap, so no single reply runs away
CONTEXT_WINDOW = 8192      # how much text the AI can hold at once (personality + conversation + reply)
# How freely the model picks its words. The model's own default is 1.0, which
# wandered: it sometimes ignored the language instruction. 0.7 still sounds warm
# and human, but follows instructions far more reliably.
TEMPERATURE = 0.7
# Ollama normally drops the model from memory after 5 idle minutes, and the next
# message then waits ~9s for it to load again. Keeping it longer costs about 3 GB
# of RAM while you chat. Set this to "0" to free the memory the moment you stop.
KEEP_LOADED = "15m"

# What the person sees when something goes wrong. Kind, plain, and it says what
# they can do. The technical details go to the terminal instead.
CANT_REACH = ("SAATHI can't reach its local AI right now. "
              "Please check that Ollama is running, then try again.")
TOO_SLOW = "SAATHI is taking too long to think. Please try again."
NO_MODEL = ("SAATHI's AI model isn't installed. "
            "In a terminal, run:  ollama pull " + MODEL)
NO_MEMORY = ("There isn't enough free memory to run SAATHI's AI. "
             "Please close a few other apps and try again.")
NO_WORDS = "SAATHI couldn't find any words this time. Please try again."
UNKNOWN = "SAATHI is having trouble with its local AI. Please try again."

# Who SAATHI is. This is sent with every message, and it does a lot of work:
# in Step 6C the same model wrote broken English-Hindi mush without the
# language rule, and natural Telugu with it.
PERSONA = """You are SAATHI, a private companion who listens. You run on this
person's own computer, and your conversations never leave it.

How you talk:
- Warm, calm and human. Short: 2 to 4 sentences, like a good friend texting.
- Plain sentences only. No bullet points, no headings, no bold text.
- Listen first. Don't rush into advice unless they ask for it.
- One gentle question is often better than a paragraph of suggestions.
- Never hand their own words back to them. "You're feeling nervous about your
  exam" only tells them what they just told you. Answer the person instead:
  say something of your own, or ask one thing you don't yet know.

LANGUAGE RULE (very important):
- Always answer in the same language and the same letters as the person's NEWEST
  message. If their earlier messages used a different one, ignore that: the
  newest message decides. A note in square brackets tells you which letters to
  use this time - follow it exactly.
- Never switch to a language they did not use, and never invent sayings or proverbs.

Notes in square brackets:
- A message may carry a note in square brackets: which earlier message the
  person is answering, and which letters to write your reply in. Use both.
- Never mention a note, never repeat one back, and never treat a note as
  something the person said to you.
- If a note says the original message was deleted, do not guess what it said.

Honesty:
- You are an AI. If you are asked, say so simply and kindly.
- Never pretend to have a body, a day of your own, feelings you do not have,
  or to meet them anywhere.
- You are not a doctor or a therapist, and you never replace real people.
  If someone seems to be in danger, gently encourage them to reach someone
  they trust, or local emergency services."""


class AIUnavailable(Exception):
    """SAATHI could not get a reply. This message is safe to show the person."""


# Which letters a piece of writing uses. A small 4B model often drifts into the
# language of the older messages, so SAATHI works this out itself, in plain
# Python, and tells the model exactly which letters to use for THIS reply.
TELUGU_LETTERS = (0x0C00, 0x0C7F)
DEVANAGARI_LETTERS = (0x0900, 0x097F)


def uses_letters(text, letter_range):
    """Does this text contain any letter from that block of the alphabet?"""
    first, last = letter_range
    return any(first <= ord(sign) <= last for sign in text)


def which_letters(newest, earlier=None):
    """How should this reply be written? Worked out from the person's own words.

    `earlier` is the messages BEFORE this one. A short message like "haa"
    carries no evidence of its own, so the conversation decides - and
    because those earlier messages never change, this note never changes
    either, which is what keeps Ollama's memory of the conversation.
    """
    # A message may carry SEVERAL notes of ours in front of it: which message
    # is being replied to (7E), and an older message brought back (7D). Every
    # one of them has to come off before the language is judged.
    #
    # Stripping only the first was a real bug: a recalled Telugu message left
    # the second note in place, and someone writing plain English was answered
    # in Telugu script.
    while newest.lstrip().startswith("["):
        newest = newest.lstrip()
        closing = newest.find("]")
        if closing == -1:
            break
        newest = newest[closing + 1:]

    return letters.instruction(letters.profile(newest, earlier))


def old_which_letters(newest):
    """The first-match version, kept only to show what 7.5C replaced.

    It stopped at the first clue it found, so one word decided a whole
    message: "Today college lo presentation undi" became pure Telugu because
    of `undi`, and short messages like "bro" silently fell to English.
    """
    if uses_letters(newest, TELUGU_LETTERS):
        return "[Answer in Telugu script.]"
    if uses_letters(newest, DEVANAGARI_LETTERS):
        return "[Answer in Devanagari (Hindi) script.]"

    # Showing the style works far better than describing it. Told only to use
    # "Telugu in English letters", the model wrote Telugu script instead.
    # Telugu typed in a-z letters gets a reply in Telugu SCRIPT, on purpose.
    #
    # It was tried the obvious way first - asking for Telugu spelled in a-z
    # letters, twice, once with worked examples. Both times gemma3:4b produced
    # invented non-words: "manchju koncham nerchukundam", "Nenu nannu
    # sunchestaanu". The model simply has not read enough Telugu written that
    # way. It writes beautiful Telugu script, so the language is kept and only
    # the letters change. Hindi below does NOT need this - the same model
    # writes natural Hinglish.
    if written_in_english_letters(newest, TELUGU_IN_ENGLISH):
        return ("[They are writing Telugu using a-z letters. Answer in Telugu, "
                "written in Telugu script. Do not answer in English.]")
    if written_in_english_letters(newest, HINDI_IN_ENGLISH):
        return ("[They are writing Hindi in a-z letters. Write your whole reply the "
                "same way - Hindi words spelled in a-z letters, like this: "
                "\"Arre, kal exam hai? Thoda tension hona normal hai. \"  "
                "Never use Devanagari script. Never reply in plain English.]")

    # "Roman letters" was tried here first, and the model once answered in
    # Romanian. Name the letters plainly instead.
    return ("[Answer in the same letters and the same language mix as this message. "
            "Do not use Telugu or Devanagari script.]")


def mark_letters(messages):
    """Put that instruction in front of every message the person wrote.

    It has to sit next to their words: left in the personality at the top, the
    older messages in a long Telugu chat still dragged the reply back into
    Telugu when the person switched to English.

    Every message gets its own note, worked out from its own words, so a
    message always looks exactly the same however many turns later it is sent.
    Marking only the newest one made each message change shape from one turn to
    the next, which threw away Ollama's memory of the conversation and made
    every reply slower than the last (9s, then 16s, then 18s).
    """
    marked = []
    spoken_before = []
    for message in messages:
        their_own_words = message["content"]      # before any note of ours
        if message.get("role") == "user":
            # Judged against what came before it, never against what came
            # after, so this message's note is the same on every future turn.
            note = which_letters(their_own_words, spoken_before)
            message = dict(message)
            # In front of their words, not after them: a note left at the end
            # was sometimes copied into the reply.
            message["content"] = note + "\n" + their_own_words
        # The history used for judging holds THEIR words, never our notes -
        # our notes are written in English, and profiling them turned a Telugu
        # conversation into an English one.
        spoken_before.append({
            "sender": "user" if message.get("role") == "user" else "saathi",
            "content": their_own_words,
        })
        marked.append(message)
    return marked


NOTE_WORDS = ("answer in", "reply in", "reply the same", "they write", "same letters",
              "script", "replying to", "a-z",
              "letters", "they are writing", "from earlier in this conversation")


def looks_like_a_note(inside):
    """Is the text inside these brackets one of ours, rather than the person's?

    A very short bracket is always a label the model stuck on its own reply -
    "[Telugu]", "[Hindi]", "[English]" - and never something a person wrote.
    """
    if len(inside) <= 20:
        return True
    return any(word in inside.lower() for word in NOTE_WORDS)


def without_notes(text):
    """Remove a note the model copied into its own reply, front or back.

    It is told never to repeat the notes, and it usually doesn't - but a 4B
    model slips, and it slips at BOTH ends: "[a-z] ..." at the start, and a
    whole instruction pasted after the reply. Either one on screen would only
    confuse, so both come off.
    """
    clean = text.strip()

    while clean.startswith("["):
        closing = clean.find("]")
        if closing == -1 or closing > 300 or not looks_like_a_note(clean[1:closing]):
            break
        log.warning("The model copied a note into its reply; removed: %s", clean[:closing + 1])
        clean = clean[closing + 1:].lstrip()

    while clean.endswith("]"):
        opening = clean.rfind("[")
        if opening == -1 or len(clean) - opening > 300 or not looks_like_a_note(clean[opening + 1:-1]):
            break
        log.warning("The model pasted a note after its reply; removed: %s", clean[opening:])
        clean = clean[:opening].rstrip()

    return clean


def explain(problem):
    """Turn Ollama's technical complaint into something a person can act on."""
    text = (problem or "").lower()
    if "not found" in text or "no such model" in text or "try pulling" in text:
        return NO_MODEL
    if "memory" in text or "out of memory" in text:
        return NO_MEMORY
    return UNKNOWN


def post_to_ollama(payload, stream):
    """Send one request to the local AI, turning any failure into a kind message.

    Both jobs go through here - writing a reply, and writing a summary - so
    there is one place that knows how to fail gracefully.
    """
    if "cloud" in MODEL:
        # A safety catch. Ollama also offers models that run on ITS servers;
        # SAATHI must only ever use a model stored on this computer.
        raise AIUnavailable("SAATHI only uses local models, never cloud ones.")

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=REPLY_TIMEOUT, stream=stream)
    except requests.exceptions.ConnectionError as error:
        log.error("Cannot reach Ollama at %s: %s", OLLAMA_URL, error)
        raise AIUnavailable(CANT_REACH)
    except requests.exceptions.Timeout as error:
        log.error("Ollama did not answer within %ss: %s", REPLY_TIMEOUT, error)
        raise AIUnavailable(TOO_SLOW)
    except requests.exceptions.RequestException as error:
        log.exception("Unexpected problem talking to Ollama: %s", error)
        raise AIUnavailable(UNKNOWN)

    if response.status_code != 200:
        detail = response.text[:500]
        log.error("Ollama answered %s: %s", response.status_code, detail)
        raise AIUnavailable(explain(detail))

    return response


def ask_ollama(messages, stream, background=None):
    """Ask for a reply in the conversation.

    `background` is what SAATHI already knows about this conversation - the
    summary of its older part, from Step 7C. It goes with the personality,
    ahead of the messages, so the model treats it as something it knows
    rather than something the person just said.
    """
    personality = PERSONA
    if background:
        personality += "\n\nWhat you already know about this conversation:\n" + background

    return post_to_ollama({
        "model": MODEL,
        "messages": [{"role": "system", "content": personality}, *mark_letters(messages)],
        "stream": stream,
        "keep_alive": KEEP_LOADED,
        "options": {"num_predict": MAX_REPLY_TOKENS, "num_ctx": CONTEXT_WINDOW,
                    "temperature": TEMPERATURE},
    }, stream)


# ---------------------------------------------------------------- the summary job

SUMMARY_TOKENS = 220          # a summary must stay short, or it defeats the point

# A different job needs different instructions. This is not the companion
# talking - it is SAATHI keeping short notes for itself.
SUMMARY_JOB = """You keep short notes about one conversation, so it can be
remembered after the older messages are no longer shown.

Write the notes as a few plain sentences, in English, whatever language the
conversation is in. English is used because it costs the model far less room.

Keep the things the person may refer to again:
- what they are doing or working on, and any names they gave it
- people they mentioned, and who those people are to them
- plans, dates and deadlines
- how they have been feeling, in their own plain words

Rules:
- Write only what was actually said. Never guess, never add advice.
- If there are earlier notes, fold them together with the new messages into
  one set of notes. Keep what still matters, drop what has passed.
- No bullet points, no headings, no greeting. Just the sentences.
- Six sentences at the very most."""


def summarize(previous_summary, older_messages):
    """Fold the older part of a conversation into a few sentences.

    Returns the notes, or None if the AI could not manage it. This must never
    break a conversation, so the caller treats a failure as "no summary yet"
    and simply tries again later.
    """
    lines = []
    if previous_summary:
        lines.append("The notes so far:\n" + previous_summary + "\n")
    lines.append("The messages to fold in:")
    for message in older_messages:
        who = "SAATHI" if message["sender"] == "saathi" else "Them"
        lines.append(f"{who}: {message['content']}")

    try:
        response = post_to_ollama({
            "model": MODEL,
            "messages": [{"role": "system", "content": SUMMARY_JOB},
                         {"role": "user", "content": "\n".join(lines)}],
            "stream": False,
            "keep_alive": KEEP_LOADED,
            "options": {"num_predict": SUMMARY_TOKENS, "num_ctx": CONTEXT_WINDOW,
                        "temperature": TEMPERATURE},
        }, stream=False)
        notes = response.json()["message"]["content"].strip()
    except (AIUnavailable, ValueError, KeyError) as problem:
        log.error("Could not write the summary this time: %s", problem)
        return None

    notes = without_notes(notes)
    if not notes:
        log.error("The summary came back empty")
        return None
    return notes


def reply(messages, background=None):
    """Ask the local AI for a whole reply, and check it came back right.

    If the reply is in the wrong language, it is asked for once more with only
    the newest message. If that is wrong too, the first reply is kept: a reply
    in the wrong language is still better than no reply at all.
    """
    text = one_reply(messages, background)
    if came_back_right(messages, text):
        return text

    log.warning("The reply came back in the wrong language; asking again.")
    for _ in range(ONE_MORE_TRY):
        second = one_reply(just_the_newest(messages), background)
        if came_back_right(messages, second):
            return second
    return text


def one_reply(messages, background=None):
    """One attempt: ask, read the answer, tidy it."""
    response = ask_ollama(messages, stream=False, background=background)
    try:
        text = response.json()["message"]["content"].strip()
    except (ValueError, KeyError) as error:
        log.error("Could not understand Ollama's answer: %s", error)
        raise AIUnavailable(UNKNOWN)
    text = without_notes(text)
    if not text:
        log.error("Ollama returned an empty reply")
        raise AIUnavailable(NO_WORDS)
    return text


def raw_pieces(messages, background=None):
    """Every small piece of the reply, exactly as Ollama sends it."""
    response = ask_ollama(messages, stream=True, background=background)
    try:
        for line in response.iter_lines():
            if not line:
                continue
            try:
                part = json.loads(line)
            except ValueError:
                log.error("Ollama sent a line we could not read: %s", line[:200])
                continue
            if part.get("error"):
                log.error("Ollama reported an error mid-reply: %s", part["error"])
                raise AIUnavailable(explain(part["error"]))
            piece = part.get("message", {}).get("content", "")
            if piece:
                yield piece
            if part.get("done"):
                return
    except requests.exceptions.Timeout as error:
        log.error("Ollama stopped sending within %ss: %s", REPLY_TIMEOUT, error)
        raise AIUnavailable(TOO_SLOW)
    except requests.exceptions.RequestException as error:
        log.error("Lost the connection to Ollama mid-reply: %s", error)
        raise AIUnavailable(CANT_REACH)


# How much of a reply is needed before its language is clear. A script shows
# itself in the very first word, so almost nothing is held back. Telling
# Hinglish from plain English needs real words, so that one waits longer.
#
# This matters for how streaming FEELS: holding 50 characters for every reply
# made short ones arrive in a single lump instead of word by word.
ENOUGH_FOR_SCRIPT = 12
ENOUGH_FOR_WORDS = 50


def enough_to_judge(messages):
    """How many characters to wait for before judging this reply."""
    wanted = letters.wanted_reply(letters.profile(their_own_words(messages)))
    language, script = wanted
    if script in ("telugu", "devanagari"):
        return ENOUGH_FOR_SCRIPT
    if language == "hindi":
        return ENOUGH_FOR_WORDS        # Hinglish or English? that needs words
    return ENOUGH_FOR_SCRIPT           # English: we are only watching for a script


def stream_reply(messages, background=None):
    """The reply as it is written, checked before any of it reaches the screen.

    The first few dozen characters are held back, just long enough to see
    which language they are in. If it is the wrong one, that attempt is
    dropped - unseen - and asked for again with only the newest message.
    Nothing appears and then disappears.
    """
    coming = clean_pieces(messages, background)
    wait_for = enough_to_judge(messages)
    held = ""
    judged = False

    for piece in coming:
        if judged:
            yield piece                       # already approved: straight through
            continue

        held += piece
        if len(held.strip()) < wait_for:
            continue                          # not enough yet to tell

        judged = True
        if came_back_right(messages, held):
            yield held
            continue

        log.warning("The reply started in the wrong language; asking again.")
        if hasattr(coming, "close"):
            coming.close()                    # stop the model writing the wrong one
        yield from clean_pieces(just_the_newest(messages), background)
        return

    if not judged and held:
        # A reply that ended before there was enough to judge. Let it through.
        yield held


def clean_pieces(messages, background=None):
    """The reply, piece by piece, with any copied note taken off.

    Only a reply that actually starts with "[" is held back for a moment, so
    an ordinary reply still reaches the screen as fast as before.
    """
    holding = ""        # the very start, until we know it is not a note
    tail = ""           # anything from a "[" onwards, until we know what it is
    checked = False

    for piece in raw_pieces(messages, background):
        if not checked:
            holding += piece
            started = holding.lstrip()
            if not started:
                continue                   # nothing but spaces so far
            if not started.startswith("["):
                checked = True             # an ordinary reply: let it straight through
                yield holding.lstrip()
            elif "]" in started or len(started) > 300:
                checked = True
                beginning = without_notes(holding)
                if beginning:
                    yield beginning
            continue

        # Past the beginning. A "[" from here on may be a note pasted after the
        # reply, so hold everything from it back rather than showing it and
        # taking it away again.
        if tail:
            tail += piece
        elif "[" in piece:
            before, bracket, after = piece.partition("[")
            if before:
                yield before
            tail = bracket + after
        else:
            yield piece

    if not checked and holding:            # a very short reply that was all note
        ending = without_notes(holding)
        if ending:
            yield ending
    elif tail:
        ending = without_notes(tail)
        if ending:
            yield ending


# ------------------------------------------------- asking again when it comes
# back wrong (7.5F)
#
# The model sometimes answers in the conversation's language instead of the
# person's. Measured: after two English turns, a Telugu message got an English
# reply 0 times out of 3 in Telugu. The SAME message with the SAME note, but
# with the conversation left out, came back in Telugu 3 times out of 3.
#
# So a wrong reply is asked for once more with only the newest message. That
# trades knowing what was said before for answering in the right language -
# a fair trade, and only when something has already gone wrong.

ONE_MORE_TRY = 1      # never a loop: one repair, then take what we have


def their_own_words(messages):
    """The newest thing the person actually wrote, with our notes taken off."""
    for message in reversed(messages):
        if message.get("role") == "user":
            text = message["content"]
            while text.lstrip().startswith("["):
                text = text.lstrip()
                closing = text.find("]")
                if closing == -1:
                    break
                text = text[closing + 1:]
            return text.strip()
    return ""


def came_back_right(messages, text):
    """Is this reply in the language the person's newest message asked for?"""
    return letters.reply_matches(letters.profile(their_own_words(messages)), text)


LAST_EXCHANGE = 3         # their previous message, our answer, and the new one


def just_the_newest(messages):
    """What to send on a second attempt - and it depends on the message.

    A message clearly in its own language does better ALONE: the conversation
    is exactly what dragged the reply into the wrong language. Measured on
    "Ela unnav bro?" after two English turns - 0 of 3 with the conversation,
    3 of 3 without it.

    A short message like "bro" or "haa" is the opposite. It has no language of
    its own and borrowed one from the conversation, so taking the conversation
    away takes away the only evidence there was. Measured on "bro" after a
    Telugu message - 0 of 3 alone, 3 of 3 with the last exchange kept.
    """
    newest = [message for message in reversed(messages) if message.get("role") == "user"][:1]
    if not newest:
        return messages

    barely_anything = letters.profile(their_own_words(messages))["confidence"] == "low"
    return messages[-LAST_EXCHANGE:] if barely_anything else newest
