"""
STT WebSocket server — runs on your PC.
Mobile/other LAN devices connect here, stream audio, get back a transcript.

Install:
    pip install fastapi uvicorn faster-whisper websockets

Run:
    python server.py
    # Server listens on 0.0.0.0:8000 so it's reachable from your phone
    # on the same router at ws://<your-pc-local-ip>:8000/ws
"""

import asyncio
import io
import wave

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

app = FastAPI()

# "small" is a good CPU speed/accuracy balance. Use "base" if you want it faster,
# "medium" if you have RAM/CPU to spare and want better accuracy.
MODEL_SIZE = "small"
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

SAMPLE_RATE = 16000  # send audio from the client at 16kHz mono for best results


def transcribe_wav_bytes(wav_bytes: bytes) -> str:
    """Run faster-whisper on a complete WAV clip and return the text."""
    audio_buffer = io.BytesIO(wav_bytes)
    segments, _info = model.transcribe(audio_buffer, language="en")
    return " ".join(segment.text.strip() for segment in segments)


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit PCM mono audio in a WAV header so whisper can read it."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


@app.websocket("/ws")
async def stt_socket(websocket: WebSocket):
    await websocket.accept()
    audio_chunks = []

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message and message["bytes"] is not None:
                # Raw 16-bit PCM audio chunk from the client mic stream
                audio_chunks.append(message["bytes"])

            elif "text" in message and message["text"] == "END":
                # Client signals "I'm done talking" -> transcribe what we have
                pcm = b"".join(audio_chunks)
                audio_chunks = []

                if not pcm:
                    await websocket.send_json({"type": "error", "message": "no audio received"})
                    continue

                wav_bytes = pcm_to_wav(pcm)
                loop = asyncio.get_event_loop()
                # transcribe is blocking/CPU-bound -> run off the event loop
                text = await loop.run_in_executor(None, transcribe_wav_bytes, wav_bytes)

                await websocket.send_json({"type": "transcript", "text": text})

                # --- Next steps you'll wire in later ---
                # response_text = call_llm_agent(text)
                # audio_reply = call_tts(response_text)
                # await websocket.send_json({"type": "reply", "text": response_text})
                # await websocket.send_bytes(audio_reply)

    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
