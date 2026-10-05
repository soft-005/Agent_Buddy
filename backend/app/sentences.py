"""Split streamed reply text into sentences, so each can be spoken as soon as it ends."""

import re

# A sentence ends at . ! ? or … (plus any closing quote or bracket) followed by
# whitespace, or at a line break. Requiring whitespace keeps "3.5" in one piece,
# and the final sentence waits for flush() or the next chunk.
_BOUNDARY = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)|\n+")


class SentenceSplitter:
    def __init__(self, min_chars: int = 20) -> None:
        # After the first sentence, pieces shorter than this ("Oh.", "Sure!")
        # are merged with the next sentence so speech doesn't sound choppy.
        # The first sentence always goes out at once: it decides how soon
        # Buddy starts talking.
        self.min_chars = min_chars
        self.emitted = False
        self.buf = ""

    def feed(self, text: str) -> list[str]:
        self.buf += text
        sentences = []
        start = 0
        for match in _BOUNDARY.finditer(self.buf):
            candidate = self.buf[start:match.end()].strip()
            if candidate and (not self.emitted or len(candidate) >= self.min_chars):
                sentences.append(candidate)
                self.emitted = True
                start = match.end()
        self.buf = self.buf[start:]
        return sentences

    def flush(self) -> list[str]:
        rest = self.buf.strip()
        self.buf = ""
        return [rest] if rest else []
