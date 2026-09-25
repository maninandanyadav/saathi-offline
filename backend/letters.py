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


# Letters and marks the first tables missed. Without these a Telugu letter
# passed straight through unconverted - "naaku" came out as "naౠku" with
# the raw script still in it.
EXTRAS = {
    "ఁ": "n", "ఀ": "n", "ఄ": "n", "ౝ": "n",   # the nasal signs
    "ఌ": "lu", "ౡ": "lu", "ౢ": "lu", "ౣ": "lu",   # vocalic l
    "ౠ": "ru", "ౄ": "ru",                       # vocalic rr
    "ౘ": "ts", "ౙ": "dz", "ౚ": "r", "ఴ": "l",   # rarer consonants
    "఼": "", "ఽ": "", "ౕ": "", "ౖ": "",        # marks with no sound of their own
    "౦": "0", "౧": "1", "౨": "2", "౩": "3", "౪": "4",
    "౫": "5", "౬": "6", "౭": "7", "౮": "8", "౯": "9",
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


DEVANAGARI_FIRST, DEVANAGARI_LAST = 0x0900, 0x097F


def has_devanagari(text):
    return any(DEVANAGARI_FIRST <= ord(sign) <= DEVANAGARI_LAST for sign in text)


def to_english_letters(text, casual=True, capitalise=True):
    """Telugu script -> a-z letters, one word at a time.

    Only words actually written in Telugu are touched. That matters: SAATHI's
    mixed replies contain English words, and shortening the vowels of the
    whole line turned "good" into "gud", "school" into "schul" and "book"
    into "buk". A word with no Telugu in it is now left exactly as it is.

    casual=True writes the shorter vowels people actually type:
    "chaalaa" becomes "chala", the way you would text it.
    """
    pieces = []
    for word in text.split(" "):
        pieces.append(one_word(word, casual) if has_telugu(word) else word)
    joined = " ".join(pieces)
    return capitalised(joined)[0] if capitalise else joined


def capitalised(text, start_of_sentence=True):
    """A capital letter at the start, and after each . ? or !

    Gives back the text AND whether the next piece begins a new sentence,
    because a streamed reply arrives in pieces: without carrying that along,
    every piece looked like a fresh start and "chala badhaga undi" came out
    as "Chala Badhaga Undi".
    """
    letters_out = list(text)
    for place, sign in enumerate(letters_out):
        if start_of_sentence and sign.isalpha():
            letters_out[place] = sign.upper()
            start_of_sentence = False
        elif sign in ".?!":
            start_of_sentence = True
    return "".join(letters_out), start_of_sentence


def one_word(text, casual=True):
    """The letter-by-letter work, for a single Telugu word."""
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
        elif sign in EXTRAS:
            out.append(EXTRAS[sign])           # the rarer letters and the digits
        elif sign == VIRAMA:
            pass                               # alone it means "no vowel": no sound
        else:
            out.append(sign)                   # punctuation, and anything else
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
        self.new_sentence = True      # carried between pieces, so only real
                                      # sentence starts get a capital

    def convert(self, ready):
        plain = to_english_letters(ready, capitalise=False)
        shown, self.new_sentence = capitalised(plain, self.new_sentence)
        return shown

    def feed(self, piece):
        if not self.active:
            return piece
        self.unfinished += piece
        ended = self.unfinished.rfind(" ")
        if ended == -1:
            return ""
        ready, self.unfinished = self.unfinished[:ended + 1], self.unfinished[ended + 1:]
        return self.convert(ready)

    def finish(self):
        if not self.active or not self.unfinished:
            return ""
        last, self.unfinished = self.unfinished, ""
        return self.convert(last)


# ------------------------------------------------- the language profile (7.5B)
#
# The old test asked "which language is this?" and stopped at the first clue,
# so ONE Telugu word turned an English sentence into Telugu:
#
#     "Today college lo presentation undi"  ->  pure Telugu
#
# This asks a better question: what is EVERY word, and what is the balance?
# Each word votes for its own language, and the counts decide. A message is
# then allowed to be what it really is - mostly English, with Telugu grammar.
#
# Grammar words matter most, because they are the ones a person cannot borrow.
# "college" and "exam" are used inside every Indian language; "lo", "undi",
# "hai" and "the" belong to one language each and give the sentence away.

# Common English words a student actually types. Function words first, then
# the everyday nouns that appear in mixed sentences.
ENGLISH_WORDS = {
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "can", "could", "will", "would",
    "should", "may", "might", "must", "i", "you", "he", "she", "it", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his", "our",
    "their", "this", "that", "these", "those", "to", "of", "in", "on", "at",
    "for", "with", "from", "by", "about", "into", "over", "after", "before",
    "and", "or", "but", "so", "if", "then", "than", "because", "how", "what",
    "when", "where", "why", "who", "which", "not", "no", "yes", "very", "just",
    "some", "any", "all", "more", "most", "too", "also", "still", "again",
    "today", "tomorrow", "yesterday", "morning", "night", "day", "week",
    "time", "now", "later", "soon", "college", "class", "classes", "exam",
    "exams", "test", "presentation", "project", "assignment", "work", "study",
    "studying", "homework", "friend", "friends", "family", "home", "hostel",
    "room", "phone", "food", "sleep", "tired", "busy", "free", "good", "bad",
    "fine", "okay", "ok", "nice", "great", "sorry", "thanks", "thank", "please",
    "going", "went", "come", "came", "get", "got", "know", "think", "feel",
    "feeling", "need", "want", "like", "love", "hate", "help", "talk", "tell",
    "say", "said", "make", "made", "take", "took", "give", "gave", "bro",
    "dude", "man", "guys", "hey", "hi", "hello",
}

# A few more grammar words for each, the ones that carry a sentence.
TELUGU_GRAMMAR = {
    "lo", "loki", "nunchi", "ki", "ku", "tho", "to", "kante", "ante", "anta",
    "ayina", "ayyindi", "avutundi", "cheyyi", "cheyyali", "cheyyadam", "undali",
    "undedi", "ledhu", "kavali", "vachindi", "vellanu", "chusanu", "ippudu",
    "appudu", "ekkada", "enduku", "evaru", "emo", "kadha", "ga", "gaa",
}
HINDI_GRAMMAR = {
    "hai", "hain", "ho", "hu", "hun", "tha", "thi", "the", "ka", "ke", "ki",
    "ko", "se", "mein", "par", "aur", "ya", "bhi", "hi", "to", "kar", "kuchh",
    "koi", "kab", "kahan", "kaun", "kyun", "kyon", "jab", "tab", "wahan",
}

# Words that belong to more than one of the lists above ("ki", "to", "the",
# "ho") cannot decide anything, so they are ignored as evidence entirely.
TELUGU_ALL = TELUGU_IN_ENGLISH | TELUGU_GRAMMAR
HINDI_ALL = HINDI_IN_ENGLISH | HINDI_GRAMMAR
SHARED = (TELUGU_ALL & HINDI_ALL) | (TELUGU_ALL & ENGLISH_WORDS) | (HINDI_ALL & ENGLISH_WORDS)


def words_in(text):
    """The plain lowercase words of a message, punctuation removed."""
    letters_only = "".join(sign.lower() if sign.isalpha() or sign.isspace() else " "
                           for sign in text)
    return letters_only.split()


def count_votes(text):
    """How many words belong to each language, and which ones."""
    votes = {"telugu": [], "hindi": [], "english": [], "unknown": []}
    for word in words_in(text):
        if word in SHARED:
            continue                       # could be either: no vote
        if word in TELUGU_ALL:
            votes["telugu"].append(word)
        elif word in HINDI_ALL:
            votes["hindi"].append(word)
        elif word in ENGLISH_WORDS:
            votes["english"].append(word)
        else:
            votes["unknown"].append(word)  # a name, a rare word, a typo
    return votes


STYLE_NAMES = {
    ("telugu", "telugu"): "Telugu",
    ("telugu", "latin"): "Telugu in English letters",
    ("hindi", "devanagari"): "Hindi",
    ("hindi", "latin"): "Hinglish",
    ("english", "latin"): "English",
}


def profile(text, earlier=None):
    """What language is this, in what letters, and is it mixed?

    Gives back a small description instead of one word, because one word
    cannot say "mostly English, with Telugu grammar":

        language    telugu / hindi / english   - whose words there are most of
        script      telugu / devanagari / latin
        mixed       True when a second language is really present
        with        that second language, or None
        style       a plain name for the whole thing
        confidence  high / medium / low - how much evidence there was
        from_earlier  True when the message was too short to judge alone
        votes       which words voted for what, so you can check the working

    `earlier` is the person's recent messages. A message like "bro" or "haa"
    carries almost no evidence, and guessing English for it would throw a
    Telugu conversation into English. In that case the conversation decides.
    """
    script = ("telugu" if has_telugu(text)
              else "devanagari" if has_devanagari(text)
              else "latin")

    votes = count_votes(text)
    strength = {name: len(found) for name, found in votes.items()}

    # A native script settles the language by itself - nobody types Telugu
    # letters by accident.
    if script == "telugu":
        language, decided = "telugu", strength["telugu"] + 3
    elif script == "devanagari":
        language, decided = "hindi", strength["hindi"] + 3
    else:
        counts = {"telugu": strength["telugu"], "hindi": strength["hindi"],
                  "english": strength["english"]}
        language = max(counts, key=counts.get)
        decided = counts[language]
        if decided == 0:
            language = None            # nothing to go on at all

    # Too little evidence: ask the conversation rather than guess.
    from_earlier = False
    if language is None or decided < 2:
        borrowed = from_recent(earlier)
        if borrowed is not None:
            if language is None or borrowed["language"] != language:
                from_earlier = True
                language = borrowed["language"]
                if script == "latin":
                    script = borrowed["script"] if borrowed["script"] != "latin" else "latin"
        elif language is None:
            language, decided = "english", 0

    # Is a second language really present, or just a borrowed word or two?
    others = {name: number for name, number in
              {"telugu": strength["telugu"], "hindi": strength["hindi"],
               "english": strength["english"]}.items()
              if name != language and number > 0}
    second = max(others, key=others.get) if others else None
    mixed = second is not None

    if decided >= 3:
        confidence = "high"
    elif decided == 2:
        confidence = "medium"
    else:
        confidence = "low"

    style = STYLE_NAMES.get((language, script), "English")
    if mixed:
        pretty = {"telugu": "Telugu", "hindi": "Hindi", "english": "English"}
        style = f"{pretty[language]}-{pretty[second]} mix"

    return {
        "language": language,
        "script": script,
        "mixed": mixed,
        "with": second,
        "style": style,
        "confidence": confidence,
        "from_earlier": from_earlier,
        "votes": {name: found for name, found in votes.items() if found},
    }


def from_recent(earlier, how_many=4):
    """The style of the person's last few messages, for when one is too short.

    Only their own messages count, and only ones with enough in them to judge.
    """
    if not earlier:
        return None
    theirs = [m["content"] for m in earlier if m.get("sender") == "user"]
    for text in reversed(theirs[-how_many:]):
        found = profile(text)             # no `earlier`, so this cannot loop
        if found["confidence"] != "low":
            return found
    return None


# --------------------------------------------- what to tell the model (7.5C)
#
# The instruction is built from the profile, not from one yes/no test, so it
# can say "mostly English, keep their Telugu words" instead of flattening a
# mixed sentence into one language.
#
# Both Telugu styles ask for Telugu SCRIPT. gemma3:4b cannot spell Telugu in
# a-z letters - it invents words like "manchju" and "sunchestaanu" - so the
# model writes the script it knows and to_english_letters() changes the
# letters afterwards. Hindi needs no such help: this model writes natural
# Hinglish by itself.

KEEP_THEIR_WORDS = ("Keep the English words they used as English. "
                    "Do not translate their words into another language.")


def instruction(found):
    """The note that tells the model how to write this particular reply."""
    language, script, mixed = found["language"], found["script"], found["mixed"]

    if language == "telugu":
        if mixed:
            return ("[Reply in Telugu, written in Telugu script, in the same easy mixed "
                    "style they used. " + KEEP_THEIR_WORDS + "]")
        return "[Reply in Telugu, written in Telugu script. Do not reply in English.]"

    if language == "hindi":
        if script == "devanagari":
            return "[Reply in Hindi, written in Devanagari script. Do not reply in English.]"
        if mixed:
            return ("[Reply in Hindi written in a-z letters, in the same easy mixed style "
                    "they used, like \"Arre, kal exam hai? Thoda tension normal hai.\" "
                    + KEEP_THEIR_WORDS + " Never use Devanagari script.]")
        return ("[Reply in Hindi written in a-z letters, like \"Arre, kal exam hai? "
                "Thoda tension hona normal hai.\" Never use Devanagari script, "
                "and do not reply in plain English.]")

    # English-led. When they mixed a little Telugu or Hindi in, keep that
    # flavour rather than answering in careful, pure English.
    if mixed and found["with"] == "telugu":
        # Three wordings were tried here. "Write the Telugu words in Telugu
        # script" gave plain English every time. Whole example SENTENCES made
        # it copy them word for word into every reply. Showing small word
        # FRAGMENTS is what worked: there is no sentence to copy, only a style
        # to follow.
        return ("[They write English and Telugu mixed together, in a-z letters. Reply the "
                "same way: ordinary English sentences with their Telugu words left in, the "
                "way they wrote them (\"college lo\", \"presentation undi\", \"chala tension\", "
                "\"sare\"). Write your own sentences. Never use Telugu script, and do not "
                "reply in only English.]")
    if mixed and found["with"] == "hindi":
        return ("[Reply mostly in English, the same easy mixed way they wrote, keeping "
                "the Hindi words in a-z letters. Do not turn the whole reply into Hindi.]")
    return "[Reply in English.]"


# ------------------------------------------ checking the reply came back right (7.5F)
#
# gemma3:4b sometimes ignores the note and answers in the conversation's
# language instead of the person's. Measured: after two English turns, a
# Telugu message got an English reply 3 times out of 3. So the reply is
# checked, and a wrong one is asked for again.


def wanted_reply(found):
    """What the MODEL should produce for this profile: (language, script).

    Both Telugu styles want Telugu SCRIPT from the model - to_english_letters
    changes the letters afterwards for someone who types in a-z.
    """
    if found["language"] == "telugu":
        return "telugu", "telugu"
    if found["language"] == "hindi":
        return "hindi", ("devanagari" if found["script"] == "devanagari" else "latin")
    return "english", "latin"


def reply_matches(found, text):
    """Did the reply roughly come back in the language we asked for?

    Roughly, on purpose. A mixed reply keeps its English words, and that is
    wanted, not a fault - this only catches a reply in the WRONG language.
    """
    if not text.strip():
        return True                        # nothing to judge; other code handles empty
    language, script = wanted_reply(found)

    if script == "telugu":
        return has_telugu(text)
    if script == "devanagari":
        return has_devanagari(text)

    # a-z letters wanted
    if has_telugu(text) or has_devanagari(text):
        return False
    if language == "hindi":
        # Hinglish must actually contain Hindi words, not be plain English
        return written_in_english_letters(text, HINDI_IN_ENGLISH)
    return True
