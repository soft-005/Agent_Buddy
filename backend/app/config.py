"""Settings loaded from environment variables (and backend/.env if present)."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"  # gitignored: memory and saved conversations


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def update_env_file(values: dict[str, str], path: Path = ENV_FILE) -> None:
    """Set NAME=value lines in backend/.env and keep everything else, comments included.
    A setting that only appears commented out is added at the end."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in pending:
            lines[i] = f"{match.group(1)}={pending.pop(match.group(1))}"
    lines += [f"{name}={value}" for name, value in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Which entry in providers.json to use. An empty LLM_MODEL picks the provider's first model.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "lmstudio")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.5-9b")

# Only used by the "anthropic" provider.
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")

SAMPLE_RATE = 16000  # clients must send 16 kHz, mono, 16-bit PCM

# Phone access is optional and off by default.
# Off: only this PC can connect (127.0.0.1), which is all the desktop app needs.
# On: listen on every network interface so a phone on the same Wi-Fi can connect too.
LAN_ACCESS = _flag("LAN_ACCESS", "false")
# Advanced override for the listen address. Leave unset normally. A specific LAN
# address shuts out 127.0.0.1, so the desktop app would need BUDDY_SERVER_URL.
HOST = os.getenv("HOST", "").strip() or ("0.0.0.0" if LAN_ACCESS else "127.0.0.1")
# 8818 rather than the common 8000, so Buddy doesn't clash with other dev servers.
# The desktop app and the launcher read this same setting.
PORT = int(os.getenv("PORT", "8818"))

SYSTEM_PROMPT = (
    "You are Buddy, a friendly desktop companion who lives as a small 3D avatar "
    "on the user's screen and talks with them by voice. Your replies are spoken "
    "aloud, so keep them short and conversational: usually one to three sentences, "
    "no markdown, no lists, and no code blocks unless the user asks for them. "
    "You have a memory file. Facts in it are listed below when there are any. "
    "When the user asks you to remember or forget something, it has already "
    "been saved or removed, so just confirm briefly."
)

# Folder with the avatar .vrm served to the phone app at /avatar.vrm (the desktop's folder by default).
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", "") or Path(__file__).resolve().parents[2] / "desktop" / "assets" / "models")

# Long-term memory (Markdown file managed by mnem, see app/memory.py).
MEMORY_ENABLED = _flag("MEMORY_ENABLED", "true")
MEMORY_FILE = Path(os.getenv("MEMORY_FILE", "") or DATA_DIR / "memory.md")
# Tokens of memory put in the prompt. Keep it small for 4096-token local models.
MEMORY_BUDGET = int(os.getenv("MEMORY_BUDGET", "200"))

# Mood tags: the model starts each reply with one, like [happy], and the avatar's face follows it.
MOOD_TAGS = _flag("MOOD_TAGS", "true")

# The conversation is saved here after every turn, so Buddy picks up where you left off.
# "New conversation" (desktop tray, phone app) moves it to data/conversations/.
SAVE_CONVERSATION = _flag("SAVE_CONVERSATION", "true")
CONVERSATION_FILE = Path(os.getenv("CONVERSATION_FILE", "") or DATA_DIR / "conversation.json")

# Hands-free talking (turned on in the desktop tray or the phone's settings).
# Wake word mode answers only when a sentence starts or ends with one of these.
WAKE_WORDS = [w.strip().lower() for w in os.getenv("WAKE_WORDS", "buddy").split(",") if w.strip()]
# How long a pause ends what you're saying, in milliseconds.
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "800"))
# After Buddy answers, how long you can reply without the wake word, in seconds.
FOLLOWUP_SECONDS = float(os.getenv("FOLLOWUP_SECONDS", "8"))
