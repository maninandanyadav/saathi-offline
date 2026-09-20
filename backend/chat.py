"""
SAATHI - conversations and messages.

Every function here takes the logged-in person's user id and only ever
touches that person's own data. The id always comes from the session,
never from anything the browser sends.
"""

from backend import ai
from backend.database import get_connection


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
        SELECT id, title, created_at, updated_at
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

    return {
        "conversation": conversation_to_dict(conversation),
        "messages": [message_to_dict(row) for row in rows],
    }


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

CONTEXT_MESSAGES = 20   # how many recent messages SAATHI reads before replying


def build_context(connection, conversation, limit=CONTEXT_MESSAGES):
    """Everything SAATHI should know before replying, gathered in one place.

    In Step 6 this becomes the input for the local AI:
        conversation info + recent messages + the message being replied to
    """
    rows = connection.execute(
        MESSAGE_SELECT
        + """
        WHERE m.conversation_id = ?
          AND m.deleted_at IS NULL
        ORDER BY m.id DESC
        LIMIT ?
        """,
        (conversation["id"], limit),
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

    return {
        "conversation": {"id": conversation["id"], "title": conversation["title"]},
        "recent_messages": recent,
        "replying_to": replying_to,
    }


CONTEXT_CHARACTERS = 6000   # roughly how much conversation we hand to the AI


def to_ai_messages(recent):
    """Turn recent messages into the form the AI expects.

    The AI can only hold so much text at once, so we start from the newest
    message and work backwards until we reach the budget. The newest messages
    always get through; the oldest are the ones dropped.
    """
    chosen, used = [], 0
    for message in reversed(recent):                 # newest first
        used += len(message["content"])
        if used > CONTEXT_CHARACTERS and chosen:     # always keep at least one
            break
        chosen.append({
            # the AI calls SAATHI's own past messages "assistant"
            "role": "assistant" if message["sender"] == "saathi" else "user",
            "content": message["content"],
        })
    chosen.reverse()                                 # back to oldest-first, like reading
    return chosen


QUOTE_LENGTH = 400   # how much of a replied-to message to show the AI


def with_reply_context(messages, replying_to):
    """Tell the AI which earlier message the newest one is answering.

    The note is attached to the newest message, so the AI reads "what you are
    answering" right beside "what they said". A deleted original cannot leak
    here: its words were erased from the database when it was deleted.
    """
    if not replying_to or not messages:
        return messages

    if replying_to.get("available"):
        whose = "yours" if replying_to["sender"] == "saathi" else "their own"
        note = f'[Replying to this earlier message of {whose}: "{shorten(replying_to["content"], QUOTE_LENGTH)}"]'
    else:
        note = "[Replying to an earlier message. Original message unavailable - it was deleted.]"

    newest = messages[-1]
    return messages[:-1] + [{**newest, "content": f'{note}\n\n{newest["content"]}'}]


def prepare_reply(user_id, conversation_id):
    """Everything that happens BEFORE SAATHI speaks.

    Checks the conversation is theirs, gathers the recent messages (6E) and
    the message being replied to (6F), and builds exactly what the AI is sent.
    Returns those messages, or None if the conversation isn't theirs.
    """
    with get_connection() as connection:
        conversation = owned_conversation(connection, user_id, conversation_id)
        if conversation is None:
            return None
        context = build_context(connection, conversation)

    latest = context["recent_messages"][-1] if context["recent_messages"] else None
    if latest is None or latest["sender"] != "user":
        raise NothingToReplyTo("There's no new message to reply to.")

    messages = to_ai_messages(context["recent_messages"])
    return with_reply_context(messages, context["replying_to"])


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
    messages = prepare_reply(user_id, conversation_id)
    if messages is None:
        return None
    return save_saathi_message(user_id, conversation_id, ai.reply(messages))
