"""
SAATHI - the context builder.

The local AI model has no memory. Every time SAATHI asks it for a reply, the
model starts from nothing: it does not remember the last message, this
conversation, or you. Everything it seems to "remember" is here, in this file,
because SAATHI tells it the conversation again on every single message.

This file has one job: turn stored messages into exactly what the AI is told.

chat.py is about your data - who owns which conversation, what is saved, what
was deleted. This file never touches the database and never checks who you
are. That stays in chat.py, in one place, so it cannot be weakened from here.

Nothing here reaches the internet. It only arranges words.
"""

import unicodedata

# --------------------------------------------------------------- counting the cost
#
# The model does not read characters. It reads TOKENS - small pieces of words -
# and a token is worth a very different number of characters in each script.
# Measured on this computer with gemma3:4b:
#
#     English      4.7 characters per token
#     Hindi        4.2
#     Tenglish     3.6   (Telugu or Hindi typed in a-z letters)
#     Telugu       2.8   <- the same sentence costs nearly twice as much
#
# Counting characters would quietly give a Telugu conversation far less room
# than an English one. So SAATHI counts tokens instead, using the smallest
# (most expensive) measured value for each script, so the guess is never low.
# Telugu is set below its measured 2.76 on purpose: at 2.7 a real sample came
# out one token under, and an under-estimate is the dangerous direction.
TELUGU_PER_TOKEN = 2.5
DEVANAGARI_PER_TOKEN = 4.0
OTHER_PER_TOKEN = 4.0          # plain English, and Telugu/Hindi in a-z letters
PER_MESSAGE_COST = 5           # the "who said this" marks around every message


def estimate_tokens(text):
    """About how many tokens this text will cost the model.

    Mixed messages are counted letter by letter, so "Repu exam undi, konchem
    nervous ga undi" and "రేపు పరీక్ష ఉంది" are each costed properly.
    """
    telugu = devanagari = 0
    for sign in text:
        place = ord(sign)
        if 0x0C00 <= place <= 0x0C7F:
            telugu += 1
        elif 0x0900 <= place <= 0x097F:
            devanagari += 1
    other = len(text) - telugu - devanagari
    return int(telugu / TELUGU_PER_TOKEN
               + devanagari / DEVANAGARI_PER_TOKEN
               + other / OTHER_PER_TOKEN) + PER_MESSAGE_COST


# How much conversation SAATHI hands over at once, in tokens.
#
# Two limits meet here. The model can hold 8192 tokens at once (CONTEXT_WINDOW
# in ai.py), and the personality (~330) and the reply itself (~400) need room
# out of that. But the real limit on this computer is TIME: it reads about 40
# tokens a second, so 1500 tokens is about 35 seconds of reading in the worst
# case - the first reply in a conversation the model hasn't seen recently.
# After that Ollama remembers the conversation, and each new message only costs
# its own few tokens. Step 7C shrinks the old part of long chats into a summary,
# which is how a long conversation stays affordable rather than by raising this.
CONTEXT_TOKENS = 1500

# How much of a replied-to message to quote, so one long message cannot
# swallow the whole budget on its own.
QUOTE_LENGTH = 400


def shorten_quote(text, limit=QUOTE_LENGTH):
    """The first part of a long message, with … to show it was cut."""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def to_ai_messages(recent, budget=CONTEXT_TOKENS):
    """Stored messages -> the labelled list the AI reads.

    Two things happen here, and both matter:

    1. Every message gets a role. The AI calls SAATHI's own past replies
       "assistant" and the person's messages "user". Get this wrong and the
       model believes it said what the person said.

    2. The budget is spent from the NEWEST message backwards, so the newest
       messages always get through and the oldest are the ones dropped. A
       conversation is far easier to follow from its recent end than its start.
    """
    chosen, used = [], 0
    for message in reversed(recent):                 # newest first
        used += estimate_tokens(message["content"])
        if used > budget and chosen:                 # always keep at least one
            break
        chosen.append({
            "role": "assistant" if message["sender"] == "saathi" else "user",
            "content": message["content"],
        })
    chosen.reverse()                                 # back to oldest-first, like reading
    return chosen


def with_reply_context(messages, replying_to):
    """Tell the AI which earlier message the newest one is answering.

    The note goes on the NEWEST message, so the AI reads "what they are
    answering" right beside "what they said". A deleted original cannot leak
    here: its words were erased from the database when it was deleted, so
    there is nothing left to quote.
    """
    if not replying_to or not messages:
        return messages

    if replying_to.get("available"):
        whose = "yours" if replying_to["sender"] == "saathi" else "their own"
        note = (f'[Replying to this earlier message of {whose}: '
                f'"{shorten_quote(replying_to["content"])}"]')
    else:
        note = "[Replying to an earlier message. Original message unavailable - it was deleted.]"

    newest = messages[-1]
    return messages[:-1] + [{**newest, "content": f'{note}\n\n{newest["content"]}'}]


# ------------------------------------------------- finding an older message (7D)
#
# The summary keeps the shape of a long conversation, but it is only a few
# sentences, so a detail mentioned once - a name, a date, a link - will not
# survive in it. This brings back the actual older message when the person
# asks about something it mentioned.
#
# It searches ONLY this conversation. There is no search across conversations
# and no memory of the person: that is Step 8, and it is a different promise.

# Everyday words that two unrelated messages share by accident. Matching on
# them would "find" any message at all.
STOP_WORDS = {
    "the", "and", "for", "you", "your", "was", "were", "are", "this", "that",
    "have", "has", "had", "with", "about", "what", "when", "where", "who",
    "why", "how", "did", "not", "but", "from", "they", "them", "there", "here",
    "just", "like", "get", "got", "can", "will", "would", "should", "could",
    "thing", "things", "something", "anything", "okay", "yes", "not",
    "some", "any", "out", "its", "it's", "i'm", "am", "been", "being", "than",
    "then", "too", "very", "really", "again", "still", "now", "today",
}

WORD_LENGTH = 3          # shorter words carry too little meaning to match on
SHARED_WORDS_NEEDED = 2  # one shared word is usually a coincidence
RECALL_MESSAGES = 2      # bring back at most this many, to stay affordable
RECALL_QUOTE = 300       # and only this much of each


def meaningful_words(text):
    """The words worth matching on: long enough, and not everyday filler.

    Punctuation becomes spaces, so "SAATHI." and "SAATHI?" are the same word.

    A word is built from letters, digits AND marks. The marks matter: Telugu
    and Hindi write their vowels as marks attached to a letter, and Python's
    isalnum() calls those marks "not alphanumeric". Using isalnum() here broke
    ప్రాజెక్ట్ into five separate letters - each too short to keep - so Telugu
    and Hindi could never have been matched on at all.
    """
    letters = "".join(sign.lower() if unicodedata.category(sign)[0] in "LMN" else " "
                      for sign in text)
    return {word for word in letters.split()
            if len(word) >= WORD_LENGTH and word not in STOP_WORDS}


def find_relevant(question, older):
    """Older messages from THIS conversation that share real words with the question.

    Deliberately simple: no search index, no extra database, nothing to keep
    in step. For one person's conversation this is both fast enough and easy
    to check by eye - you can always see WHY a message was brought back.
    """
    wanted = meaningful_words(question)
    if not wanted:
        return []

    found = []
    for message in older:
        shared = wanted & meaningful_words(message["content"])
        if len(shared) >= SHARED_WORDS_NEEDED:
            found.append((len(shared), message["id"], message))

    # Best match first; where two match equally well, the more recent one.
    found.sort(key=lambda scored: (scored[0], scored[1]), reverse=True)
    return [message for _, _, message in found[:RECALL_MESSAGES]]


def with_recalled(messages, recalled):
    """Put the older messages that matter in front of the newest message.

    They go on the newest message rather than with the personality, because
    the personality is the same every turn and Ollama remembers it. Changing
    it would throw that memory away and make every reply slow again.
    """
    if not recalled or not messages:
        return messages

    lines = []
    for message in recalled:
        who = "You said" if message["sender"] == "saathi" else "They said"
        lines.append(f'{who} earlier: "{shorten_quote(message["content"], RECALL_QUOTE)}"')
    note = "[From earlier in this conversation. " + " ".join(lines) + "]"

    newest = messages[-1]
    return messages[:-1] + [{**newest, "content": f'{note}\n\n{newest["content"]}'}]


def build(recent, replying_to=None, summary=None, older=None):
    """THE context builder: a conversation in, what the AI is told out.

    One place, one order:
        1. the summary of the older part takes its share of the budget (7C)
        2. the recent messages fill what is left, newest first
        3. older messages that match what was just asked come back (7D)
        4. the note about which message is being answered goes on the newest

    `recent` is oldest-first, exactly as chat.py read it from the database.
    `older` is everything before that, searched but never all sent.

    Gives back two things, because they travel to the model differently:
        messages   - the conversation itself
        background - what SAATHI already knows, which rides with its
                     personality rather than pretending to be a message
    """
    room = CONTEXT_TOKENS - (estimate_tokens(summary) if summary else 0)
    messages = to_ai_messages(recent, room)

    recalled = []
    if older and messages:
        recalled = find_relevant(messages[-1]["content"], older)
        messages = with_recalled(messages, recalled)

    messages = with_reply_context(messages, replying_to)
    return {"messages": messages, "background": summary or None, "recalled": recalled}


def describe(recent, messages, summary=None):
    """A plain-words account of what the builder decided, for looking at.

    Used by tests/show_context.py. It changes nothing - it only reports.
    """
    characters = sum(len(message["content"]) for message in messages)
    tokens = sum(estimate_tokens(message["content"]) for message in messages)
    if summary:
        tokens += estimate_tokens(summary)
    return {
        "a summary of the older part exists": "yes" if summary else "no",
        "messages read from the database": len(recent),
        "messages sent to the AI": len(messages),
        "messages left out (too old for the budget)": len(recent) - len(messages),
        "characters sent": characters,
        "tokens that costs": f"{tokens} of {CONTEXT_TOKENS} allowed",
        "roughly how long the model takes to read it": f"{tokens / 40:.0f}s if it has been away",
        "a reply note is attached": bool(messages) and messages[-1]["content"].startswith("["),
    }
