/* SAATHI - the chat screen. (5G: phone layout and polish) */

const list = document.getElementById("conversation-list");
const newChatButton = document.getElementById("new-chat");
const sidebarError = document.getElementById("sidebar-error");
const noSelection = document.getElementById("no-selection");
const conversationView = document.getElementById("conversation-view");
const conversationTitle = document.getElementById("conversation-title");
const messageArea = document.getElementById("messages");
const composer = document.getElementById("composer");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send");
const composerError = document.getElementById("composer-error");
const replyBar = document.getElementById("reply-bar");
const replyLabel = document.getElementById("reply-label");
const replyPreview = document.getElementById("reply-preview");
const layout = document.querySelector(".layout");
const announcer = document.getElementById("announcer");

// Phones show one screen at a time: the list, OR one conversation.
const phoneLayout = window.matchMedia("(max-width: 760px)");
// Touch screens use an on-screen keyboard, where Enter should make a new line.
const touchScreen = window.matchMedia("(pointer: coarse)");

let conversations = [];
let activeId = null;
let sending = false;
let pushedHistory = false;        // on phones, opening a chat adds a Back step
const drafts = new Map();         // unsent text, kept per conversation so switching never loses it
const messagesById = new Map();   // the open conversation's messages, so Reply can show their text
let replyingTo = null;            // the message being replied to, if any

// One place for every request to the backend.
// If the session has ended, go straight back to the login screen.
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401) {
    window.location.replace("/");
    throw new Error("Not logged in");
  }
  if (!response.ok) {
    const error = new Error(`Request failed (${response.status})`);
    error.status = response.status;
    // Keep the server's own explanation, e.g. "That message is too long".
    const body = await response.json().catch(() => ({}));
    if (typeof body.detail === "string") error.detail = body.detail;
    throw error;
  }
  if (response.status === 204) return null;   // 204: "done", with nothing to send back
  return response.json();
}

// ---------------------------------------------------------------- times

function isToday(date) {
  return date.toDateString() === new Date().toDateString();
}

function isYesterday(date) {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  return date.toDateString() === yesterday.toDateString();
}

// For the list: "00:13" (today), "Yesterday", or "12 Sep"
function formatListTime(iso) {
  const date = new Date(iso);
  if (isToday(date)) return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (isYesterday(date)) return "Yesterday";
  return date.toLocaleDateString([], { day: "numeric", month: "short" });
}

// For messages: just the time. The day is shown once, in a divider above.
function formatMessageTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// For the dividers between days: "Today", "Yesterday", or "12 Sep 2026"
function formatDay(iso) {
  const date = new Date(iso);
  if (isToday(date)) return "Today";
  if (isYesterday(date)) return "Yesterday";
  return date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

// ---------------------------------------------------------------- conversation list

function showListNote(text, isError = false) {
  const note = document.createElement("li");
  note.className = isError ? "list-note error" : "list-note";
  note.textContent = text;
  list.replaceChildren(note);
}

function renderList() {
  if (conversations.length === 0) {
    showListNote("No conversations yet.");
    return;
  }

  list.replaceChildren();
  for (const conversation of conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = conversation.id === activeId ? "conversation active" : "conversation";

    // textContent, never innerHTML: whatever the title says, the browser
    // shows it as plain text. It can never run as code.
    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conversation.title;

    const time = document.createElement("span");
    time.className = "conversation-time";
    time.textContent = formatListTime(conversation.updated_at);

    button.append(title, time);
    button.addEventListener("click", () => openConversation(conversation.id));

    const item = document.createElement("li");
    item.append(button);
    list.append(item);
  }
}

async function loadConversations() {
  showListNote("Loading…");
  try {
    conversations = await api("/api/conversations");
    renderList();
  } catch (error) {
    showListNote("Couldn't load your conversations.", true);
  }
}

// ---------------------------------------------------------------- messages

function showMessageNote(text, isError = false) {
  const note = document.createElement("p");
  note.className = isError ? "empty-note error" : "empty-note";
  note.textContent = text;
  messageArea.replaceChildren(note);
}

function buildMessage(message) {
  messagesById.set(message.id, message);

  const row = document.createElement("div");
  row.className = `message-row ${message.sender}`;   // "user" or "saathi"
  row.dataset.messageId = message.id;
  row.dataset.day = new Date(message.created_at).toDateString();   // for the day dividers

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.tabIndex = 0;   // reachable with the Tab key; Enter opens its actions

  // A reply shows a small quote of the message it answers.
  if (message.reply_to) {
    const quote = document.createElement("div");
    if (message.reply_to.available) {
      quote.className = "reply-quote";
      const who = document.createElement("span");
      who.className = "quote-sender";
      who.textContent = message.reply_to.sender === "user" ? "You" : "Saathi";
      const preview = document.createElement("span");
      preview.textContent = message.reply_to.preview;
      quote.append(who, preview);
    } else {
      quote.className = "reply-quote unavailable";
      quote.textContent = "Original message unavailable.";
    }
    bubble.append(quote);
  }

  const text = document.createElement("p");
  text.className = "bubble-text";
  text.textContent = message.content;

  const time = document.createElement("time");
  time.className = "bubble-time";
  time.dateTime = message.created_at;
  time.textContent = (message.saved ? "★ " : "") + formatMessageTime(message.created_at);

  bubble.append(text, time);
  row.append(bubble);
  return row;
}

function buildDayDivider(iso) {
  const label = document.createElement("span");
  label.textContent = formatDay(iso);
  const divider = document.createElement("div");
  divider.className = "day-divider";
  divider.append(label);
  return divider;
}

function renderMessages(messages) {
  messagesById.clear();
  if (messages.length === 0) {
    showMessageNote("This is the start of your conversation.");
    return;
  }

  const items = [];
  let lastDay = null;
  for (const message of messages) {
    const day = new Date(message.created_at).toDateString();
    if (day !== lastDay) {
      items.push(buildDayDivider(message.created_at));   // "Today", "Yesterday", ...
      lastDay = day;
    }
    items.push(buildMessage(message));
  }
  messageArea.replaceChildren(...items);
  messageArea.scrollTop = messageArea.scrollHeight;   // show the newest message
}

// Put the open conversation's number in the address (#12) so a refresh reopens it.
// On a phone, opening a chat also adds a step to the browser's history, so the
// phone's own Back button returns to the list - just like a messaging app.
function rememberInAddress(id) {
  if (phoneLayout.matches && !location.hash) {
    history.pushState(null, "", `#${id}`);
    pushedHistory = true;
  } else {
    history.replaceState(null, "", `#${id}`);
  }
}

// Close the open conversation: back to the list (phones) or the start screen.
function closeConversation() {
  if (activeId !== null) drafts.set(activeId, input.value);   // keep what you were typing
  activeId = null;
  pushedHistory = false;
  cancelReply();
  showConversationActions();
  history.replaceState(null, "", location.pathname);   // no #number any more
  conversationView.hidden = true;
  noSelection.hidden = false;
  layout.classList.remove("show-chat");
  renderList();
}

async function openConversation(id) {
  if (activeId !== null) drafts.set(activeId, input.value);   // keep what you were typing there
  activeId = id;
  rememberInAddress(id);
  layout.classList.add("show-chat");   // phones: switch from the list to this chat
  renderList();

  const known = conversations.find((c) => c.id === id);
  conversationTitle.textContent = known ? known.title : "";
  noSelection.hidden = true;
  conversationView.hidden = false;
  showMessageNote("Loading messages…");

  // Only now that the view is visible: a hidden box has no size to measure.
  input.value = drafts.get(id) || "";
  autoGrow();
  updateSendButton();
  showComposerError("");
  cancelReply();   // a reply belongs to one conversation
  showConversationActions();

  try {
    const data = await api(`/api/conversations/${id}`);
    // If you clicked a different conversation while this one was loading,
    // don't let this slower answer overwrite what you're looking at now.
    if (activeId !== id) return;
    conversationTitle.textContent = data.conversation.title;
    renderMessages(data.messages);
  } catch (error) {
    if (activeId !== id) return;
    conversationTitle.textContent = "";
    showMessageNote(
      error.status === 404
        ? "This conversation isn't available."
        : "Couldn't open this conversation. Please try again.",
      true
    );
  }
}

// ---------------------------------------------------------------- sending

// Is the person looking at the newest messages, or scrolled up to older ones?
function isNearBottom() {
  return messageArea.scrollHeight - messageArea.scrollTop - messageArea.clientHeight < 120;
}

// Follow new messages down only if the person was already at the bottom:
// someone who scrolled up to re-read something shouldn't be pulled away from it.
function scrollDownIf(wasNearBottom) {
  if (wasNearBottom) messageArea.scrollTop = messageArea.scrollHeight;
}

// Screen readers read the hidden announcer line aloud whenever it changes.
function announce(text) {
  announcer.textContent = text;
}

function appendMessage(message, { alwaysScroll = false } = {}) {
  const follow = alwaysScroll || isNearBottom();
  messageArea.querySelector(".empty-note")?.remove();   // "This is the start..." goes away

  // A new day since the last message? Add a divider first.
  const lastRow = [...messageArea.querySelectorAll(".message-row[data-day]")].pop();
  if (!lastRow || lastRow.dataset.day !== new Date(message.created_at).toDateString()) {
    messageArea.append(buildDayDivider(message.created_at));
  }

  messageArea.append(buildMessage(message));
  scrollDownIf(follow);
}

// Three softly pulsing dots while SAATHI's reply is on its way.
function showTyping() {
  const follow = isNearBottom();
  const bubble = document.createElement("div");
  bubble.className = "bubble typing";
  bubble.setAttribute("aria-label", "Saathi is typing");
  bubble.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));

  const row = document.createElement("div");
  row.id = "typing";
  row.className = "message-row saathi";
  row.append(bubble);
  messageArea.append(row);
  scrollDownIf(follow);
}

function hideTyping() {
  document.getElementById("typing")?.remove();
}

// New activity moves a conversation to the top of the list.
function bumpConversation(id, time) {
  const index = conversations.findIndex((c) => c.id === id);
  if (index === -1) return;
  const [conversation] = conversations.splice(index, 1);
  conversation.updated_at = time;
  conversations.unshift(conversation);
  renderList();
}

function showComposerError(text) {
  composerError.textContent = text;
  composerError.hidden = !text;
}

function updateSendButton() {
  sendButton.disabled = sending || input.value.trim() === "";
}

// The box grows with the text, up to the height set in the CSS.
function autoGrow() {
  input.style.height = "auto";
  // scrollHeight doesn't include the border, so add it back -
  // otherwise the box is always 2px too short and shows a scrollbar.
  const border = input.offsetHeight - input.clientHeight;
  input.style.height = `${input.scrollHeight + border}px`;
}

function showReplyFailed(conversationId, why) {
  const follow = isNearBottom();
  const note = document.createElement("div");
  note.className = "reply-failed";
  const text = document.createElement("span");
  // The server explains what happened in plain words; fall back to a safe one.
  text.textContent = why || "Saathi couldn't reply. Your message is saved.";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "text-button";
  retry.textContent = "Try again";
  retry.addEventListener("click", () => {
    note.remove();
    askForReply(conversationId);
  });
  note.append(text, retry);
  messageArea.append(note);
  scrollDownIf(follow);
  announce("Saathi couldn't reply. Your message is saved.");
}

// A bubble that fills up while SAATHI writes.
function startLiveBubble() {
  const text = document.createElement("p");
  text.className = "bubble-text";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.append(text);

  const row = document.createElement("div");
  row.id = "live-reply";
  row.className = "message-row saathi";
  row.append(bubble);

  messageArea.append(row);
  return text;
}

function removeLiveBubble() {
  document.getElementById("live-reply")?.remove();
}

// If streaming isn't available, ask for the whole reply in one go instead.
async function askForWholeReply(conversationId) {
  const reply = await api(`/api/conversations/${conversationId}/reply`, { method: "POST" });
  bumpConversation(conversationId, reply.created_at);
  if (activeId !== conversationId) return;
  appendMessage(reply);
  announce(`Saathi says: ${reply.content}`);
}

async function askForReply(conversationId) {
  showTyping();
  let live = null;
  let failed = null;

  try {
    const response = await fetch(`/api/conversations/${conversationId}/reply/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (response.status === 401) {
      window.location.replace("/");
      return;
    }
    if (!response.ok || !response.body) {
      await askForWholeReply(conversationId);   // older browser, or the stream never started
      return;
    }

    // The answer arrives as lines of small JSON objects, one piece at a time.
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop();            // the last piece may be half a line; wait for the rest
      for (const line of lines) {
        if (!line.trim()) continue;
        const part = JSON.parse(line);

        if (part.chunk && activeId === conversationId) {
          const follow = isNearBottom();
          hideTyping();
          if (!live) live = startLiveBubble();
          live.textContent += part.chunk;    // textContent, so it is always plain text
          scrollDownIf(follow);
        } else if (part.message) {
          bumpConversation(conversationId, part.message.created_at);
          if (activeId !== conversationId) continue;
          removeLiveBubble();
          appendMessage(part.message);       // swap in the saved one, so its menu works
          announce(`Saathi says: ${part.message.content}`);
        } else if (part.error) {
          failed = part.error;
        }
      }
    }
  } catch (error) {
    failed = failed || error.detail;
  }

  hideTyping();
  if (failed && activeId === conversationId) {
    removeLiveBubble();
    showReplyFailed(conversationId, failed);
  }
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || sending || activeId === null) return;

  const conversationId = activeId;
  const replyTo = replyingTo;   // fixed now, even if the person clicks around while it sends
  sending = true;
  updateSendButton();
  showComposerError("");

  try {
    // 1. Save the person's message first. Their words are kept no matter
    //    what happens to SAATHI's reply afterwards.
    const body = { content };
    if (replyTo) body.reply_to_message_id = replyTo.id;
    const message = await api(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    drafts.delete(conversationId);
    bumpConversation(conversationId, message.created_at);
    if (activeId === conversationId) {
      input.value = "";
      autoGrow();
      if (replyingTo === replyTo) cancelReply();
      appendMessage(message, { alwaysScroll: true });   // your own message: always show it
    }

    // 2. Then ask for SAATHI's reply.
    await askForReply(conversationId);
  } catch (error) {
    // The message was NOT saved - the text stays in the box, so nothing is lost.
    if (activeId === conversationId) {
      showComposerError(error.detail || "Couldn't send your message. Please try again.");
    }
  }

  sending = false;
  updateSendButton();
  input.focus();
});

input.addEventListener("input", () => {
  autoGrow();
  updateSendButton();
});

input.addEventListener("keydown", (event) => {
  // Enter sends; Shift+Enter starts a new line.
  // On touch screens Enter always makes a new line - people tap Send there,
  // and an on-screen keyboard has no easy Shift+Enter.
  // isComposing: people typing Telugu or Hindi with an input tool press Enter
  // to confirm a word. That Enter must never send the message.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !touchScreen.matches) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

// ---------------------------------------------------------------- choosing a message

function closeActions() {
  messageArea.querySelector(".message-actions")?.remove();
  messageArea.querySelector(".message-row.selected")?.classList.remove("selected");
}

function actionButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function openActions(row) {
  closeActions();
  row.classList.add("selected");
  const id = Number(row.dataset.messageId);
  const message = messagesById.get(id);

  const actions = document.createElement("div");
  actions.className = "message-actions";
  actions.append(
    actionButton("↩ Reply", () => startReply(id)),
    actionButton("📋 Copy", (event) => copyMessage(id, event.currentTarget)),
    actionButton(message.saved ? "★ Saved" : "⭐ Save", () => toggleSaved(id)),
    actionButton("🗑 Delete", () => askToDeleteMessage(actions, id)),
  );
  row.append(actions);
}

// The clipboard only works on "secure" pages - https, or this computer itself
// (127.0.0.1 counts) - and only right after a click. If the browser still says
// no, select the text so the person can press Ctrl+C themselves.
async function copyMessage(id, button) {
  try {
    await navigator.clipboard.writeText(messagesById.get(id).content);
    button.textContent = "✓ Copied";
    setTimeout(closeActions, 900);
  } catch (error) {
    const text = messageArea.querySelector(`[data-message-id="${id}"] .bubble-text`);
    window.getSelection().selectAllChildren(text);
    button.textContent = "Press Ctrl+C to copy";
  }
}

// Swap one message on screen for its updated version.
function replaceMessage(message) {
  const old = messageArea.querySelector(`[data-message-id="${message.id}"]`);
  if (old) old.replaceWith(buildMessage(message));
}

async function toggleSaved(id) {
  try {
    const updated = await api(`/api/conversations/${activeId}/messages/${id}/saved`, {
      method: "PUT",
      body: JSON.stringify({ saved: !messagesById.get(id).saved }),
    });
    replaceMessage(updated);
  } catch (error) {
    showComposerError("Couldn't update that message. Please try again.");
  }
}

// Deleting can't be undone, so ask first - right there in the menu.
function askToDeleteMessage(actions, id) {
  const question = document.createElement("span");
  question.className = "confirm-text";
  question.textContent = "Delete this message?";
  const yes = actionButton("Delete", () => deleteMessage(id));
  yes.classList.add("danger");
  actions.replaceChildren(question, yes, actionButton("Cancel", closeActions));
}

async function deleteMessage(id) {
  try {
    await api(`/api/conversations/${activeId}/messages/${id}`, { method: "DELETE" });
    if (replyingTo?.id === id) cancelReply();
    await refreshMessages();   // replies to it now show "Original message unavailable."
  } catch (error) {
    showComposerError("Couldn't delete that message. Please try again.");
  }
}

// Load the open conversation again, keeping your place on the screen.
async function refreshMessages() {
  const id = activeId;
  const scrollPosition = messageArea.scrollTop;
  const data = await api(`/api/conversations/${id}`);
  if (activeId !== id) return;
  renderMessages(data.messages);
  messageArea.scrollTop = scrollPosition;
}

function toggleActions(row) {
  if (row.classList.contains("selected")) {
    closeActions();
  } else {
    openActions(row);
  }
}

// Click a message to choose it. (Selecting its text with the mouse doesn't count.)
messageArea.addEventListener("click", (event) => {
  // A menu button that replaced itself (like Delete swapping to "Delete this
  // message?") is no longer on the page. That click was already handled.
  if (!event.target.isConnected) return;
  if (event.target.closest(".message-actions")) return;
  const bubble = event.target.closest(".bubble");
  if (!bubble || bubble.classList.contains("typing")) {
    closeActions();
    return;
  }
  if (window.getSelection().toString()) return;
  toggleActions(bubble.closest(".message-row"));
});

// Keyboard: Tab to a message, then Enter (or Space) to choose it.
messageArea.addEventListener("keydown", (event) => {
  const bubble = event.target.closest(".bubble");
  if (bubble && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    toggleActions(bubble.closest(".message-row"));
  }
});

// ---------------------------------------------------------------- replying

function startReply(messageId) {
  const message = messagesById.get(messageId);
  if (!message) return;
  closeActions();
  replyingTo = message;
  replyLabel.textContent = message.sender === "user" ? "Replying to your message" : "Replying to Saathi";
  replyPreview.textContent = message.content;   // textContent: shown as plain text, always
  replyBar.hidden = false;
  input.focus();
}

function cancelReply() {
  replyingTo = null;
  replyBar.hidden = true;
}

document.getElementById("reply-cancel").addEventListener("click", () => {
  cancelReply();
  input.focus();
});

// Escape closes the actions menu first, then cancels a reply.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (messageArea.querySelector(".message-actions")) {
    closeActions();
  } else if (replyingTo) {
    cancelReply();
  }
});

// ---------------------------------------------------------------- rename and delete a conversation

const chatHeader = document.querySelector(".chat-header");
const conversationActions = document.getElementById("conversation-actions");

// Put the header back to normal: the name, with Rename and Delete beside it.
function showConversationActions() {
  chatHeader.querySelector(".header-edit")?.remove();
  conversationTitle.hidden = false;
  conversationActions.hidden = false;
}

function headerButton(label, onClick, extraClass = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `text-button ${extraClass}`.trim();
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

document.getElementById("rename-conversation").addEventListener("click", () => {
  const field = document.createElement("input");
  field.type = "text";
  field.maxLength = 80;
  field.value = conversationTitle.textContent;
  field.setAttribute("aria-label", "Conversation name");

  const save = document.createElement("button");
  save.type = "submit";
  save.className = "text-button";
  save.textContent = "Save";

  const problem = document.createElement("span");
  problem.className = "header-error";

  const form = document.createElement("form");
  form.className = "header-edit";
  form.append(field, save, headerButton("Cancel", showConversationActions), problem);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const updated = await api(`/api/conversations/${activeId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: field.value }),
      });
      const known = conversations.find((c) => c.id === updated.id);
      if (known) known.title = updated.title;
      conversationTitle.textContent = updated.title;
      renderList();
      showConversationActions();
    } catch (error) {
      problem.textContent = error.detail || "Couldn't rename it. Please try again.";
    }
  });

  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();   // just cancel the rename, nothing else
      showConversationActions();
    }
  });

  conversationTitle.hidden = true;
  conversationActions.hidden = true;
  chatHeader.append(form);
  field.focus();
  field.select();
});

// Deleting a whole conversation can't be undone, so ask first.
document.getElementById("delete-conversation").addEventListener("click", () => {
  const question = document.createElement("span");
  question.className = "confirm-text";
  question.textContent = "Delete this conversation and all its messages?";

  const box = document.createElement("div");
  box.className = "header-edit";
  box.append(
    question,
    headerButton("Delete", deleteConversation, "danger"),
    headerButton("Cancel", showConversationActions),
  );
  conversationActions.hidden = true;
  chatHeader.append(box);
});

async function deleteConversation() {
  const id = activeId;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
  } catch (error) {
    showConversationActions();
    showComposerError("Couldn't delete this conversation. Please try again.");
    return;
  }
  conversations = conversations.filter((c) => c.id !== id);
  closeConversation();
  drafts.delete(id);   // after closing, which would have saved the draft
}

// ---------------------------------------------------------------- buttons

newChatButton.addEventListener("click", async () => {
  newChatButton.disabled = true;
  sidebarError.hidden = true;
  try {
    const conversation = await api("/api/conversations", { method: "POST" });
    conversations.unshift(conversation);   // newest goes to the top
    openConversation(conversation.id);
  } catch (error) {
    sidebarError.textContent = "Couldn't start a new conversation. Please try again.";
    sidebarError.hidden = false;
  }
  newChatButton.disabled = false;
});

document.getElementById("logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.replace("/");
});

// ---------------------------------------------------------------- start

api("/api/me")
  .then((user) => { document.getElementById("whoami").textContent = user.saathi_id; })
  .catch(() => {});   // if this fails, the name just stays blank

// The #number in the address says which conversation is open.
function openFromAddress() {
  const fromAddress = Number(location.hash.slice(1));
  if (Number.isInteger(fromAddress) && fromAddress > 0) {
    if (fromAddress !== activeId) openConversation(fromAddress);
  } else if (activeId !== null) {
    closeConversation();   // the #number is gone - e.g. Back on a phone
  }
}

// The ← button (phones only). If opening this chat added a history step,
// step back through it - exactly what the phone's own Back button does.
document.getElementById("back").addEventListener("click", () => {
  if (pushedHistory) {
    history.back();
  } else {
    closeConversation();
  }
});

// Changing only the #number doesn't reload the page, so listen for it too.
window.addEventListener("hashchange", openFromAddress);

// After a refresh, reopen the conversation named in the address.
loadConversations().then(openFromAddress);
