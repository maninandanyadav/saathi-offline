"""
SAATHI - the few things it remembers about you across conversations.

A conversation summary (Step 7C) lives and dies with its conversation. What
is kept here outlives all of them: your name, what you study, what you are
building. That is the whole difference.

Nothing is saved here on its own. SAATHI does not listen to your chats and
quietly write things down - every memory is put here on purpose, and Step 8F
will show you all of them so you can delete any you do not want.

Like every other file that touches your data, each function takes the id of
the person who is logged in, and that id comes from the session cookie -
never from anything the browser sends.
"""

import logging
import sqlite3

from backend import context
from backend.database import get_connection

log = logging.getLogger("saathi.memory")

# A memory is a fact, not an essay. Anything longer is almost certainly a
# whole conversation being saved by mistake.
MAX_FACT_LENGTH = 300
MIN_FACT_LENGTH = 3

# Things that must never become a memory, however they are phrased. SAATHI
# has no reason to keep them, and a stored secret is a leaked secret.
NEVER_REMEMBER = ("password", "passcode", "otp", "pin number", "cvv",
                  "api key", "api-key", "secret key", "access token",
                  "credit card", "debit card", "aadhaar", "aadhar")


class MemoryRefused(Exception):
    """This cannot be remembered. The text is safe to show the person.

    Not called MemoryError: Python already has one of those, for running out
    of memory, and taking its name would hide real crashes.
    """


def clean_fact(text):
    """Check a fact and tidy it. Returns the text to store.

    Whitespace is collapsed, so "  The user  is studying   BTech. " and
    "The user is studying BTech." are stored as the same thing - which also
    lets the database's duplicate check recognise them as the same.
    """
    if not isinstance(text, str):
        raise MemoryRefused("Please write something for SAATHI to remember.")

    fact = " ".join(text.split())

    if not fact:
        raise MemoryRefused("Please write something for SAATHI to remember.")
    if len(fact) < MIN_FACT_LENGTH:
        raise MemoryRefused("That's too short to be worth remembering.")
    if len(fact) > MAX_FACT_LENGTH:
        raise MemoryRefused(
            f"A memory should be one fact, not a whole story - "
            f"{MAX_FACT_LENGTH} characters at most."
        )
    if not any(sign.isalpha() for sign in fact):
        raise MemoryRefused("That doesn't look like something to remember.")

    lowered = fact.lower()
    for secret in NEVER_REMEMBER:
        if secret in lowered:
            log.warning("Refused to remember something that looked like a secret.")
            raise MemoryRefused(
                "SAATHI doesn't keep passwords, codes or card details in its memory."
            )

    return fact


def memory_to_dict(row):
    """One memory, as the screen needs it. The owner's id is never sent out."""
    return {
        "id": row["id"],
        "fact": row["fact"],
        "created_at": to_iso(row["created_at"]),
        "updated_at": to_iso(row["updated_at"]),
    }


def to_iso(sqlite_time):
    """'2026-09-27 18:43:58' (UTC) -> '2026-09-27T18:43:58Z' for the browser."""
    return sqlite_time.replace(" ", "T") + "Z" if sqlite_time else None


# ------------------------------------------- telling two memories apart (8E)
#
# The database already refuses the SAME sentence twice. These are the near
# misses it cannot see:
#
#     "The user is studying BTech."
#     "The user studies BTech."
#     "User is a BTech student."
#
# Comparing whole sentences fails, because almost every memory contains "the
# user" and a verb like "likes" or "studying". Strip those away and what is
# left is what the memory is actually ABOUT - "btech" in all three above, and
# "cricket" versus "football" in two that only look alike.
EVERYDAY_IN_MEMORIES = {
    "user", "users", "person", "people", "they", "them", "their",
    "likes", "like", "liked", "loves", "love", "prefers", "prefer",
    "wants", "want", "knows", "know", "enjoys", "enjoy",
    "studying", "studies", "study", "studied", "student",
    "learning", "learns", "learn", "learnt", "learned",
    "working", "works", "work", "worked", "doing", "does",
    "name", "named", "called", "currently", "also",
}

# How much of the smaller memory has to appear in the bigger one before the
# two count as the same thing. Half is deliberately forgiving: it is better
# to update one memory than to collect five sentences saying the same thing.
SAME_THING = 0.5


def subject_of(fact):
    """What a memory is actually about, with the everyday words taken out."""
    return context.meaningful_words(fact) - EVERYDAY_IN_MEMORIES


def about_the_same(one, other):
    """Are these two memories about the same thing?

    Not clever, and not meant to be: no embeddings, no model, nothing to go
    wrong quietly. It only asks whether what the two are about overlaps.
    """
    mine, theirs = subject_of(one), subject_of(other)
    if not mine or not theirs:
        return False
    shared = mine & theirs
    return bool(shared) and len(shared) / min(len(mine), len(theirs)) >= SAME_THING


def replace_memory(user_id, memory_id, fact):
    """Write a newer wording over an existing memory, keeping its place.

    The user_id in the WHERE line is what stops this touching anybody else's
    memory, however the id was arrived at.
    """
    with get_connection() as connection:
        connection.execute(
            "UPDATE memories SET fact = ?, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (fact, memory_id, user_id),
        )
        row = connection.execute(
            "SELECT id, fact, created_at, updated_at FROM memories "
            "WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        ).fetchone()
    return memory_to_dict(row) if row else None


def list_memories(user_id):
    """Everything SAATHI remembers about this person, newest first.

    The WHERE line is the whole security story, and it is the same lock
    chat.list_conversations() uses: the query can only ever see rows
    belonging to the id it was given, and that id comes from the session
    cookie. There is no way to ask this function for somebody else - it
    takes no other argument.

    Someone with no memories gets an empty list, which is a perfectly good
    answer and never an error.
    """
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, fact, created_at, updated_at
            FROM memories
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
    return [memory_to_dict(row) for row in rows]


def delete_memory(user_id, memory_id):
    """Forget one thing, for good. False if it isn't this person's.

    The owner check is part of the DELETE itself, exactly as it is for a
    conversation: a row whose user_id does not match simply is not matched,
    so changing the id in a request reaches nothing. There is no separate
    "check, then delete" that could be got between.

    PRAGMA secure_delete is on for every connection, so the words are
    scrubbed from the database file rather than just unlinked.
    """
    with get_connection() as connection:
        gone = connection.execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        ).rowcount
    if gone:
        log.info("Forgot memory %s for user %s.", memory_id, user_id)
    return gone == 1


def save_memory(user_id, fact):
    """Remember one fact for this person. Returns the memory.

    Asking twice is harmless: the database refuses the same fact twice for
    the same person (UNIQUE on user_id and fact, ignoring capitals), so the
    second attempt quietly gives back the memory that already exists instead
    of making a near-copy.
    """
    fact = clean_fact(fact)

    # Is this really something new, or the same thing said differently? If it
    # is the same thing, the NEWER wording wins - people finish courses and
    # change plans, and the latest thing they said is the true one.
    for remembered in list_memories(user_id):
        if remembered["fact"].lower() == fact.lower():
            return remembered                      # word for word; nothing to do
        if about_the_same(remembered["fact"], fact):
            log.info("Updating memory %s instead of adding a near-copy.", remembered["id"])
            return replace_memory(user_id, remembered["id"], fact)

    with get_connection() as connection:
        try:
            memory_id = connection.execute(
                "INSERT INTO memories (user_id, fact) VALUES (?, ?)",
                (user_id, fact),
            ).lastrowid
        except sqlite3.IntegrityError:
            log.info("Already remembered that for user %s; not saving it twice.", user_id)
            row = connection.execute(
                "SELECT id, fact, created_at, updated_at FROM memories "
                "WHERE user_id = ? AND fact = ?",
                (user_id, fact),
            ).fetchone()
            return memory_to_dict(row)

        row = connection.execute(
            "SELECT id, fact, created_at, updated_at FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()

    return memory_to_dict(row)


# ------------------------- "what do you remember about me?" (Step 8H)
#
# This one question is answered from the database, not by the model.
#
# Asked directly, the model did list all four facts - but as a flowing
# sentence, and a model can always paraphrase a fact into something the
# person never said. When somebody asks what is being kept about them, the
# answer has to be exactly what is kept, word for word, every time.

# A remembering word AND a phrase meaning "about me" must both appear, so
# "I must remember to call her" is not mistaken for the question.
REMEMBER_WORDS = ("remember", "remembered", "గుర్తు", "gurthu", "gurtu",
                  "याद", "yaad", "yad")
ABOUT_ME = ("about me", "about myself", "of me",
            "నా గురించి", "naa gurinchi", "na gurinchi",
            "मेरे बारे", "mere bare", "mere baare")

# One sentence each, written out rather than translated by the model, so the
# wording never drifts and never invents.
LEAD_IN = {
    "telugu-script": "మీ గురించి నాకు గుర్తున్నవి:",
    "telugu-latin": "Mee gurinchi naku gurthunnavi:",
    "hindi-script": "मुझे आपके बारे में यह याद है:",
    "hindi-latin": "Mujhe tumhare baare mein yeh yaad hai:",
    "english": "Here's what I remember about you:",
}
NOTHING_YET = {
    "telugu-script": "మీ గురించి నాకు ఇంకా ఏమీ గుర్తు లేదు.",
    "telugu-latin": "Mee gurinchi naku inka emi gurthu ledu.",
    "hindi-script": "मुझे अभी आपके बारे में कुछ भी याद नहीं है.",
    "hindi-latin": "Mujhe abhi tumhare baare mein kuch bhi yaad nahi hai.",
    "english": "I don't have any saved memories about you yet.",
}


def asked_about_memories(text):
    """Is this person asking what SAATHI remembers about them?"""
    if not text:
        return False
    asking = text.lower()
    return (any(word in asking for word in REMEMBER_WORDS)
            and any(phrase in asking for phrase in ABOUT_ME))


def their_style(question):
    """Which written-out sentence to use, from the question's own language."""
    found = context.letters_profile(question)
    language, script = found["language"], found["script"]
    if language == "telugu":
        return "telugu-script" if script == "telugu" else "telugu-latin"
    if language == "hindi":
        return "hindi-script" if script == "devanagari" else "hindi-latin"
    return "english"


def as_told_to_them(fact):
    """"The user is studying BTech." -> "You are studying BTech."

    Only the opening is changed, and only when it is one SAATHI wrote itself.
    Anything else is shown exactly as it is stored - a memory is never
    reworded into something the person did not say.
    """
    for third_person, second_person in (("The user's ", "Your "),
                                        ("the user's ", "your "),
                                        ("The user is ", "You are "),
                                        ("the user is ", "you are "),
                                        ("The user has ", "You have "),
                                        ("The user ", "You "),
                                        ("the user ", "you ")):
        if fact.startswith(third_person):
            return second_person + fact[len(third_person):]
    return fact


def what_i_remember(user_id, question=""):
    """The answer to "what do you remember about me?", straight from storage."""
    style = their_style(question)
    remembered = list_memories(user_id)
    if not remembered:
        return NOTHING_YET[style]

    lines = [LEAD_IN[style]]
    lines += [f"• {as_told_to_them(one['fact'])}" for one in remembered]
    return "\n".join(lines)
