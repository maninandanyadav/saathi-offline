"""
SAATHI - the database.

One SQLite file holding every account and every conversation.
It never leaves this computer.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Normally <project>/data/saathi.db, no matter which folder you started from.
# The SAATHI_DB environment variable can point it somewhere else instead -
# that's how the tests run on a throwaway copy and never touch your real data.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "saathi.db"
DB_PATH = Path(os.environ.get("SAATHI_DB", DEFAULT_DB_PATH))


@contextmanager
def get_connection():
    """Open the SAATHI database for one piece of work.

    Always used as:   with get_connection() as connection: ...

    When the work succeeds, changes are saved; if it fails, they're undone.
    And either way the connection is CLOSED at the end. (Python's own
    "with connection:" saves or undoes, but does NOT close - an open
    connection keeps the file locked on Windows.)
    """
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row  # lets us read columns by name
    connection.execute("PRAGMA foreign_keys = ON")
    # When something is deleted, overwrite its old bytes with zeros. Without this,
    # SQLite leaves deleted words sitting in the file until the space is reused.
    connection.execute("PRAGMA secure_delete = ON")
    try:
        with connection:    # save on success, undo on error
            yield connection
    finally:
        connection.close()  # and always close


def add_column_if_missing(connection, table, column, kind):
    """Add a column to a table that already exists.

    CREATE TABLE IF NOT EXISTS does NOTHING to a table that is already there.
    So when a later step needs a new column - like Step 7C's conversation
    summary - the column has to be added on purpose, or your existing
    conversations would keep the old shape and SAATHI would fail on them.

    The table and column names come from our own code here, never from
    anything typed in, which is why they can be written into the SQL.
    """
    already = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in already:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def init_db():
    """Create the tables if they don't exist. Safe to run on every startup."""
    with get_connection() as connection:

        # ------------------------------------------------------------ accounts
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                saathi_id     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        # ------------------------------------------------------------ chat
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT    NOT NULL DEFAULT 'New conversation',
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id     INTEGER NOT NULL,
                sender              TEXT    NOT NULL CHECK (sender IN ('user', 'saathi')),
                content             TEXT    NOT NULL,
                reply_to_message_id INTEGER,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                saved_at            TEXT,   -- NULL = not saved, a time = saved (and when)
                deleted_at          TEXT,   -- NULL = visible, a time = deleted (and when)
                FOREIGN KEY (conversation_id)     REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (reply_to_message_id) REFERENCES messages(id)      ON DELETE SET NULL
            )
            """
        )

        # ------------------------------------------------ Step 7C: the summary
        # A long conversation cannot all be sent to the AI, so the older part
        # is kept as a few sentences here. It belongs to this conversation
        # only: delete the conversation and the summary goes with it, and no
        # other conversation can ever see it.
        add_column_if_missing(connection, "conversations", "summary", "TEXT")
        # Which message the summary covers up to. Everything newer than this
        # is still sent to the AI in full.
        add_column_if_missing(connection, "conversations", "summary_upto_message_id", "INTEGER")

        # Indexes work like the index at the back of a book: SQLite can jump
        # straight to one user's conversations, or one conversation's messages,
        # instead of reading every row.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_user "
            "ON conversations (user_id, updated_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
            "ON messages (conversation_id, id)"
        )


# Runs only when you start this file directly, so you can check the database.
if __name__ == "__main__":
    init_db()
    with get_connection() as connection:
        print(f"Database file: {DB_PATH}\n")
        # Table names can't use ? placeholders. That's safe here only because
        # these names are written in our own code - never taken from a user.
        for table in ("users", "sessions", "conversations", "messages"):
            print(f"{table} table:")
            for column in connection.execute(f"PRAGMA table_info({table})"):
                print(f"  {column['name']:<20} {column['type']}")
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  -> {count} rows\n")
