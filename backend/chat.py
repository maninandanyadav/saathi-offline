"""
SAATHI - conversations and messages.

Every function here takes the logged-in person's user id and only ever
touches that person's own data. The id always comes from the session,
never from anything the browser sends.
"""

import logging
import threading

from backend import ai, context, letters
from backend.database import get_connection

log = logging.getLogger("saathi.chat")


def to_iso(sqlite_time):
    """'2026-09-18 18:43:58' (UTC) -> '2026-09-18T18:43:58Z'

    The Z tells the browser the time is UTC. Without it, browsers assume
    it is already local time and show it hours off.
    """
    return sqlite_time.replace(" ", "T") + "Z" if sqlite_time else None


def conversation_to_dict(row):
    """Only what the screen needs. The owner's id is never sent out."""
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": to_iso(row["created_at"]),
        "updated_at": to_iso(row["updated_at"]),
    }


def owned_conversation(connection, user_id, conversation_id):
    """The conversation if it belongs to this person - otherwise None.

    This is THE ownership check. Every function that touches a conversation
    goes through it, so there is exactly one lock to get right.
    """
    return connection.execute(
        """
        SELECT id, title, created_at, updated_at,
               summary, summary_upto_message_id
        FROM conversations
        WHERE id = ? AND user_id = ?
        """,
        (conversation_id, user_id),
    ).fetchone()


def create_conversation(user_id):
    """Start a new, empty conversation for this person."""
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO conversations (user_id) VALUES (?)",
            (user_id,),
        )
        row = owned_conversation(connection, user_id, cursor.lastrowid)
    return conversation_to_dict(row)


def list_conversations(user_id):
    """This person's conversations, most recently active first."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
    return [conversation_to_dict(row) for row in rows]


# ---------------------------------------------------------------- reading messages

PREVIEW_LENGTH = 120

# The start of every query that reads messages. Each query adds its own
# WHERE part after this, and values still always go through ? placeholders.
MESSAGE_SELECT = """
    SELECT m.id, m.sender, m.content, m.created_at, m.saved_at,
           m.reply_to_message_id,
           o.sender     AS original_sender,
           o.content    AS original_content,
           o.deleted_at AS original_deleted_at
    FROM messages AS m
    LEFT JOIN messages AS o
           ON o.id = m.reply_to_message_id
          AND o.conversation_id = m.conversation_id  -- only ever quote from this conversation
"""


def shorten(text, limit=PREVIEW_LENGTH):
    """The first part of a message, used in reply previews."""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def message_to_dict(row):
    """One message, as the screen needs it."""
    message = {
        "id": row["id"],
        "sender": row["sender"],
        "content": row["content"],
        "created_at": to_iso(row["created_at"]),
        "saved": row["saved_at"] is not None,
        "reply_to": None,
    }

    if row["reply_to_message_id"] is not None:
        if row["original_content"] is None or row["original_deleted_at"] is not None:
            # The original was deleted, or can't be shown. Say so - without its words.
            message["reply_to"] = {"id": row["reply_to_message_id"], "available": False}
        else:
            message["reply_to"] = {
                "id": row["reply_to_message_id"],
                "available": True,
                "sender": row["original_sender"],
                "preview": shorten(row["original_content"]),
            }

    return message


def get_conversation(user_id, conversation_id):
    """One of this person's conversations with its messages - or None.

    None means "not found", whether the conversation doesn't exist or belongs
    to someone else. Nobody can tell the difference, so nobody can go looking.
    """
    with get_connection() as connection:
        conversation = owned_conversation(connection, user_id, conversation_id)
        if conversation is None:
            return None

        rows = connection.execute(
            MESSAGE_SELECT
            + """
            WHERE m.conversation_id = ?
              AND m.deleted_at IS NULL      -- deleted messages simply don't appear
            ORDER BY m.id
            """,
            (conversation_id,),
        ).fetchall()

    messages = [message_to_dict(row) for row in rows]
    return {
        "conversation": conversation_to_dict(conversation),
        "messages": shown_as_they_write(messages),
    }


def how_they_write(user_id, conversation_id):
    """Does this person type Telugu in a-z letters in this conversation?"""
    opened = get_conversation(user_id, conversation_id)
    return bool(opened) and letters.person_writes_in_english(opened["messages"])


def shown_as_they_write(messages):
    """Show SAATHI's Telugu in the letters this person uses.

    The model's Telugu is STORED in Telugu script, because that is what keeps
    it writing good Telugu - seeing its own a-z replies in the conversation
    made it copy that style and invent words. Only the way it is shown
    changes, and only for someone who types Telugu in a-z letters themselves.
    """
    if not letters.person_writes_in_english(messages):
        return messages

    shown = []
    for message in messages:
        if message["sender"] == "saathi" and letters.has_telugu(message["content"]):
            message = {**message, "content": letters.to_english_letters(message["content"])}
        shown.append(message)
    return shown


# ---------------------------------------------------------------- writing messages

MAX_MESSAGE_LENGTH = 4000


class MessageError(Exception):
    """The message can't be accepted. The text is safe to show the person."""


class NothingToReplyTo(Exception):
    """SAATHI has already answered the latest message."""


def clean_message(content):
    """Check a message and tidy it. Returns the text to store."""
    content = content.strip()
    if not content:
        raise MessageError("Please type a message.")
    if len(content) > MAX_MESSAGE_LENGTH:
        raise MessageError(
            f"That message is too long - {MAX_MESSAGE_LENGTH:,} characters at most. "
            "Try sending it in parts."
        )
    return content


def check_reply_target(connection, conversation_id, reply_to_message_id):
    """A reply may only point at a visible message in the SAME conversation."""
    if reply_to_message_id is None:
        return None
    target = connection.execute(
        """
        SELECT id FROM messages
        WHERE id = ? AND conversation_id = ? AND deleted_at IS NULL
        """,
        (reply_to_message_id, conversation_id),
    ).fetchone()
    if target is None:
        # One answer whether it doesn't exist, was deleted, or sits in someone
        # else's conversation - so nobody can probe for other people's messages.
        raise MessageError("That message isn't available to reply to.")
    return reply_to_message_id


def insert_message(connection, conversation_id, sender, content, reply_to_message_id=None):
    """Save one message, and move its conversation to the top of the list."""
    message_id = connection.execute(
        """
        INSERT INTO messages (conversation_id, sender, content, reply_to_message_id)
        VALUES (?, ?, ?, ?)
        """,
        (conversation_id, sender, content, reply_to_message_id),
    ).lastrowid
    connection.execute(
        "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
        (conversation_id,),
    )
    row = connection.execute(MESSAGE_SELECT + "WHERE m.id = ?", (message_id,)).fetchone()
    return message_to_dict(row)


def add_user_message(user_id, conversation_id, content, reply_to_message_id=None):
    """Save something the person wrote, optionally as a reply to one message.

    None if the conversation isn't theirs.
    """
    with get_connection() as connection:
        if owned_conversation(connection, user_id, conversation_id) is None:
            return None
        content = clean_message(content)
        reply_to = check_reply_target(connection, conversation_id, reply_to_message_id)
        return insert_message(connection, conversation_id, "user", content, reply_to)


# ---------------------------------------------------------------- changing and deleting

MAX_TITLE_LENGTH = 80


class TitleError(Exception):
    """The conversation name can't be accepted. The text is safe to show."""


def find_message(connection, user_id, conversation_id, message_id):
    """A visible message, in this conversation, owned by this person - or None.

    A message id alone is never trusted: it must belong to the conversation
    in the address, and that conversation must belong to the logged-in person.
    """
    return connection.execute(
        """
        SELECT m.id FROM messages AS m
        JOIN conversations AS c ON c.id = m.conversation_id
        WHERE m.id = ? AND m.conversation_id = ? AND c.user_id = ?
          AND m.deleted_at IS NULL
        """,
        (message_id, conversation_id, user_id),
    ).fetchone()


def set_saved(user_id, conversation_id, message_id, saved):
    """Star or un-star a message. None if it isn't this person's."""
    with get_connection() as connection:
        if find_message(connection, user_id, conversation_id, message_id) is None:
            return None
        if saved:
            # "AND saved_at IS NULL" keeps the original time if it was already saved.
            connection.execute(
                "UPDATE messages SET saved_at = datetime('now') WHERE id = ? AND saved_at IS NULL",
                (message_id,),
            )
        else:
            connection.execute("UPDATE messages SET saved_at = NULL WHERE id = ?", (message_id,))
        row = connection.execute(MESSAGE_SELECT + "WHERE m.id = ?", (message_id,)).fetchone()
    return message_to_dict(row)


def delete_message(user_id, conversation_id, message_id):
    """Erase a message's words for good. False if it isn't this person's.

    An empty marker stays behind, so a reply to it can still say
    "Original message unavailable" instead of pretending it was never a reply.

    The conversation's notes are thrown away at the same time. Those notes
    were written FROM the messages, so they could still be carrying the words
    that were just deleted. They are rebuilt later from what remains - which
    is the only honest way to keep "delete" meaning delete.
    """
    with get_connection() as connection:
        if find_message(connection, user_id, conversation_id, message_id) is None:
            return False
        connection.execute(
            """
            UPDATE messages
            SET content = '', saved_at = NULL, deleted_at = datetime('now')
            WHERE id = ?
            """,
            (message_id,),
        )
        connection.execute(
            "UPDATE conversations SET summary = NULL, summary_upto_message_id = NULL "
            "WHERE id = ?",
            (conversation_id,),
        )
    return True


def rename_conversation(user_id, conversation_id, title):
    """Give a conversation a new name. None if it isn't this person's."""
    title = " ".join(title.split())   # tidy extra spaces and line breaks into single spaces
    if not title:
        raise TitleError("Please give the conversation a name.")
    if len(title) > MAX_TITLE_LENGTH:
        raise TitleError(f"That name is too long - {MAX_TITLE_LENGTH} characters at most.")

    with get_connection() as connection:
        if owned_conversation(connection, user_id, conversation_id) is None:
            return None
        connection.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )
        return conversation_to_dict(owned_conversation(connection, user_id, conversation_id))


def delete_conversation(user_id, conversation_id):
    """Delete a conversation and ALL its messages, for good. False if it isn't theirs."""
    with get_connection() as connection:
        deleted = connection.execute(
            # The owner check is part of the DELETE itself. Its messages go with it
            # automatically (ON DELETE CASCADE, from Step 5A).
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).rowcount
    return deleted == 1


# ---------------------------------------------------------------- SAATHI's reply

# How many recent messages to READ from the database. This is only a rough
# net to keep the query small - the real limit is context.CONTEXT_TOKENS,
# which decides how many of these actually reach the model. Short messages
# are cheap, so 40 of them usually fit; a few long ones will not, and the
# token budget trims them.
CONTEXT_MESSAGES = 40

# How far back Step 7D may look for a message that matters right now. These
# are only SEARCHED, never all sent - at most two of them are brought back.
SEARCHABLE_MESSAGES = 300


def gather_from_database(connection, conversation, limit=CONTEXT_MESSAGES):
    """Read the raw material for a reply out of the database.

    This is the database half of the work: the conversation, its recent
    messages in order, and the one message being replied to (fetched in full,
    because it may be older than the recent messages). Shaping all of this
    into what the AI is told is context.build()'s job, not this one.
    """
    # Everything since the summary leaves off - NOT "the newest 40".
    #
    # This matters more than it looks. With a sliding window, every new message
    # pushed the oldest one out, so the conversation the model was given
    # changed at its START every single turn. Ollama could then reuse nothing
    # it had already read, and on this computer a 62-message conversation cost
    # about 40 seconds PER REPLY, over and over.
    #
    # Anchored to the summary, the conversation only ever GROWS at the end
    # between one summary and the next, so everything before stays word for
    # word the same and Ollama reads only the new message. The anchor moves in
    # steps of twenty, when the notes are rewritten - not every turn.
    anchor = conversation["summary_upto_message_id"] or 0
    rows = connection.execute(
        MESSAGE_SELECT
        + """
        WHERE m.conversation_id = ?
          AND m.deleted_at IS NULL
          AND m.id > ?           -- everything the notes do not already cover
        ORDER BY m.id DESC
        LIMIT ?
        """,
        (conversation["id"], anchor, limit),
    ).fetchall()

    recent = [message_to_dict(row) for row in reversed(rows)]   # oldest first, like reading
    latest = recent[-1] if recent else None

    # The message being replied to, fetched IN FULL. It may be older than the
    # recent messages above, and the AI needs its whole text, not a preview.
    replying_to = None
    if latest is not None and latest["reply_to"] is not None:
        original = connection.execute(
            """
            SELECT id, sender, content FROM messages
            WHERE id = ? AND conversation_id = ? AND deleted_at IS NULL
            """,
            (latest["reply_to"]["id"], conversation["id"]),
        ).fetchone()
        if original is None:
            replying_to = {"id": latest["reply_to"]["id"], "available": False}
        else:
            replying_to = {
                "id": original["id"],
                "available": True,
                "sender": original["sender"],
                "content": original["content"],
            }

    # Step 7D: the messages BEFORE the recent window, so one of them can be
    # brought back if it turns out to matter. Only this conversation is read -
    # the WHERE below cannot see another conversation, let alone another
    # person's. Searching them is context.find_relevant()'s job, not this one.
    oldest_shown = recent[0]["id"] if recent else None
    older = []
    if oldest_shown is not None:
        older = [dict(row) for row in connection.execute(
            """
            SELECT id, sender, content FROM messages
            WHERE conversation_id = ?
              AND deleted_at IS NULL     -- deleted words are never searched
              AND id < ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation["id"], oldest_shown, SEARCHABLE_MESSAGES),
        ).fetchall()]

    return {
        "conversation": {"id": conversation["id"], "title": conversation["title"]},
        "recent_messages": recent,
        "replying_to": replying_to,
        "summary": conversation["summary"],      # the older part, in a few sentences
        "older_messages": older,                 # searchable, not sent
    }


def prepare_reply(user_id, conversation_id):
    """Everything that happens BEFORE SAATHI speaks.

    THE WHOLE JOURNEY OF ONE MESSAGE, and where each part lives:

      you type a message
        -> main.py            saves it, after checking you are logged in
        -> owned_conversation is this conversation yours? (the only lock)
        -> gather_from_database
              the messages since the summary leaves off,
              the one message you are replying to, fetched in full,
              the older messages, for searching but not for sending,
              the summary of the older part
        -> context.build      decides what the AI is actually told:
              to_ai_messages      who said what, in order, inside the budget
              find_relevant       an older message that matches your words
              with_recalled       ...attached to your newest message
              with_reply_context  ...and which message you are answering
        -> ai.reply           adds SAATHI's personality and the summary,
              mark_letters        tells it which letters to answer in,
              and asks the model on 127.0.0.1
        -> letters            shows Telugu in the letters you write in
        -> main.py            sends it to your screen, saves it, and starts
                              the summary in the background if it is due

    Only the last four steps involve the AI at all. Everything before them is
    ordinary Python deciding what is worth saying - which is why it can be
    read, tested and trusted without running a model.

    Returns that plan, or None if the conversation isn't theirs.
    """
    with get_connection() as connection:
        conversation = owned_conversation(connection, user_id, conversation_id)
        if conversation is None:
            return None
        gathered = gather_from_database(connection, conversation)

    latest = gathered["recent_messages"][-1] if gathered["recent_messages"] else None
    if latest is None or latest["sender"] != "user":
        raise NothingToReplyTo("There's no new message to reply to.")

    return context.build(gathered["recent_messages"], gathered["replying_to"],
                         gathered["summary"], gathered["older_messages"])


def save_saathi_message(user_id, conversation_id, text):
    """Save what SAATHI said. None if the conversation vanished meanwhile."""
    with get_connection() as connection:
        if owned_conversation(connection, user_id, conversation_id) is None:
            return None    # the conversation was deleted while SAATHI was thinking
        return insert_message(connection, conversation_id, "saathi", text)


def add_saathi_reply(user_id, conversation_id):
    """Ask the AI for a whole reply, then save it.

    The slow part happens between the two database visits, so no connection
    is held open while the AI thinks.
    """
    plan = prepare_reply(user_id, conversation_id)
    if plan is None:
        return None
    text = ai.reply(plan["messages"], plan["background"])
    saved = save_saathi_message(user_id, conversation_id, text)
    if saved is None:
        return None
    # Shown in their letters; the Telugu script stays in the database.
    if letters.has_telugu(saved["content"]) and how_they_write(user_id, conversation_id):
        saved = {**saved, "content": letters.to_english_letters(saved["content"])}
    return saved


# ---------------------------------------------------------------- the summary (7C)

# How many messages must fall out of sight before SAATHI writes notes about
# them. Writing notes costs a whole extra visit to the AI, so it is done in
# batches rather than every time one message scrolls out.
SUMMARIZE_AFTER = 20

# Conversations whose notes are being written right now, so two replies
# arriving close together can never start the same job twice.
being_summarized = set()


# How many recent messages stay in full when the notes are rewritten. The
# conversation sent to the AI therefore moves between 20 and 40 messages, and
# grows only at its end in between - which is what keeps replies fast.
KEEP_IN_FULL = 20


def messages_to_fold(connection, conversation_id, already_covered):
    """The messages ready to become notes: everything since the last notes,
    except the newest KEEP_IN_FULL, which stay in the conversation in full.

    Returns nothing until there are enough of them to be worth one visit to
    the AI, so the notes are rewritten roughly every twenty messages instead
    of every single time somebody speaks.
    """
    since_notes = connection.execute(
        """
        SELECT id, sender, content FROM messages
        WHERE conversation_id = ? AND deleted_at IS NULL
          AND id > ?           -- not already written into the notes
        ORDER BY id
        """,
        (conversation_id, already_covered or 0),
    ).fetchall()

    if len(since_notes) <= KEEP_IN_FULL:
        return []
    foldable = since_notes[:-KEEP_IN_FULL]
    return foldable if len(foldable) >= SUMMARIZE_AFTER else []


def update_summary_if_needed(user_id, conversation_id):
    """Fold the older part of a long conversation into a few sentences.

    Returns the new notes, or None if nothing needed doing. Notice the
    database connection is closed BEFORE the AI is asked: writing notes takes
    many seconds, and holding the database open that long would block
    everything else.
    """
    with get_connection() as connection:
        conversation = owned_conversation(connection, user_id, conversation_id)
        if conversation is None:
            return None
        older = messages_to_fold(connection, conversation_id,
                                 conversation["summary_upto_message_id"])
        if len(older) < SUMMARIZE_AFTER:
            return None
        previous = conversation["summary"]
        forget_after = older[-1]["id"]
        older = [dict(row) for row in older]

    notes = ai.summarize(previous, older)      # slow, and no connection is held
    if notes is None:
        return None                            # try again after the next reply

    with get_connection() as connection:
        if owned_conversation(connection, user_id, conversation_id) is None:
            return None                        # deleted while the notes were written
        connection.execute(
            "UPDATE conversations SET summary = ?, summary_upto_message_id = ? WHERE id = ?",
            (notes, forget_after, conversation_id),
        )
    log.info("Wrote notes for conversation %s, covering up to message %s",
             conversation_id, forget_after)
    return notes


def update_summary_in_background(user_id, conversation_id):
    """Write the notes while the person reads the reply, so nobody waits.

    If anything goes wrong it is written to the terminal and forgotten. A
    failed summary must never break a conversation - the next reply simply
    tries again.
    """
    if conversation_id in being_summarized:
        return
    being_summarized.add(conversation_id)

    def work():
        try:
            update_summary_if_needed(user_id, conversation_id)
        except Exception as problem:               # noqa: BLE001 - nothing may escape
            log.exception("Writing the notes failed: %s", problem)
        finally:
            being_summarized.discard(conversation_id)

    threading.Thread(target=work, daemon=True).start()
