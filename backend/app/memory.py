"""Long-term memory in a Markdown file, using mnem (github.com/JustVugg/mnem).

The file (backend/data/memory.md by default) is the database: open it to see
what Buddy remembers, edit or delete a line to correct it. Newer statements on
the same topic supersede older ones, and restating something makes it rank
higher, so important and repeated facts float to the top.

What gets saved, with no extra model calls:
  - "remember (that) ..."  saves the rest of the sentence.
  - "forget (that/about) ..." removes the best-matching fact.
  - Clear personal statements ("my name is ...", "I live in ...", "I prefer ...")
    are saved automatically. Questions are never saved.

The current facts go into the system prompt, newest first, within
MEMORY_BUDGET tokens. The block only changes when a fact changes, so the local
model's prompt cache stays valid between turns.
"""

import logging
import re
from pathlib import Path

from .config import MEMORY_BUDGET, MEMORY_ENABLED, MEMORY_FILE

log = logging.getLogger("buddy.memory")

try:
    from mnem import Memory
except ImportError:  # optional dependency; Buddy works without memory
    Memory = None

_PREFIX = r"^\s*(?:(?:hey|hi|ok|okay)\s+buddy[,!.]?\s*)?(?:please\s+)?(?:can you\s+|could you\s+)?"
_REMEMBER = re.compile(_PREFIX + r"remember(?:\s+that)?\s*[:,]?\s+(?P<fact>.+)$", re.I | re.S)
_FORGET = re.compile(_PREFIX + r"forget(?:\s+that|\s+about)?\s*[:,]?\s+(?P<needle>.+)$", re.I | re.S)

# First-person statements worth keeping. "I like that/this/it" is excluded:
# it refers to the conversation, not to the user.
_PERSONAL = re.compile(
    r"\b(?:my name is|call me|i live in|i'?m from|i am from|i work (?:as|at|in|for)|"
    r"my (?:favou?rite|birthday|wife|husband|partner|girlfriend|boyfriend|son|daughter|kids?|dog|cat|job|age)|"
    r"i(?:'m| am) allergic|i(?: really)? (?:like|love|prefer|hate|enjoy)(?! (?:that|this|it|those|these)\b)|"
    r"i don'?t (?:like|eat|drink))\b",
    re.I,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
_WORD = re.compile(r"[a-z0-9']+")
_STOP = {"a", "an", "the", "my", "i", "me", "about", "that", "this", "it", "is", "to", "of", "and", "what", "please"}
MAX_FACT_CHARS = 200


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(" .,!;:")


def personal_facts(text: str) -> list[str]:
    """Sentences in text that state something lasting about the user."""
    facts = []
    for sentence in _SENTENCE.findall(text):
        sentence = sentence.strip()
        if sentence.endswith("?") or len(sentence) > MAX_FACT_CHARS:
            continue
        if _PERSONAL.search(sentence):
            facts.append(_clean(sentence))
    return facts


class BuddyMemory:
    def __init__(self, path: Path = MEMORY_FILE, budget: int = MEMORY_BUDGET, enabled: bool = MEMORY_ENABLED) -> None:
        self.budget = budget
        self.memory = None
        if not enabled:
            log.info("Memory is turned off (MEMORY_ENABLED=false)")
            return
        if Memory is None:
            log.warning("Memory is off: the mnem package is not installed. Run: pip install -r requirements.txt")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = Memory.open(str(path))
        log.info("Memory file: %s (%d facts)", path, len(self.memory))

    @property
    def enabled(self) -> bool:
        return self.memory is not None

    def observe(self, user_text: str) -> None:
        """Update memory from what the user said, before the model replies."""
        if not self.enabled:
            return
        if match := _FORGET.match(user_text):
            removed = self.forget(_clean(match["needle"]))
            log.info("forget %r: removed %d fact(s)", match["needle"], removed)
        elif match := _REMEMBER.match(user_text):
            fact = _clean(match["fact"])
            if fact:
                self.memory.add(fact, source="remember")
                log.info("remembered: %s", fact)
        else:
            for fact in personal_facts(user_text):
                self.memory.add(fact, source="auto")
                log.info("noted: %s", fact)

    def forget(self, needle: str) -> int:
        """Remove facts containing needle, or else the one fact sharing the most words with it."""
        removed = self.memory.forget(needle)
        if removed:
            return removed
        wanted = set(_WORD.findall(needle.lower())) - _STOP
        if not wanted:
            return 0
        best, best_overlap = None, 0
        for fact in self.memory.facts():
            if not fact.current:
                continue
            overlap = len(wanted & set(_WORD.findall(fact.text.lower())))
            if overlap > best_overlap:
                best, best_overlap = fact, overlap
        return self.memory.forget(best.text) if best else 0

    def prompt_block(self) -> str:
        if not self.enabled:
            return ""
        return self.memory.prompt(budget=self.budget, header="What you remember about the user:")
