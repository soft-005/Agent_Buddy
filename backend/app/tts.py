"""Text-to-speech.

Not implemented yet: synthesize() returns None, and clients fall back to
their own local speech (the desktop app uses the browser speechSynthesis API).
Plug a real engine in here later (Piper for local, or a hosted TTS API) and
return WAV bytes. It is called once per sentence while the reply streams,
so it should be fast for short text.
"""


async def synthesize(text: str) -> bytes | None:
    return None
