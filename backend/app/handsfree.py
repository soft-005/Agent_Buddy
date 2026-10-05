"""Hands-free talking: voice detection and the wake word.

A client in hands-free mode streams its microphone continuously (pausing while
Buddy speaks). VoiceDetector finds where each utterance starts and ends with
the Silero VAD model that faster-whisper already ships, so no extra download.
One 32 ms window takes well under a millisecond, so it runs on the event loop.

In wake word mode every utterance is transcribed, and Buddy answers only if it
starts or ends with the wake word ("Hey Buddy, what's the time?", "What's the
time, Buddy?"). Whisper does the recognition, so any word works as a wake word.

While Buddy waits for permission ("May I open Notepad?"), a spoken yes or no
answers it instead of starting a new turn.
"""

import logging
import re
from collections import deque

import numpy as np

from .config import SAMPLE_RATE, VAD_SILENCE_MS

log = logging.getLogger("buddy.handsfree")

WINDOW = 512  # samples per Silero window (32 ms at 16 kHz)
CONTEXT = 64  # samples of the previous window the model sees with each window
WINDOW_BYTES = WINDOW * 2
WINDOW_MS = WINDOW * 1000 / SAMPLE_RATE

_session = None


def _vad_session():
    global _session
    if _session is None:
        from faster_whisper.vad import get_vad_model

        _session = get_vad_model().session
        log.info("Voice detection ready")
    return _session


def _windows(ms: float) -> int:
    return max(1, round(ms / WINDOW_MS))


class VoiceDetector:
    """Turns a continuous 16 kHz mono int16 stream into utterances.

    feed() returns events in order: ("start", b"") once speech has lasted
    min_speech_ms, and ("end", pcm) after silence_ms of quiet, with the
    utterance's audio (including a little audio from before it started).
    A short noise that never reaches min_speech_ms produces no events.
    """

    def __init__(
        self,
        silence_ms: int = VAD_SILENCE_MS,
        threshold: float = 0.5,
        min_speech_ms: int = 200,  # "Yes." and "No." are short
        max_speech_s: float = 30,
        preroll_ms: int = 300,
        tail_ms: int = 250,
    ) -> None:
        self.session = _vad_session()
        self.threshold = threshold
        self.end_silence = _windows(silence_ms)
        self.min_speech = _windows(min_speech_ms)
        self.tail = _windows(tail_ms)
        self.max_bytes = int(max_speech_s * SAMPLE_RATE) * 2
        self.preroll: deque[bytes] = deque(maxlen=_windows(preroll_ms))
        self.reset()

    def reset(self) -> None:
        """Forget everything heard so far (a new listening mode, or the client paused)."""
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)
        self.pending = b""
        self.preroll.clear()
        self._new_utterance()

    def _new_utterance(self) -> None:
        self.collecting = False  # something above the threshold was heard
        self.active = False  # ...and it lasted long enough to count as speech
        self.audio = bytearray()
        self.speech = 0  # windows of speech in this utterance
        self.silence = 0  # windows of quiet since the last speech

    def _probability(self, window: bytes) -> float:
        samples = np.frombuffer(window, np.int16).astype(np.float32) / 32768
        out, self.h, self.c = self.session.run(
            None, {"input": np.concatenate([self.context, samples])[None, :], "h": self.h, "c": self.c}
        )
        self.context = samples[-CONTEXT:]
        return float(out.reshape(-1)[0])

    def feed(self, pcm: bytes) -> list[tuple[str, bytes]]:
        data = self.pending + pcm
        usable = len(data) - len(data) % WINDOW_BYTES
        self.pending = data[usable:]
        events = []
        for i in range(0, usable, WINDOW_BYTES):
            window = data[i:i + WINDOW_BYTES]
            events += self._step(window, self._probability(window))
        return events

    def _step(self, window: bytes, probability: float) -> list[tuple[str, bytes]]:
        if not self.collecting:
            if probability < self.threshold:
                self.preroll.append(window)
                return []
            self.collecting = True
            self.audio = bytearray(b"".join(self.preroll))
            self.preroll.clear()

        self.audio += window
        # Started by a window above the threshold; after that a slightly lower one
        # still counts as speech (the same hysteresis Silero's own splitter uses).
        if probability >= self.threshold - 0.15:
            self.speech += 1
            self.silence = 0
        else:
            self.silence += 1

        events = []
        if not self.active and self.speech >= self.min_speech:
            self.active = True
            events.append(("start", b""))
        if self.silence >= self.end_silence:
            if self.active:
                # Keep a little of the quiet after the last word, drop the rest.
                cut = len(self.audio) - (self.silence - self.tail) * WINDOW_BYTES
                events.append(("end", bytes(self.audio[:cut])))
            self._new_utterance()
        elif self.active and len(self.audio) >= self.max_bytes:
            events.append(("end", bytes(self.audio)))
            self._new_utterance()
        return events


_YES = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|allow(?: it)?|please do|of course|alright|all right)\b", re.I
)
_NO = re.compile(r"^\W*(?:no|nope|nah|don'?t|do not|deny|stop|cancel|never ?mind)\b", re.I)


def yes_or_no(text: str) -> bool | None:
    """A spoken answer to "May I ...?": True for yes, False for no, None if it's neither."""
    if _NO.match(text):
        return False
    if _YES.match(text):
        return True
    return None


# Words that may come before the wake word ("Hey Buddy", "OK Buddy", "Um, Buddy").
_LEAD_IN = {"hey", "hi", "hello", "ok", "okay", "yo", "oh", "um", "uh", "so", "and"}
# Ways Whisper sometimes writes "Buddy".
_SOUNDALIKES = {"buddy": {"buddie", "budy", "bodhi"}}
_WORD = re.compile(r"[A-Za-z']+")


def strip_wake_word(text: str, wake_words: list[str]) -> str | None:
    """The request in text without the wake word, "" if text is only the wake
    word, or None if text doesn't address Buddy.

    The wake word counts at the start (after up to two words like "hey") or as
    the last word after a comma: "Hey Buddy, what's the time?" and "What's the
    time, Buddy?" both give "what's the time?". The comma keeps "He's my buddy."
    from counting: Whisper writes one when the name is used to address someone.
    """
    names = set(wake_words)
    for word in wake_words:
        names |= _SOUNDALIKES.get(word, set())
    words = list(_WORD.finditer(text))
    lowered = [w.group().lower() for w in words]

    for i, word in enumerate(lowered[:3]):
        if word in names:
            return text[words[i].end():].lstrip(" ,.!?;:-").strip()
        if word not in _LEAD_IN:
            break
    if len(words) > 1 and lowered[-1] in names:
        before = text[: words[-1].start()].rstrip()
        if before.endswith(","):
            return (before.rstrip(" ,") + text[words[-1].end():]).strip()
    return None
