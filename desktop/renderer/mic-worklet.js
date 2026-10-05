// AudioWorklet: converts Float32 mic samples to 16-bit PCM and posts them
// to the main thread in ~0.1 s chunks. The AudioContext runs at 16 kHz,
// so the output matches what the backend expects (16 kHz, mono, int16).

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(1600); // 0.1 s at 16 kHz
    this.offset = 0;
    // "stop" when the mic closes. Without it this node kept running on the closed
    // stream's silence, and its chunks were sent along with the next recording's.
    this.stopped = false;
    this.port.onmessage = (e) => {
      if (e.data === "stop") this.stopped = true;
    };
  }

  process(inputs) {
    if (this.stopped) return false; // lets the node be freed
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.offset++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer.buffer.slice(0));
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
