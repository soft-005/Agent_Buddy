"""Test client: sends a WAV clip (or typed text) and prints the server's replies.

Run from the backend/ folder:
    python scripts/test_client.py clip.wav
    python scripts/test_client.py --text "hello buddy"
    python scripts/test_client.py clip.wav --url ws://192.168.1.42:8818/ws
    python scripts/test_client.py --text "open notepad" --approve no
    python scripts/test_client.py clip.wav --handsfree wake   # stream it as hands-free audio
    python scripts/test_client.py --history                   # print the saved conversation
    python scripts/test_client.py --new-conversation          # start a new one

Tool approvals: --approve ask (default) asks in the terminal; yes or no answers
every request automatically.

--handsfree vad|wake streams the clip like a hands-free app (with a second of
silence before and after) instead of sending "end", and the server decides
where speech starts and ends. In wake mode the clip must start or end with the
wake word, or the server ignores it (then this client stops after 30 s).
"""

import argparse
import asyncio
import json
import time
import wave

import websockets


def read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 16000, "server expects 16 kHz audio"
        assert wf.getnchannels() == 1, "server expects mono audio"
        assert wf.getsampwidth() == 2, "server expects 16-bit audio"
        return wf.readframes(wf.getnframes())


async def send_audio(ws, pcm: bytes, realtime: bool) -> None:
    for i in range(0, len(pcm), 3200):  # about 0.1 s per chunk
        await ws.send(pcm[i:i + 3200])
        if realtime:
            await asyncio.sleep(0.1)


async def main(args) -> None:
    async with websockets.connect(args.url) as ws:
        if args.history or args.new_conversation:
            await ws.send(json.dumps({"type": "reset" if args.new_conversation else "history"}))
            data = json.loads(await ws.recv())
            for line in data.get("messages", []):
                print(f"{line['role']:>9}: {line['text']}")
            if args.new_conversation:
                print("Started a new conversation.")
            return

        if args.text:
            await ws.send(json.dumps({"type": "text", "text": args.text}))
        elif args.handsfree:
            await ws.send(json.dumps({"type": "listen", "mode": args.handsfree}))
            silence = bytes(32000)  # 1 s
            asyncio.create_task(send_audio(ws, silence + read_wav(args.wav) + silence * 2, realtime=True))
        else:
            await send_audio(ws, read_wav(args.wav), realtime=False)
            await ws.send(json.dumps({"type": "end"}))

        start = time.perf_counter()
        answered = not args.handsfree  # hands-free: until a transcript, the server may be ignoring us
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=None if answered else 30)
            except asyncio.TimeoutError:
                print("No reply: the server didn't hear the wake word, or no speech was found.")
                break
            elapsed = f"{time.perf_counter() - start:6.1f}s"
            if isinstance(msg, bytes):
                print(f"{elapsed}  [audio] {len(msg)} bytes")
                continue
            data = json.loads(msg)
            print(elapsed, data)
            answered = answered or data["type"] == "transcript"
            if data["type"] == "approval":
                if args.approve == "ask":
                    answer = await asyncio.to_thread(input, f"Allow: {data['title']}? [y/N] ")
                    approved = answer.strip().lower().startswith("y")
                else:
                    approved = args.approve == "yes"
                print(f"{elapsed}  -> {'allowed' if approved else 'denied'}")
                await ws.send(json.dumps({"type": "approval", "id": data["id"], "approved": approved}))
            if data["type"] in ("reply", "error") or (data["type"] == "transcript" and not data["text"]):
                break


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("wav", nargs="?")
    p.add_argument("--text")
    p.add_argument("--url", default="ws://127.0.0.1:8818/ws")
    p.add_argument("--approve", choices=["ask", "yes", "no"], default="ask", help="answer to tool approvals")
    p.add_argument("--handsfree", choices=["vad", "wake"], help="stream the WAV as hands-free audio")
    p.add_argument("--history", action="store_true", help="print the saved conversation")
    p.add_argument("--new-conversation", action="store_true", help="start a new conversation")
    a = p.parse_args()
    if not (a.wav or a.text or a.history or a.new_conversation):
        p.error("give a WAV file, --text, --history or --new-conversation")
    if a.handsfree and not a.wav:
        p.error("--handsfree needs a WAV file")
    asyncio.run(main(a))
