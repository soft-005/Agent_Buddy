"""Mood tags: the model starts each reply with a tag such as [happy], so the
avatar's face can match what it says.

The tags are removed before text is shown or spoken. Each sentence sent to the
clients carries the mood in effect when it was complete, so a reply that
changes mood halfway ("[happy] Great! [sad] But I can't come.") changes the
face sentence by sentence. History keeps the tags: small models copy the format
of their own earlier replies, so seeing the tags there keeps them using them.
"""

import re

# The VRM expression presets, plus neutral (no expression).
MOODS = ("neutral", "happy", "sad", "angry", "surprised", "relaxed")

# Words small models sometimes use instead of the six names.
_ALIASES = {
    "joy": "happy", "joyful": "happy", "glad": "happy", "excited": "happy", "cheerful": "happy",
    "amused": "happy", "playful": "happy", "laughing": "happy", "smiling": "happy", "proud": "happy",
    "sorry": "sad", "sorrow": "sad", "unhappy": "sad", "disappointed": "sad", "worried": "sad",
    "concerned": "sad", "apologetic": "sad",
    "annoyed": "angry", "frustrated": "angry", "mad": "angry", "irritated": "angry",
    "shocked": "surprised", "amazed": "surprised", "astonished": "surprised", "curious": "surprised",
    "calm": "relaxed", "content": "relaxed", "gentle": "relaxed", "peaceful": "relaxed", "fun": "relaxed",
    "thinking": "neutral", "thoughtful": "neutral", "serious": "neutral",
}

MOOD_PROMPT = (
    "Your avatar's face can show a mood. Begin what you say with one mood tag: [happy], "
    "[sad], [angry], [surprised], [relaxed] or [neutral], like this: [happy] That's great "
    "news! The tag is never spoken. A tag never replaces a tool call: when a request needs "
    "a tool, still call it in the same reply."
)

_TAG = re.compile(r"\[\s*([A-Za-z]+)\s*\]")
_PARTIAL = re.compile(r"\[\s*[A-Za-z]*\s*")  # the start of something that could still be a tag
_MAX_TAG_CHARS = 24
_SPACES = re.compile(r"[ \t]{2,}")


def mood_name(word: str) -> str | None:
    """The mood a tag word stands for, or None if it isn't one."""
    word = word.lower()
    return word if word in MOODS else _ALIASES.get(word)


def strip_moods(text: str) -> str:
    """Remove mood tags from a complete text."""
    text = _TAG.sub(lambda m: "" if mood_name(m.group(1)) else m.group(0), text)
    return _SPACES.sub(" ", text).strip()


class MoodFilter:
    """Streaming counterpart of strip_moods.

    Removes mood tags even when one is split across chunks, and records them:
    `mood` is the mood in effect now, `first` the first one the reply set
    (None until it sets one). Unknown bracketed words are left in the text.
    """

    def __init__(self) -> None:
        self.buf = ""
        self.mood = "neutral"
        self.first: str | None = None

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        out = []
        while self.buf:
            start = self.buf.find("[")
            if start < 0:
                out.append(self.buf)
                self.buf = ""
                break
            out.append(self.buf[:start])
            self.buf = self.buf[start:]
            tag = _TAG.match(self.buf)
            if tag:
                mood = mood_name(tag.group(1))
                if mood:
                    self.mood = mood
                    self.first = self.first or mood
                else:
                    out.append(tag.group(0))
                self.buf = self.buf[tag.end():]
                continue
            partial = _PARTIAL.match(self.buf)
            if not final and partial.end() == len(self.buf) and len(self.buf) <= _MAX_TAG_CHARS:
                break  # wait for the rest of the tag
            out.append("[")  # not a tag
            self.buf = self.buf[1:]
        return "".join(out)
