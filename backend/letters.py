"""
SAATHI - writing Telugu in English letters.

You type "Repu exam undi" and SAATHI should answer the same way. The model
cannot do that: asked for Telugu in a-z letters it invents non-words
("manchju", "sunchestaanu"). But it writes beautiful Telugu script.

So the work is split. The model writes the Telugu. This file changes only the
LETTERS, never the words - and because it is plain Python, it cannot invent
anything. "చాలా బాధగా ఉంది" becomes "chala badhaga undi".

Hindi needs none of this: the same model writes natural Hinglish by itself.
"""

TELUGU_FIRST, TELUGU_LAST = 0x0C00, 0x0C7F

# Vowels standing on their own, at the start of a word.
VOWELS = {
    "అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ee", "ఉ": "u", "ఊ": "oo",
    "ఋ": "ru", "ఎ": "e", "ఏ": "e", "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au",
}

# The same vowels as marks hanging off a consonant.
MATRAS = {
    "ా": "aa", "ి": "i", "ీ": "ee", "ు": "u", "ూ": "oo",
    "ృ": "ru", "ె": "e", "ే": "e", "ై": "ai",
    "ొ": "o", "ో": "o", "ౌ": "au",
}

# Consonants. Each one carries a built-in "a" unless a mark says otherwise.
CONSONANTS = {
    "క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "ng",
    "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh", "ఞ": "ny",
    "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n",
    "త": "t", "థ": "th", "ద": "d", "ధ": "dh", "న": "n",
    "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m",
    "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ళ": "l",
    "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h",
}

VIRAMA = "్"      # "no vowel here"
ANUSVARA = "ం"    # the dot: an n or m sound
VISARGA = "ః"     # a light h sound

# Before these sounds the dot is an "m"; everywhere else it is an "n".
# Without this, "ఉంది" comes out as "umdi" instead of "undi".
M_BEFORE = ("p", "ph", "b", "bh", "m")


# Telugu and Hindi typed in English letters. Telling the model to "use the same
# letters" was not enough: a-z letters describe English too, so it simply
# translated "Ela unnavu?" into "Hello there. How are you?". SAATHI has to
# recognise the language itself and say so plainly.
#
# These are everyday words that almost never appear in an English sentence, so
# one of them is enough to tell what is being written.
TELUGU_IN_ENGLISH = {
    "ela", "unnav", "unnava", "unnavu", "unnanu", "unnaru", "undi", "unna",
    "nenu", "nuvvu", "meeru", "naku", "naaku", "ninnu", "mee", "nee",
    "chala", "chaala", "konchem", "kani", "kaani", "ledu", "kuda", "kada",
    "enti", "emiti", "emi", "cheppu", "cheppandi", "telusu", "teliyadu",
    "repu", "nedu", "ninna", "bagunnava", "bagundi", "bagane", "baga",
    "chesanu", "chestunnanu", "chaduvu", "chaduvuthunnanu", "avvatledu",
    "ayyo", "ammo", "anipinchindi", "anukuntunnanu", "vellali", "ravali",
}
HINDI_IN_ENGLISH = {
    "kaise", "kaisa", "kaisi", "tum", "tumhara", "tumhe", "mujhe", "mera",
    "meri", "nahi", "nahin", "kya", "acha", "accha", "thik", "theek",
    "bahut", "aaj", "kal", "yaar", "haan", "bhai", "kuch", "kuchh",
    "karo", "karna", "raha", "rahi", "rahe", "gaya", "gayi", "hoon", "hain",
    "matlab", "abhi", "phir", "sab", "lekin", "magar", "bohot", "thoda",
}


def written_in_english_letters(text, words):
    """Is this message one of our languages, typed in a-z letters?"""
    plain = "".join(sign.lower() if sign.isalpha() or sign.isspace() else " " for sign in text)
    return bool(words & set(plain.split()))


def has_telugu(text):
    return any(TELUGU_FIRST <= ord(sign) <= TELUGU_LAST for sign in text)


def to_english_letters(text, casual=True):
    """Telugu script -> a-z letters. Only the spelling changes.

    casual=True writes the shorter vowels people actually type:
    "chaalaa" becomes "chala", the way you would text it.
    """
    out = []
    place = 0
    while place < len(text):
        sign = text[place]

        if sign in CONSONANTS:
            out.append(CONSONANTS[sign])
            following = text[place + 1] if place + 1 < len(text) else ""
            if following == VIRAMA:
                place += 2                     # no vowel at all
                continue
            if following in MATRAS:
                out.append(MATRAS[following])
                place += 2
                continue
            out.append("a")                    # the built-in vowel
            place += 1
            continue

        if sign in VOWELS:
            out.append(VOWELS[sign])
        elif sign == ANUSVARA:
            after = text[place + 1] if place + 1 < len(text) else ""
            sound = CONSONANTS.get(after)
            # At the end of a word there is no following sound to borrow from,
            # and Telugu says "m" there: "siddham", "namaskaram".
            out.append("m" if sound is None or sound in M_BEFORE else "n")
        elif sign == VISARGA:
            out.append("h")
        elif sign in MATRAS:
            out.append(MATRAS[sign])           # a stray mark; keep its sound
        else:
            out.append(sign)                   # spaces, punctuation, English words
        place += 1

    written = "".join(out)
    if casual:
        written = written.replace("aa", "a").replace("oo", "u")
    return written


def wants_english_letters(text):
    """Is this Telugu written in a-z letters, rather than Telugu script?"""
    return not has_telugu(text) and written_in_english_letters(text, TELUGU_IN_ENGLISH)


def person_writes_in_english(messages):
    """Does this person type Telugu in a-z letters in this conversation?

    If they ever use Telugu script themselves, they are happy reading it, so
    nothing is changed. Otherwise SAATHI's Telugu is shown in their letters.
    """
    theirs = [m["content"] for m in messages if m.get("sender") == "user"]
    if any(has_telugu(one) for one in theirs):
        return False
    return any(wants_english_letters(one) for one in theirs)


class AsTheyWrite:
    """Converts a reply as it streams, a whole word at a time.

    The pieces Ollama sends can split a word down the middle, and half a
    Telugu letter cannot be spelled on its own - so an unfinished word waits
    here until its space arrives.
    """

    def __init__(self, active):
        self.active = active
        self.unfinished = ""

    def feed(self, piece):
        if not self.active:
            return piece
        self.unfinished += piece
        ended = self.unfinished.rfind(" ")
        if ended == -1:
            return ""
        ready, self.unfinished = self.unfinished[:ended + 1], self.unfinished[ended + 1:]
        return to_english_letters(ready)

    def finish(self):
        if not self.active or not self.unfinished:
            return ""
        last, self.unfinished = self.unfinished, ""
        return to_english_letters(last)
