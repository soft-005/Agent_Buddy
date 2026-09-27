"""
Minimal test client — proves the PC<->phone connection works before you
build real mic capture into your mobile app.

Install:
    pip install websockets

Run (from your phone's Python env, or just from the PC itself to test locally first):
    python client_example.py path/to/test_clip.wav

Replace PC_LOCAL_IP with your PC's LAN IP (find it with `ipconfig` on Windows
or `ifconfig`/`ip a` on Mac/Linux). Set a DHCP reservation on your router so
this IP doesn't change.
"""

import asyncio
import sys
import wave

import websockets

PC_LOCAL_IP = "192.168.1.42"  # <-- change this to your PC's actual LAN IP
SERVER_URL = f"ws://{PC_LOCAL_IP}:8000/ws"


async def send_wav_file(path: str):
    with wave.open(path, "rb") as wf:
        assert wf.getframerate() == 16000, "server expects 16kHz audio"
        assert wf.getnchannels() == 1, "server expects mono audio"
        pcm_bytes = wf.readframes(wf.getnframes())

    async with websockets.connect(SERVER_URL) as ws:
        chunk_size = 3200  # ~0.1s of audio at 16kHz/16-bit
        for i in range(0, len(pcm_bytes), chunk_size):
            await ws.send(pcm_bytes[i:i + chunk_size])

        await ws.send("END")

        response = await ws.recv()
        print("Server response:", response)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python client_example.py path/to/test_clip.wav")
        sys.exit(1)

    asyncio.run(send_wav_file(sys.argv[1]))
