"""Buddy backend: WebSocket server for STT -> LLM -> (TTS).

Run from the backend/ folder:
    python -m app.main
"""

import asyncio
import json
import logging
import socket
import time
import uuid

# Configure logging before the app modules load: they log while starting up
# (memory file, Whisper model), and those lines should reach logs/backend.log.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

from contextlib import aclosing, asynccontextmanager  # noqa: E402

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

from . import agent, config, model_switch  # noqa: E402
from .agent import SESSION, TOOLS, warm_up  # noqa: E402
from .handsfree import VoiceDetector, strip_wake_word, yes_or_no  # noqa: E402
from .llm import LLMError  # noqa: E402
from .providers import ModelConfig, list_models, load_model  # noqa: E402
from .status import llm_status, snapshot  # noqa: E402
from .stt import transcribe_pcm  # noqa: E402
from .tools import Approver  # noqa: E402
from .tts import synthesize  # noqa: E402

log = logging.getLogger("buddy")
_background: set[asyncio.Task] = set()  # warm-ups started after a model switch


async def warm_up_model() -> None:
    """Get a local model into memory (LM Studio loads it on request): at startup,
    and after switching models. Gives up if the model is switched again meanwhile."""
    MODEL = agent.MODEL
    if MODEL.api != "openai-completions":
        return
    missing_checks = 0
    for _ in range(90):  # LM Studio may still be starting: wait up to about 3 minutes
        if agent.MODEL is not MODEL:
            return
        state = await asyncio.to_thread(llm_status)
        if state == "ready":
            # Already loaded: still send the prompt once, so the server caches it.
            started = time.perf_counter()
            try:
                await warm_up()
                log.info("Prompt cached in %s in %.0f s", MODEL.provider, time.perf_counter() - started)
            except LLMError as e:
                log.warning("Could not prepare %s: %s", MODEL.id, e)
            return
        if state == "missing":
            # Right after it starts, LM Studio can list only some models; give it a minute.
            missing_checks += 1
            if missing_checks >= 30:
                log.warning("LM Studio doesn't have the model '%s' (LLM_MODEL in .env)", MODEL.id)
                return
            await asyncio.sleep(2)
            continue
        if state == "not_loaded":
            log.info("Loading %s in %s (first load takes a minute or more)...", MODEL.id, MODEL.provider)
            started = time.perf_counter()
            try:
                await warm_up()
                log.info("%s loaded in %.0f s", MODEL.id, time.perf_counter() - started)
            except LLMError as e:
                log.warning("Could not load %s ahead of time: %s", MODEL.id, e)
            return
        await asyncio.sleep(2)  # offline: wait for the server
    log.warning("%s server not reachable at %s; Buddy can't reply until it is", MODEL.provider, MODEL.base_url)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(warm_up_model())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    status = snapshot(await asyncio.to_thread(llm_status))
    return {"ok": True, "model": agent.MODEL.id, "whisper": config.WHISPER_MODEL, **status}


# ---------- switching models ----------

def _model_info(model: ModelConfig) -> dict:
    return {"provider": model.provider, "model": model.id, "name": model.name}


async def select_model(name: str = "", provider: str = "", model_id: str = "") -> ModelConfig:
    """Switch to a model by name ("gemma") or by provider and id, and start loading
    it. Raises ValueError with a message for the user if it can't."""
    if provider:
        model = load_model(provider, model_id)
    else:
        model, candidates = model_switch.find(name)
        if model is None:
            raise ValueError(model_switch.not_found(name, candidates))
    if (model.provider, model.id) != (agent.MODEL.provider, agent.MODEL.id):
        await agent.switch_model(model)
        task = asyncio.create_task(warm_up_model())
        _background.add(task)
        task.add_done_callback(_background.discard)
    return model


@app.get("/models")
async def models():
    """The models in providers.json, and the one in use (for scripts/model.py)."""
    return {
        "current": _model_info(agent.MODEL),
        "models": [{**_model_info(m), "usable": model_switch.unusable_reason(m) is None} for m in list_models()],
    }


@app.post("/model")
async def set_model(body: dict):
    """Switch models: {"name": "gemma"} or {"provider": "lmstudio", "model": "google/gemma-3n-e4b"}."""
    try:
        model = await select_model(str(body.get("name") or ""), str(body.get("provider") or ""), str(body.get("model") or ""))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return {"ok": True, **_model_info(model)}


async def answer_model_command(ws: WebSocket, command: tuple[str, str]) -> None:
    """A chat command about the model ("switch to Gemma", "/model"), answered without calling the model."""
    kind, name = command
    await ws.send_json({"type": "thinking"})
    mood = "happy"
    if kind == "show":
        text, mood = model_switch.describe_choices(agent.MODEL), "neutral"
    else:
        before = agent.MODEL
        try:
            model = await select_model(name)
            if model is before or (model.provider, model.id) == (before.provider, before.id):
                text = f"I'm already using {model.name}."
            else:
                load_note = " It may take a minute to load the first time." if model.api == "openai-completions" else ""
                text = f"Okay, I'm now using {model.name}.{load_note}"
                await ws.send_json({"type": "model", **_model_info(model)})  # the desktop tray follows
        except ValueError as e:
            text, mood = str(e), "sad"
    await speak(ws, text, mood)
    await ws.send_json({"type": "reply", "text": text, "mood": mood})


CORS = {"Access-Control-Allow-Origin": "*"}  # the phone's web view loads avatars cross-origin


def list_avatars() -> list[str]:
    """The .vrm files in AVATAR_DIR, sorted as the desktop app sorts them."""
    try:
        return sorted(p.name for p in config.AVATAR_DIR.iterdir() if p.suffix.lower() == ".vrm" and p.is_file())
    except OSError:
        return []


def default_avatar(names: list[str]) -> str | None:
    """Same choice as the desktop app: avatar.vrm, else the first .vrm alphabetically."""
    return "avatar.vrm" if "avatar.vrm" in names else (names[0] if names else None)


def avatar_response(name: str | None):
    if name is None:
        return JSONResponse({"error": f"no such .vrm file in {config.AVATAR_DIR}"}, status_code=404, headers=CORS)
    headers = {**CORS, "Cache-Control": "max-age=300"}
    return FileResponse(config.AVATAR_DIR / name, media_type="model/gltf-binary", headers=headers)


@app.get("/avatar.vrm")
async def avatar():
    """The default avatar model, for the phone app."""
    return avatar_response(default_avatar(list_avatars()))


@app.get("/avatars")
async def avatars():
    """Avatars the phone app can switch between: {"avatars": [file names], "default": name}."""
    names = list_avatars()
    return JSONResponse({"avatars": names, "default": default_avatar(names)}, headers=CORS)


@app.get("/avatars/{name}")
async def avatar_by_name(name: str):
    # Only names from the folder listing, so a request can't reach other files.
    return avatar_response(name if name in list_avatars() else None)


async def speak(ws: WebSocket, text: str, mood: str) -> None:
    """Send one sentence of a reply, with server speech if TTS is set up."""
    audio = await synthesize(text)
    # "audio": true tells the client a WAV frame follows, so it shouldn't speak locally.
    await ws.send_json({"type": "reply_chunk", "text": text, "audio": bool(audio), "mood": mood})
    if audio:
        await ws.send_bytes(audio)


async def handle_user_text(ws: WebSocket, text: str, approver: Approver) -> None:
    """Stream the reply sentence by sentence so the client can start speaking early."""
    command = model_switch.parse(text)
    if command:
        await answer_model_command(ws, command)
        return
    await ws.send_json({"type": "thinking"})
    try:
        # aclosing: if this task is cancelled mid-reply, the turn ends (and saves) right away.
        async with aclosing(SESSION.reply_stream(text, approver)) as stream:
            async for item in stream:
                if isinstance(item, dict):  # a tool ran, or the final {"type": "reply"}
                    await ws.send_json(item)
                else:
                    await speak(ws, item.text, item.mood)
    except LLMError as e:
        log.warning("LLM call failed: %s", e)
        await ws.send_json({"type": "error", "message": str(e)})


async def transcribe(pcm: bytes) -> str:
    """Speech to text in a thread. Logs what was heard, which helps when voice seems not to work."""
    text = await asyncio.to_thread(transcribe_pcm, pcm)
    log.info("Heard %.1f s of audio: %s", len(pcm) / 2 / config.SAMPLE_RATE, text or "(no words)")
    return text


async def handle_voice(ws: WebSocket, pcm: bytes, approver: Approver) -> None:
    try:
        text = await transcribe(pcm)
    except RuntimeError as e:
        log.warning("%s", e)
        await ws.send_json({"type": "error", "message": str(e)})
        return
    await ws.send_json({"type": "transcript", "text": text})
    if text:
        await handle_user_text(ws, text, approver)


async def acknowledge(ws: WebSocket) -> None:
    """Only the wake word was said ("Hey Buddy"): answer briefly. After speaking, the
    app opens the follow-up window, so the request itself needs no wake word."""
    await ws.send_json({"type": "thinking"})
    await speak(ws, "Yes?", "happy")
    await ws.send_json({"type": "reply", "text": "Yes?", "mood": "happy"})


def _report_turn_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None and not isinstance(error, WebSocketDisconnect):
        log.error("turn failed", exc_info=error)


@app.websocket("/ws")
async def buddy_socket(ws: WebSocket):
    """One connection per app. All of them share Buddy's saved conversation.

    Each turn (transcribe, then stream the reply) runs as a task, so this loop
    keeps reading while Buddy talks. New input or {"type": "cancel"} cancels an
    unfinished turn, which also stops the model generating.

    Binary frames are microphone audio. In "push" listening mode (the default)
    they're buffered until {"type": "end"}. In the hands-free modes ("vad",
    "wake") the client streams continuously and the server finds each utterance.
    """
    await ws.accept()
    audio_chunks: list[bytes] = []
    turn: asyncio.Task | None = None
    approvals: dict[str, asyncio.Future] = {}
    listen_mode = "push"
    detector: VoiceDetector | None = None
    followup_until = 0.0  # wake word mode: until then, Buddy answers without the wake word
    addressed = False  # the utterance in progress is for Buddy (no wake word check needed)
    utterances: set[asyncio.Task] = set()  # hands-free utterances being transcribed and checked

    async def ask_user(request: dict) -> bool:
        """Ask this connection's user to allow a tool; no answer in time counts as no."""
        approval_id = uuid.uuid4().hex[:8]
        answer = asyncio.get_running_loop().create_future()
        approvals[approval_id] = answer
        await ws.send_json({"type": "approval", "id": approval_id, **request, "timeout": TOOLS.approval_timeout})
        try:
            return await asyncio.wait_for(answer, TOOLS.approval_timeout)
        except asyncio.TimeoutError:
            await ws.send_json({"type": "approval_closed", "id": approval_id, "reason": "timeout"})
            return False
        finally:
            approvals.pop(approval_id, None)

    async def cancel_turn() -> None:
        nonlocal turn
        if turn is not None and not turn.done():
            turn.cancel()
            try:
                await turn
            except BaseException:
                pass
        turn = None

    def start_turn(coro) -> None:
        nonlocal turn
        turn = asyncio.create_task(coro)
        turn.add_done_callback(_report_turn_failure)

    def cancel_utterances() -> None:
        for task in utterances:
            task.cancel()

    async def handle_utterance(pcm: bytes, for_buddy: bool) -> None:
        """Hands-free, after transcribing: a yes or no while Buddy waits for permission
        answers it; otherwise the utterance becomes the next turn if it's for Buddy
        (always listening, or the follow-up window) or has the wake word."""
        try:
            text = await transcribe(pcm)
        except RuntimeError as e:
            log.warning("%s", e)
            await ws.send_json({"type": "error", "message": str(e)})
            return
        request = strip_wake_word(text, config.WAKE_WORDS)
        if approvals:
            answer = yes_or_no(text if request is None else request)
            if answer is not None:
                for approval_id, future in list(approvals.items()):
                    if not future.done():
                        future.set_result(answer)
                        await ws.send_json({"type": "approval_closed", "id": approval_id, "reason": "answered", "approved": answer})
                return
        if not for_buddy and request is None:
            if text:
                log.info("(no wake word: not answered)")
            return
        await ws.send_json({"type": "transcript", "text": text})
        if not text:
            return
        await cancel_turn()  # a new question replaces an unfinished answer
        request = text if request is None else request
        start_turn(handle_user_text(ws, request, ask_user) if request else acknowledge(ws))

    async def hear(chunk: bytes) -> None:
        """Hands-free: feed the voice detector and act on each utterance it finds."""
        nonlocal addressed
        for event, pcm in detector.feed(chunk):
            if event == "start":
                addressed = listen_mode == "vad" or time.monotonic() < followup_until
                await ws.send_json({"type": "speech", "active": True, "addressed": addressed})
                continue
            await ws.send_json({"type": "speech", "active": False, "addressed": addressed})
            task = asyncio.create_task(handle_utterance(pcm, addressed))
            utterances.add(task)
            task.add_done_callback(utterances.discard)
            task.add_done_callback(_report_turn_failure)

    try:
        while True:
            message = await ws.receive()
            # Starlette returns a disconnect message rather than raising here.
            if message["type"] == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                if listen_mode == "push":
                    audio_chunks.append(message["bytes"])
                else:
                    await hear(message["bytes"])
                continue

            raw = message.get("text")
            if raw is None:
                continue
            # Also accept the plain "END" string used by the first test client.
            try:
                msg = {"type": "end"} if raw == "END" else json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "invalid JSON"})
                continue

            kind = msg.get("type")
            if kind == "status":
                # The desktop app asks about once a second while its startup loader shows.
                await ws.send_json(snapshot(await asyncio.to_thread(llm_status)))

            elif kind == "approval":
                answer = approvals.get(str(msg.get("id")))
                if answer is not None and not answer.done():
                    answer.set_result(bool(msg.get("approved")))

            elif kind == "cancel":
                audio_chunks = []  # drop a recording the client abandoned
                if detector is not None:
                    detector.reset()
                cancel_utterances()
                await cancel_turn()

            elif kind == "end":
                pcm = b"".join(audio_chunks)
                audio_chunks = []
                if not pcm:
                    log.warning("Asked to transcribe, but no audio arrived (is the app's microphone working?)")
                    await ws.send_json({"type": "error", "message": "no audio received"})
                    continue
                cancel_utterances()
                await cancel_turn()  # a new question replaces an unfinished answer
                start_turn(handle_voice(ws, pcm, ask_user))

            elif kind == "text" and str(msg.get("text", "")).strip():
                cancel_utterances()
                await cancel_turn()
                start_turn(handle_user_text(ws, str(msg["text"]).strip(), ask_user))

            elif kind == "listen":
                mode = msg.get("mode")
                if mode not in ("push", "vad", "wake"):
                    await ws.send_json({"type": "error", "message": f"unknown listening mode {mode!r}"})
                    continue
                if mode != "push" and detector is None:
                    try:
                        # In a thread: the first one loads the VAD model, which takes a moment.
                        detector = await asyncio.to_thread(VoiceDetector)
                    except Exception as e:  # onnxruntime or the model file missing
                        log.warning("Voice detection unavailable: %s", e)
                        await ws.send_json({"type": "error", "message": f"hands-free talking is unavailable: {e}"})
                        continue
                listen_mode = mode
                audio_chunks = []
                if detector is not None:
                    detector.reset()
                if msg.get("followup"):
                    followup_until = time.monotonic() + config.FOLLOWUP_SECONDS

            elif kind == "model":
                # The desktop tray's Model menu: {"provider", "model"}, or {"name": "gemma"}.
                try:
                    model = await select_model(str(msg.get("name") or ""), str(msg.get("provider") or ""), str(msg.get("model") or ""))
                    await ws.send_json({"type": "model", **_model_info(model)})
                except ValueError as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif kind == "history":
                await ws.send_json({"type": "history", "messages": SESSION.transcript()})

            elif kind == "reset":
                cancel_utterances()
                await cancel_turn()
                await SESSION.reset()
                await ws.send_json({"type": "history", "messages": []})

    except WebSocketDisconnect:
        pass
    finally:
        cancel_utterances()
        await cancel_turn()


LOCAL_HOSTS = {"127.0.0.1", "localhost"}
ALL_INTERFACES = {"0.0.0.0", "::"}


def resolve_host(host: str) -> str:
    """Return host if this PC owns that address, otherwise fall back to 127.0.0.1."""
    if host in LOCAL_HOSTS | ALL_INTERFACES:
        return host
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family) as probe:
            probe.bind((host, 0))  # port 0: only checks the address
        return host
    except OSError:
        log.warning(
            "HOST=%s is not an address of this PC, so Buddy can't listen on it. "
            "Using 127.0.0.1 (this PC only). Remove HOST from .env, "
            "or set LAN_ACCESS=true to let a phone connect.",
            host,
        )
        return "127.0.0.1"


def lan_addresses() -> list[str]:
    """This PC's likely Wi-Fi/Ethernet addresses, home-network (192.168.x) ones first.

    Skips loopback and addresses ending in .1, which on Windows are usually
    virtual adapters (WSL, Hyper-V, VMware, mobile hotspot) rather than the LAN.
    """
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    ips = {info[4][0] for info in infos}
    ips = [ip for ip in ips if not ip.startswith("127.") and not ip.endswith(".1")]
    return sorted(ips, key=lambda ip: (not ip.startswith("192.168."), ip))


def log_connect_info(host: str, port: int) -> None:
    if host in LOCAL_HOSTS:
        log.info("Phone access off: only this PC can connect, at ws://127.0.0.1:%d/ws", port)
        log.info("To allow a phone on the same Wi-Fi, set LAN_ACCESS=true in backend/.env")
    elif host in ALL_INTERFACES:
        log.info("Phone access on. This PC connects at ws://127.0.0.1:%d/ws", port)
        for ip in lan_addresses():
            log.info("A phone on the same network can try ws://%s:%d/ws", ip, port)
    else:
        log.info("Listening only on ws://%s:%d/ws", host, port)


if __name__ == "__main__":
    import uvicorn

    host = resolve_host(config.HOST)
    log_connect_info(host, config.PORT)
    uvicorn.run(app, host=host, port=config.PORT)
