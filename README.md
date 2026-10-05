# Buddy

A voice companion that lives on your screen. A 3D avatar sits in a small always-on-top window. You talk to it (hold a key, go hands-free, or type), and it answers out loud with lip movement, a mood on its face, and eye contact. Everything runs on your PC: speech recognition with Whisper, and the language model in LM Studio (or the Claude API if you prefer). An Android app talks to the same Buddy over Wi-Fi.

What it can do:
- **Talk:** hold Right Ctrl or the mic button, turn on hands-free ("Hey Buddy, …"), or type.
- **Remember:** keeps the conversation across restarts, and facts you tell it ("remember that…").
- **Act on your PC, with your OK:** open apps, files and web pages, read and write files in `Documents\Buddy`, run commands. Anything that changes something asks first.
- **Switch avatars, models and voices** from the tray menu, or by asking.

## Setup (once)

You need Windows 10 or 11, [Python 3.11+](https://www.python.org/), [Node.js 20+](https://nodejs.org/), [LM Studio](https://lmstudio.ai/), and git (for one Python package).

1. **Model:** in LM Studio, download `qwen/qwen3.5-9b` (or another model listed in `backend/providers.json`). Set its context length to at least 8192.
2. **Backend:** from the project folder:
   ```
   python -m venv venv
   venv\Scripts\pip install -r backend\requirements.txt
   copy backend\.env.example backend\.env
   ```
   The defaults work. Open `backend\.env` to change the model, port, phone access and so on (see [Settings](#settings)).
3. **Desktop app:** `cd desktop` then `npm install`.
4. **Avatar (optional):** put a `.vrm` file in `desktop\assets\models` (free from VRoid Studio). Without one, Buddy is a blue sphere.
5. **Shortcuts:** `powershell -ExecutionPolicy Bypass -File install-shortcuts.ps1` adds **Buddy**, **Buddy - Show or Hide** and **Buddy - Quit** to your Desktop.

## Everyday use

Double-click **Buddy**. It starts LM Studio's server if needed, the backend and the window. A loader shows progress; the first start takes a minute or two while the model loads.

- **Talk:** hold **Right Ctrl** anywhere (or the mic button) while you speak, and let go when done. **Ctrl+Shift+Space** toggles listening.
- **Type:** in the box under Buddy.
- **Stop Buddy talking:** start talking or typing.
- **Move or resize:** drag ☰. Ctrl + mouse wheel over the controls changes the size.
- **Tray menu** (right-click the icon near the clock): new conversation, model, connection, avatar, eye contact, size, hiding when idle, hold-to-talk key, keep the mic ready, hands-free, voice, speed, pitch, volume, Buddy's folder, permissions, quit.

Hands-free (tray, then **Hands-free talking**): **Always listening** answers whenever you stop talking. **Only after "Hey Buddy"** answers sentences that start with "Hey Buddy" or end with ", Buddy?", and for 8 seconds after each reply. While Buddy asks "May I …?", just say yes or no.

From a terminal (`powershell -ExecutionPolicy Bypass -File buddy.ps1 <command>`):

| Command | Does |
|---|---|
| `start` / `stop` / `restart` | start, quit, or restart everything (restart after editing `backend\.env` or code) |
| `status` | what's running, and on which port |
| `model` | list the language models; `model gemma` switches (no restart) |
| `stop-all` | also unload LM Studio's models to free memory |

## Switching models

The models are listed in `backend\providers.json`. Switch any of these ways; the choice is saved in `backend\.env`:
- **Ask Buddy:** "switch to Gemma", "use the Claude model", "which model are you?", or type `/model` and `/model qwen`.
- **Command line:** `buddy.ps1 model gemma` (or `python scripts\model.py gemma` from `backend`).
- **Tray:** **Model**.

A local model takes a minute to load the first time. Claude needs `ANTHROPIC_API_KEY` in `backend\.env`. To add a model, add an entry to `providers.json` (no code changes).

## Phone (Android)

1. In `backend\.env` set `LAN_ACCESS=true`, then restart Buddy. Allow Python through Windows Firewall on private networks when asked.
2. Install `mobile\build\app\outputs\flutter-apk\app-arm64-v8a-release.apk` (build it with `cd mobile`, `flutter build apk --release --split-per-abi`).
3. Open Buddy on the phone, tap **Find on Wi-Fi** (or type the PC's address), then **Connect**.

Hold the mic button (or Volume Down) to talk. Settings has the avatar, hands-free, voice and the floating bubble. The chat picks up the conversation from the PC.

## Settings

`backend\.env` (restart Buddy after editing):

| Setting | Default | What it does |
|---|---|---|
| `LLM_PROVIDER`, `LLM_MODEL` | `lmstudio`, `qwen/qwen3.5-9b` | the model (an entry in `providers.json`) |
| `PORT` | `8818` | Buddy's port. The desktop app and launcher follow it; the tray's **Connection…** can change it too |
| `LAN_ACCESS` | `false` | `true` lets a phone on your Wi-Fi connect |
| `WHISPER_MODEL` | `small` | speech recognition size: `base` is faster, `medium` more accurate |
| `WAKE_WORDS` | `buddy` | the hands-free wake word(s), comma-separated |
| `VAD_SILENCE_MS` | `800` | how long a pause ends what you're saying (hands-free) |
| `SAVE_CONVERSATION` | `true` | keep the conversation across restarts |
| `MOOD_TAGS` | `true` | the avatar shows a mood per sentence |
| `MEMORY_ENABLED` | `true` | remember facts about you in `backend\data\memory.md` |
| `ANTHROPIC_API_KEY` | | only for Claude |

What Buddy may do on the PC is in `backend\tools.json` (`auto`, `ask` or `off` per action, and the folders it may use). Desktop settings are in the tray and saved in `%APPDATA%\buddy-desktop\settings.json`.

## Your data

All on this PC, in `backend\data` (not in git): `memory.md` (facts, editable), `conversation.json` (the current conversation) and `conversations\` (earlier ones, moved there by **New conversation**). The backend log is `logs\backend.log`, and it includes what Buddy heard, which helps when voice isn't working.

## Troubleshooting

- **Buddy doesn't hear me:** check Windows Settings → Privacy → Microphone (allow desktop apps). Keep **Keep the microphone ready** on in the tray (it stops the first word being cut off). `logs\backend.log` shows each recording, as "Heard 1.8 s of audio: …".
- **Slow replies:** try Gemma ("switch to Gemma"), set `WHISPER_MODEL=base`, and close heavy apps. LM Studio unloads an idle model after a while, so the next reply waits for it to load.
- **"LM Studio isn't running":** open LM Studio and start the server (Developer tab, port 1234).
- **Phone can't connect:** same Wi-Fi, `LAN_ACCESS=true`, firewall allowed for Python, and the address with `:port` if `PORT` isn't 8818. "Find on Wi-Fi" searches the port typed in the field.
- **Port already in use:** change it in the tray (**Connection…**, type a port number) or set `PORT` in `backend\.env`.

## Project layout

`backend` (Python: speech, model, tools, memory), `desktop` (Electron window and tray), `mobile` (Flutter Android app), `buddy.ps1` (launcher). Details for developers, including the WebSocket protocol, are in `CLAUDE.md`.
