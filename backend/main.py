"""
SAATHI - backend server
Step 6D: SAATHI's replies now come from the local AI.
"""

import json
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import ai, auth, chat, database

# Worked out from this file's location, so it never depends on which
# folder you started the server from.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The name of the cookie holding the session token.
SESSION_COOKIE = "saathi_session"

app = FastAPI(
    title="SAATHI",
    description="A private AI companion that runs on your own computer.",
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.middleware("http")
async def always_check_for_newer_files(request, call_next):
    """Browsers keep copies of CSS and JS files. 'no-cache' means: you may keep
    a copy, but ask us first whether it changed - so an edit shows up on the
    very next refresh instead of the old file being used."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Make sure the database file and its tables exist before anything else runs.
database.init_db()


# ---------------------------------------------------------------- who is logged in

def require_user(saathi_session: str | None = Cookie(default=None)):
    """Guard for API routes: stop the request unless someone is logged in."""
    user = auth.get_user_for_session(saathi_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return user


# ---------------------------------------------------------------- models

class SignupRequest(BaseModel):
    saathi_id: str
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    saathi_id: str
    password: str


class SendMessageRequest(BaseModel):
    content: str
    reply_to_message_id: int | None = None   # set when replying to one specific message


class SaveRequest(BaseModel):
    saved: bool


class RenameRequest(BaseModel):
    title: str


# ---------------------------------------------------------------- pages

@app.get("/")
def login_page():
    """The opening SAATHI screen."""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/signup")
def signup_page():
    """The create-an-account screen."""
    return FileResponse(FRONTEND_DIR / "signup.html")


@app.get("/home")
def home_page(saathi_session: str | None = Cookie(default=None)):
    """The SAATHI home screen - only for people who are logged in."""
    if auth.get_user_for_session(saathi_session) is None:
        # Not logged in: send them to the login screen instead.
        return RedirectResponse("/", status_code=303)

    # no-store: never keep a copy of this page, so the Back button
    # cannot show it again after logout.
    return FileResponse(FRONTEND_DIR / "home.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask every site for a tab icon at this address. Hand them the lotus."""
    return FileResponse(FRONTEND_DIR / "images" / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- accounts

@app.post("/api/signup")
def signup(request: SignupRequest):
    """Create a new SAATHI account."""
    try:
        auth.create_user(
            request.saathi_id,
            request.password,
            request.confirm_password,
        )
    except auth.SignupError as error:
        # 400 means "your details were not acceptable" - a normal, expected answer.
        raise HTTPException(status_code=400, detail=str(error))

    return {"message": "Your Saathi is ready."}


@app.post("/api/login")
def login(request: LoginRequest, response: Response):
    """Check the details and start a login session."""
    try:
        user_id = auth.login_user(request.saathi_id, request.password)
    except auth.LoginError as error:
        # 401 means "we could not confirm who you are".
        raise HTTPException(status_code=401, detail=str(error))

    token = auth.create_session(user_id)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,       # JavaScript cannot read it
        samesite="lax",      # other websites cannot use it
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )
    return {"message": "Welcome back."}


@app.get("/api/me")
def me(user=Depends(require_user)):
    """Who is logged in right now?"""
    return {"saathi_id": user["saathi_id"], "member_since": user["created_at"]}


@app.post("/api/logout")
def logout(response: Response, saathi_session: str | None = Cookie(default=None)):
    """End this session: delete it from the database AND from the browser."""
    auth.delete_session(saathi_session)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return {"message": "You have logged out."}


# ---------------------------------------------------------------- conversations

@app.get("/api/conversations")
def get_conversations(user=Depends(require_user)):
    """The logged-in person's conversations."""
    return chat.list_conversations(user["id"])


@app.post("/api/conversations", status_code=201)
def new_conversation(user=Depends(require_user)):
    """Start a new conversation for the logged-in person."""
    return chat.create_conversation(user["id"])


@app.get("/api/conversations/{conversation_id}")
def open_conversation(conversation_id: int, user=Depends(require_user)):
    """One of the logged-in person's conversations, with its messages."""
    result = chat.get_conversation(user["id"], conversation_id)
    if result is None:
        # The same answer for "doesn't exist" and "belongs to someone else",
        # so nobody can discover which conversation numbers are in use.
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return result


@app.post("/api/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: int, request: SendMessageRequest, user=Depends(require_user)):
    """Save a message the logged-in person wrote."""
    try:
        message = chat.add_user_message(
            user["id"], conversation_id, request.content, request.reply_to_message_id
        )
    except chat.MessageError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if message is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return message


@app.post("/api/conversations/{conversation_id}/reply", status_code=201)
def reply(conversation_id: int, user=Depends(require_user)):
    """Ask SAATHI to answer the latest message, and wait for the whole reply."""
    try:
        message = chat.add_saathi_reply(user["id"], conversation_id)
    except chat.NothingToReplyTo as error:
        # 409 means "that doesn't make sense right now": SAATHI already answered.
        raise HTTPException(status_code=409, detail=str(error))
    except ai.AIUnavailable as error:
        # 503 means "can't do this at the moment". The message is already
        # written for a person to read; the technical detail went to the log.
        raise HTTPException(status_code=503, detail=str(error))
    if message is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return message


@app.post("/api/conversations/{conversation_id}/reply/stream")
def reply_streaming(conversation_id: int, user=Depends(require_user)):
    """The same reply, but sent word by word as the AI writes it.

    Each line is one small JSON object:
        {"chunk": "..."}      a piece of the reply, as it arrives
        {"message": {...}}    the finished, saved message
        {"error": "..."}      something went wrong; the text is safe to show
    """
    try:
        messages = chat.prepare_reply(user["id"], conversation_id)
    except chat.NothingToReplyTo as error:
        raise HTTPException(status_code=409, detail=str(error))
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    def lines():
        pieces = []
        try:
            for piece in ai.stream_reply(messages):
                pieces.append(piece)
                yield json.dumps({"chunk": piece}) + "\n"
        except ai.AIUnavailable as error:
            yield json.dumps({"error": str(error)}) + "\n"
            return

        text = "".join(pieces).strip()
        if not text:
            yield json.dumps({"error": ai.NO_WORDS}) + "\n"
            return

        saved = chat.save_saathi_message(user["id"], conversation_id, text)
        if saved is None:     # the conversation was deleted while SAATHI was writing
            return
        yield json.dumps({"message": saved}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, request: RenameRequest, user=Depends(require_user)):
    """Give one of the logged-in person's conversations a new name."""
    try:
        conversation = chat.rename_conversation(user["id"], conversation_id, request.title)
    except chat.TitleError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, user=Depends(require_user)):
    """Delete a conversation and every message in it."""
    if not chat.delete_conversation(user["id"], conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return Response(status_code=204)   # 204: done, and nothing to send back


# ---------------------------------------------------------------- message actions

@app.put("/api/conversations/{conversation_id}/messages/{message_id}/saved")
def save_message(conversation_id: int, message_id: int, request: SaveRequest,
                 user=Depends(require_user)):
    """Star (saved: true) or un-star (saved: false) a message."""
    message = chat.set_saved(user["id"], conversation_id, message_id, request.saved)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return message


@app.delete("/api/conversations/{conversation_id}/messages/{message_id}", status_code=204)
def delete_message(conversation_id: int, message_id: int, user=Depends(require_user)):
    """Erase one message's words for good."""
    if not chat.delete_message(user["id"], conversation_id, message_id):
        raise HTTPException(status_code=404, detail="Message not found.")
    return Response(status_code=204)
