"""
SAATHI - accounts.

Passwords are hashed with bcrypt and never stored as readable text.
"""

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import bcrypt

from backend.database import get_connection

MIN_ID_LENGTH = 3
MAX_ID_LENGTH = 20
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt cannot handle more than this

# Letters, numbers and underscores only.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


SESSION_DAYS = 30

# A real hash, used only to burn the same amount of time when an account
# does not exist - see login_user() below.
DUMMY_HASH = bcrypt.hashpw(b"no-such-account", bcrypt.gensalt()).decode("utf-8")


class SignupError(Exception):
    """The signup details were not acceptable. The message is safe to show."""


class LoginError(Exception):
    """Login failed. The message is deliberately vague."""


def hash_password(password):
    """Scramble a password one way. This can never be turned back."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password, password_hash):
    """Check a typed password against a stored hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def validate_signup(saathi_id, password, confirm_password):
    """Make sure the details make sense. Returns the cleaned SAATHI ID."""
    saathi_id = saathi_id.strip()

    if not saathi_id:
        raise SignupError("Please choose a SAATHI ID.")
    if len(saathi_id) < MIN_ID_LENGTH:
        raise SignupError(f"Your SAATHI ID needs at least {MIN_ID_LENGTH} characters.")
    if len(saathi_id) > MAX_ID_LENGTH:
        raise SignupError(f"Your SAATHI ID can be at most {MAX_ID_LENGTH} characters.")
    if not ID_PATTERN.match(saathi_id):
        raise SignupError("Your SAATHI ID can use only letters, numbers and underscores.")

    if not password:
        raise SignupError("Please choose a password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SignupError(f"Your password needs at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise SignupError("That password is too long. Please use a shorter one.")
    if password != confirm_password:
        raise SignupError("The two passwords do not match.")

    return saathi_id


def create_user(saathi_id, password, confirm_password):
    """Create an account and return its internal user id."""
    saathi_id = validate_signup(saathi_id, password, confirm_password)
    password_hash = hash_password(password)

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                "INSERT INTO users (saathi_id, password_hash) VALUES (?, ?)",
                (saathi_id, password_hash),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        # The database refused a duplicate - our last line of defence.
        raise SignupError("That SAATHI ID is already taken. Please choose another.")


# ------------------------------------------------------------------ login

def login_user(saathi_id, password):
    """Check the details. Returns the internal user id, or raises LoginError."""
    saathi_id = saathi_id.strip()

    if not saathi_id or not password:
        raise LoginError("Please enter your SAATHI ID and password.")

    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, password_hash FROM users WHERE saathi_id = ?",
            (saathi_id,),
        ).fetchone()

    if user is None:
        # Check against a fake hash anyway, so a missing account takes just as
        # long as a wrong password. Otherwise the reply speed would reveal
        # which SAATHI IDs exist.
        check_password(password, DUMMY_HASH)
        raise LoginError("Incorrect SAATHI ID or password.")

    if not check_password(password, user["password_hash"]):
        raise LoginError("Incorrect SAATHI ID or password.")

    return user["id"]


# ---------------------------------------------------------------- sessions

def create_session(user_id):
    """Start a login session and return its secret token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)

    with get_connection() as connection:
        connection.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
    return token


def get_user_for_session(token):
    """Return the logged-in user for a token, or None if it is invalid."""
    if not token:
        return None

    with get_connection() as connection:
        return connection.execute(
            """
            SELECT users.id, users.saathi_id, users.created_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
              AND sessions.expires_at > datetime('now')
            """,
            (token,),
        ).fetchone()


def delete_session(token):
    """End one session. That token stops working immediately."""
    if not token:
        return

    with get_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
