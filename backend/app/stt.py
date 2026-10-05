"""Speech-to-text with faster-whisper (CPU, int8).

The model loads in a background thread when the backend starts, so the server
accepts connections (and typed messages) right away. The first voice message
waits for the load to finish if it hasn't yet.
"""

import io
import logging
import threading
import time

from faster_whisper import WhisperModel

from .audio import pcm_to_wav
from .config import WHISPER_LANGUAGE, WHISPER_MODEL

log = logging.getLogger("buddy.stt")

_model: WhisperModel | None = None
_load_error: Exception | None = None
_ready = threading.Event()


def _load() -> None:
    global _model, _load_error
    started = time.perf_counter()
    try:
        # Use the cached copy without contacting Hugging Face (faster, works offline).
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        try:
            log.info("Downloading Whisper model '%s' (first run only)...", WHISPER_MODEL)
            _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        except Exception as e:  # reported on the first voice message
            _load_error = e
            log.error("Could not load Whisper model '%s': %s", WHISPER_MODEL, e)
    if _model is not None:
        log.info("Whisper model '%s' ready in %.1f s", WHISPER_MODEL, time.perf_counter() - started)
    _ready.set()


threading.Thread(target=_load, name="whisper-load", daemon=True).start()


def status() -> str:
    """"loading", "ready" or "error" (shown by the desktop app's startup loader)."""
    if _model is not None:
        return "ready"
    return "error" if _ready.is_set() else "loading"


def transcribe_pcm(pcm_bytes: bytes) -> str:
    """Blocking and CPU-bound. Call via asyncio.to_thread from async code."""
    _ready.wait()
    if _model is None:
        raise RuntimeError(f"speech recognition unavailable: {_load_error}")
    wav = io.BytesIO(pcm_to_wav(pcm_bytes))
    segments, _info = _model.transcribe(wav, language=WHISPER_LANGUAGE, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()
