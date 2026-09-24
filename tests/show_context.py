r"""
Show exactly what SAATHI sends to the local AI. Nothing hidden.

    .\venv\Scripts\python.exe tests\show_context.py shivani_26
    .\venv\Scripts\python.exe tests\show_context.py shivani_26 15

With no conversation number it lists your conversations. With one, it prints
the context builder's decisions and every message the AI would be told.

This only READS. It never changes a message, never deletes anything, and
never contacts the AI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")   # so Telugu and Hindi print on Windows

from backend import chat, context                       # noqa: E402
from backend.database import DB_PATH, get_connection    # noqa: E402

LINE = "-" * 70


def find_user(saathi_id):
    with get_connection() as connection:
        return connection.execute(
            "SELECT id, saathi_id FROM users WHERE saathi_id = ?", (saathi_id,)
        ).fetchone()


def show_conversations(user):
    conversations = chat.list_conversations(user["id"])
    if not conversations:
        print(f"{user['saathi_id']} has no conversations yet.")
        return
    print(f"Conversations for {user['saathi_id']}:")
    for conversation in conversations:
        print(f"  {conversation['id']:>4}   {conversation['title'] or '(no name yet)'}")
    print()
    print("Run it again with one of those numbers to see the context.")


def show_context(user, conversation_id):
    with get_connection() as connection:
        conversation = chat.owned_conversation(connection, user["id"], conversation_id)
        if conversation is None:
            # The same answer whether it doesn't exist or belongs to someone
            # else - exactly what the website does.
            print(f"Conversation {conversation_id} is not one of {user['saathi_id']}'s.")
            return
        gathered = chat.gather_from_database(connection, conversation)
        total = connection.execute(
            """
            SELECT COUNT(*) AS how_many FROM messages
            WHERE conversation_id = ? AND deleted_at IS NULL
            """,
            (conversation["id"],),
        ).fetchone()["how_many"]

    plan = context.build(gathered["recent_messages"], gathered["replying_to"],
                         gathered["summary"])
    messages = plan["messages"]

    print(LINE)
    print(f"Conversation {conversation['id']}: {conversation['title'] or '(no name yet)'}")
    print(f"Owner: {user['saathi_id']}")
    print(LINE)
    print(f"  {'messages in this conversation altogether':<44} {total}")
    for label, value in context.describe(gathered["recent_messages"], messages,
                                         plan["background"]).items():
        print(f"  {label:<44} {value}")
    print(LINE)

    if plan["background"]:
        print("WHAT SAATHI ALREADY KNOWS (the notes on the older part):")
        print()
        for line in plan["background"].splitlines():
            print(f"      {line}")
        print()
        print(LINE)

    print("WHAT THE AI IS TOLD, in this order:")
    print()

    if not messages:
        print("  (nothing - this conversation is empty)")
    for number, message in enumerate(messages, start=1):
        words = message["content"]
        if len(words) > 300:
            words = words[:300] + f" … (+{len(message['content']) - 300} more characters)"
        words = words.replace("\n", "\n" + " " * 16)
        print(f"  {number:>2}. {message['role']:<9} | {words}")
        print()

    print(LINE)
    print("The AI sees no names, no ID and no other conversation - only the above,")
    print("plus SAATHI's personality from backend/ai.py.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    print(f"reading: {DB_PATH}\n")

    user = find_user(sys.argv[1])
    if user is None:
        print(f"No account called {sys.argv[1]!r}.")
        return

    if len(sys.argv) < 3:
        show_conversations(user)
        return

    if not sys.argv[2].isdigit():
        print(f"{sys.argv[2]!r} is not a conversation number.")
        return
    show_context(user, int(sys.argv[2]))


if __name__ == "__main__":
    main()
