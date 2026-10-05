// Renderer entry: connects to the backend, captures the mic (hold to talk, or
// hands-free), shows replies, speaks them sentence by sentence as they stream
// in with the mood of each sentence, docks Buddy when idle, and drives the avatar.

import { Avatar } from "./avatar.js";

const avatar = new Avatar(document.getElementById("avatar"));
const bubble = document.getElementById("bubble");
const bubbleText = document.getElementById("bubble-text");
const bubbleClose = document.getElementById("bubble-close");
const controls = document.getElementById("controls");
const talkBtn = document.getElementById("talk");
const textInput = document.getElementById("text-input");
const statusDot = document.getElementById("status");

const KEY_LABELS = { CtrlRight: "Right Ctrl", AltRight: "Right Alt", CapsLock: "Caps Lock", F8: "F8", F9: "F9", Space: "Space" };

// ---------- Settings (changed from the tray menu) ----------
let settings = { dockAfterSeconds: 60, pttKey: "", voice: "", rate: 1, pitch: 1, volume: 1, handsFree: "off" };
let avatarChoice; // undefined until the first settings arrive

function applySettings(next) {
  settings = next;
  const key = KEY_LABELS[settings.pttKey];
  talkBtn.title = {
    vad: "Listening: just talk. Or hold to talk.",
    wake: "Say “Hey Buddy”, or hold to talk.",
  }[settings.handsFree] ?? (key ? `Hold to talk, or hold ${key} anywhere` : "Hold to talk");
  if (avatarChoice !== undefined && next.avatar !== avatarChoice) avatar.load(); // switched in the tray
  avatarChoice = next.avatar;
  setHandsFree(settings.handsFree || "off");
  syncMic(); // "Keep the microphone ready"
}
window.buddy.getSettings().then(applySettings);
window.buddy.onSettings(applySettings);

// Eye contact: the main process reports the pointer anywhere on screen.
window.buddy.onPointer((p) => (p ? avatar.lookAtPointer(p.x, p.y) : avatar.lookAtViewer()));

// Tray: "New conversation". The backend archives the saved one.
window.buddy.onNewConversation(() => {
  wake();
  interrupt();
  if (ws?.readyState !== WebSocket.OPEN) {
    showBubble("(Buddy is offline, so the conversation can't be reset right now)");
    return;
  }
  send(JSON.stringify({ type: "reset" }));
  showBubble("(New conversation started)");
});

// ---------- Activity, docking and waking ----------
// After dockAfterSeconds with nothing happening, Buddy slides down so only its
// head peeks out from the bottom edge. Hovering the head, talking, or a new
// message wakes it.
let lastActivity = Date.now();
let docked = false;
const touch = () => (lastActivity = Date.now());

function dock() {
  if (docked) return;
  docked = true;
  document.body.classList.add("docked");
  hideBubble();
  setClickThrough(true);
  avatar.setDocked(true);
  window.buddy.setDocked(true);
}

function wake() {
  touch();
  if (!docked) return;
  docked = false;
  clearTimeout(hoverTimer);
  hoverTimer = null;
  document.body.classList.remove("docked");
  avatar.setDocked(false);
  window.buddy.setDocked(false);
}
window.buddy.onWake(wake);

setInterval(() => {
  const limit = (settings.dockAfterSeconds || 0) * 1000;
  if (!limit || docked) return;
  const typing = document.activeElement === textInput && textInput.value;
  const editing = !connectionCard.classList.contains("hidden");
  if (loading || approval || editing || listening || heardSpeech || pending > 0 || !replyDone || typing) return touch();
  if (Date.now() - lastActivity > limit) dock();
}, 1000);

// ---------- Click-through: only Buddy's controls capture the mouse ----------
let ignoring = true;
let hoverTimer;
function setClickThrough(ignore) {
  if (ignore === ignoring) return;
  ignoring = ignore;
  window.buddy.setClickThrough(ignore);
}

window.addEventListener("mousemove", (e) => {
  if (docked) {
    // Hovering the peeking head (bottom middle of the window) for a moment wakes Buddy.
    const overHead = e.clientY > innerHeight * 0.65 && Math.abs(e.clientX - innerWidth / 2) < innerWidth * 0.35;
    if (overHead && !hoverTimer) hoverTimer = setTimeout(wake, 500);
    if (!overHead) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    return;
  }
  const overUi = !!document.elementFromPoint(e.clientX, e.clientY)?.closest(".interactive");
  if (overUi) touch();
  setClickThrough(!overUi);
});
document.addEventListener("mouseleave", () => {
  clearTimeout(hoverTimer);
  hoverTimer = null;
});

// Ctrl + mouse wheel over the controls or bubble resizes Buddy (small/medium/large).
for (const el of [controls, bubble]) {
  el.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    window.buddy.resizeStep(e.deltaY < 0 ? 1 : -1);
  }, { passive: false });
}

// ---------- Speech bubble ----------
let hideTimer;
function showBubble(text, { user = false, sticky = false } = {}) {
  if (user) setToolNotes([]); // a new question: clear the last reply's action list
  bubbleText.textContent = text;
  bubble.classList.toggle("user", user);
  bubble.classList.remove("hidden");
  bubble.scrollTop = bubble.scrollHeight; // keep the newest sentence in view
  clearTimeout(hideTimer);
  if (!sticky) scheduleHide(12000);
}

function hideBubble() {
  clearTimeout(hideTimer);
  bubble.classList.add("hidden");
}

// After Buddy finishes speaking, the bubble closes by itself.
function scheduleHide(ms = 6000) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hideBubble, ms);
}

bubbleClose.addEventListener("click", hideBubble);

// ---------- Current reply ----------
let replyText = ""; // sentences received so far
let streamed = false; // this reply arrived as reply_chunk messages
let replyDone = true; // the final "reply" (or an error) has arrived
let muted = false; // the user talked over Buddy: ignore the rest of this reply
let chunkMood; // mood of the last reply_chunk, for the WAV frame that may follow it

function beginReply() {
  replyText = "";
  streamed = false;
  replyDone = false;
  muted = false;
  setToolNotes([]);
}

// ---------- Actions and approvals ----------
// Each action Buddy took shows as a line at the top of the bubble (✓ done, ✗ not
// done). Actions set to "ask" in backend/tools.json wait for Allow or Deny here.
const bubbleTools = document.getElementById("bubble-tools");
const approvalCard = document.getElementById("approval");
const approvalWhat = document.getElementById("approval-what");
const approvalDetail = document.getElementById("approval-detail");
const approvalTime = document.getElementById("approval-time");
let toolNotes = [];
let approval = null; // { id, deadline } while waiting for the user

function setToolNotes(notes) {
  toolNotes = notes;
  bubbleTools.replaceChildren(
    ...notes.map(({ ok, title }) => {
      const line = document.createElement("div");
      line.textContent = `${ok ? "✓" : "✗"} ${title}${ok ? "" : " (not done)"}`;
      if (!ok) line.className = "failed";
      return line;
    })
  );
}

function showApproval(msg) {
  approval = { id: msg.id, deadline: Date.now() + (msg.timeout || 60) * 1000 };
  approvalWhat.textContent = msg.title;
  approvalDetail.textContent = msg.detail || "";
  approvalCard.classList.remove("hidden");
  updateApprovalTime();
  const ask = msg.title.charAt(0).toLowerCase() + msg.title.slice(1);
  speakLocal(`May I ${ask}?`);
}

function answerApproval(approved) {
  if (!approval) return;
  send(JSON.stringify({ type: "approval", id: approval.id, approved }));
  closeApproval();
  touch();
}

function closeApproval() {
  approval = null;
  approvalCard.classList.add("hidden");
}

function updateApprovalTime() {
  if (approval) approvalTime.textContent = `${Math.max(0, Math.ceil((approval.deadline - Date.now()) / 1000))} s`;
}
setInterval(updateApprovalTime, 500);

document.getElementById("approval-allow").addEventListener("click", () => answerApproval(true));
document.getElementById("approval-deny").addEventListener("click", () => answerApproval(false));

function endReply() {
  replyDone = true;
  if (pending === 0) {
    avatar.setState("idle");
    scheduleHide();
  }
}

// ---------- WebSocket ----------
// The address comes from the main process: this PC at the PORT in backend/.env,
// or what the Connection card set. When it changes, the socket reconnects there.
let ws;
let serverUrl;
function connect() {
  ws = new WebSocket(serverUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    statusDot.className = "online";
    if (handsFree !== "off") send(JSON.stringify({ type: "listen", mode: handsFree }));
    if (loading) {
      setStep("backend", "done", "Buddy started");
      startStatusPolling();
    }
  };
  ws.onclose = () => {
    statusDot.className = "offline";
    stopStatusPolling();
    closeApproval();
    heardSpeech = false;
    talkBtn.classList.remove("hearing");
    if (!listening) talkBtn.classList.remove("listening");
    if (!replyDone) {
      // The backend went away mid-reply: don't stay stuck in "thinking".
      replyDone = true;
      if (pending === 0) avatar.setState("idle");
    }
    if (!loading && !docked) showLoader("Reconnecting to Buddy…");
    else if (loading) setStep("backend", "active", "Starting Buddy");
    setTimeout(connect, 2000); // keep retrying until the backend is up
  };

  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      wake();
      if (!muted) queueAudio(event.data, chunkMood); // WAV for the reply_chunk just received
      return;
    }
    const msg = JSON.parse(event.data);
    if (msg.type === "status") {
      applyStatus(msg);
      return;
    }
    if (msg.type === "history") return; // the desktop shows no chat log
    if (msg.type === "speech" && !msg.addressed) {
      // Wake word mode heard someone talking, maybe not to Buddy: only a hint on the mic button.
      talkBtn.classList.toggle("hearing", msg.active);
      return;
    }
    wake(); // anything else from Buddy's side brings it back out
    switch (msg.type) {
      case "speech": // hands-free: speech meant for Buddy started or ended
        talkBtn.classList.remove("hearing");
        if (msg.active) {
          heardSpeech = true;
          if (!approval) {
            // The backend replaces the old turn once this one is transcribed. (While
            // Buddy waits for permission, a spoken yes or no answers it instead.)
            stopSpeaking();
            if (!replyDone) muted = true;
          }
          talkBtn.classList.add("listening");
          avatar.setState("listening");
        } else if (heardSpeech) {
          heardSpeech = false;
          talkBtn.classList.remove("listening");
          avatar.setState("thinking"); // the transcript comes next
        }
        break;
      case "transcript":
        if (msg.text) {
          // Hands-free: a new turn starts now, so drop the old reply.
          stopSpeaking();
          closeApproval();
          if (!replyDone) muted = true;
          showBubble(msg.text, { user: true, sticky: true });
        } else avatar.setState(replyDone ? "idle" : "thinking");
        break;
      case "thinking":
        beginReply();
        avatar.setState("thinking");
        break;
      case "reply_chunk":
        if (muted) break;
        streamed = true;
        chunkMood = msg.mood;
        replyText = replyText ? `${replyText} ${msg.text}` : msg.text;
        showBubble(replyText, { sticky: true });
        if (!msg.audio) speakLocal(msg.text, msg.mood); // with audio, a WAV frame follows
        break;
      case "reply":
        if (muted) break;
        closeApproval();
        showBubble(msg.text, { sticky: true });
        if (!streamed) speakLocal(msg.text, msg.mood); // backend without streaming
        endReply();
        break;
      case "error":
        closeApproval();
        showBubble(`(${msg.message})`);
        replyDone = true;
        if (pending === 0) avatar.setState("idle");
        break;
      case "approval":
        if (muted) break;
        showApproval(msg);
        break;
      case "model": // switched models (tray menu, or "switch to ..." in chat)
        window.buddy.modelChanged(); // the tray's Model menu shows the new one
        if (replyDone) showBubble(`(Now using ${msg.name})`);
        break;
      case "approval_closed": // "timeout", or "answered" by voice (hands-free)
        if (approval && approval.id === msg.id) {
          closeApproval();
          if (msg.reason === "timeout") setToolNotes([...toolNotes, { ok: false, title: "No answer in time" }]);
        }
        break;
      case "tool":
        if (muted) break;
        setToolNotes([...toolNotes, { ok: msg.ok, title: msg.title }]);
        showBubble(replyText, { sticky: true });
        break;
    }
  };
}
window.buddy.getServerUrl().then((url) => {
  serverUrl = url;
  connect();
});
window.buddy.onServerUrl((url) => {
  if (url === serverUrl) return;
  serverUrl = url;
  ws?.close(); // onclose reconnects, now to the new address
});

// ---------- Connection card (tray: Connection…) ----------
// A port changes Buddy's port on this PC (PORT in backend/.env; the backend
// restarts on it). An address connects to Buddy on another PC. Empty = this PC.
const connectionCard = document.getElementById("connection");
const connectionInput = document.getElementById("connection-input");
const connectionMessage = document.getElementById("connection-message");

window.buddy.onEditConnection(({ server, port }) => {
  connectionInput.value = server || String(port);
  connectionMessage.className = "";
  connectionMessage.textContent = server
    ? `Clear it to use Buddy on this PC (port ${port}).`
    : `Buddy on this PC uses port ${port} (PORT in backend\\.env).`;
  connectionCard.classList.remove("hidden");
  connectionInput.focus();
  connectionInput.select();
});

async function saveConnection() {
  const result = await window.buddy.setConnection(connectionInput.value);
  connectionMessage.textContent = result.message;
  connectionMessage.className = result.ok ? "" : "failed";
  if (result.ok) setTimeout(() => connectionCard.classList.add("hidden"), 2500);
}

document.getElementById("connection-save").addEventListener("click", saveConnection);
document.getElementById("connection-cancel").addEventListener("click", () => connectionCard.classList.add("hidden"));
connectionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveConnection();
  if (e.key === "Escape") connectionCard.classList.add("hidden");
});

// Tray: Model menu. The backend switches and saves the choice in backend/.env.
window.buddy.onSwitchModel(({ provider, model }) => {
  wake();
  if (ws?.readyState !== WebSocket.OPEN) {
    showBubble("(Buddy is offline, so the model can't be switched right now)");
    return;
  }
  send(JSON.stringify({ type: "model", provider, model }));
  showBubble("(Switching models…)");
});

function send(data) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
}

// ---------- Speaking ----------
// Sentences are spoken in arrival order. Without backend TTS the system voice
// is used through speechSynthesis (it queues utterances itself); voice, speed,
// pitch and volume come from the tray menu. With backend TTS, WAV clips are
// played one after another.
let pending = 0; // utterances and clips queued or playing
let speechGen = 0; // bumped by stopSpeaking() so callbacks from cancelled speech are ignored
const audioQueue = [];
let audioPlaying = false;
let currentAudio = null;

// Report installed voices so the tray menu can list them.
function reportVoices() {
  const names = speechSynthesis.getVoices().map((v) => v.name);
  if (names.length) window.buddy.reportVoices(names);
}
speechSynthesis.addEventListener("voiceschanged", reportVoices);
reportVoices();

// mood: the face to show while this sentence is spoken (from the backend), or undefined to keep it.
function startedSpeaking(gen, mood) {
  if (gen !== speechGen) return;
  touch();
  pauseHandsFree();
  if (mood) avatar.setMood(mood);
  avatar.setState("speaking");
}

function finishedOne(gen) {
  if (gen !== speechGen) return;
  touch();
  pending = Math.max(0, pending - 1);
  if (pending > 0) return;
  // Between sentences of a slow model Buddy goes back to "thinking".
  avatar.setState(replyDone ? "idle" : "thinking");
  if (replyDone) scheduleHide();
  resumeHandsFree();
}

function speakLocal(text, mood) {
  const gen = speechGen;
  pending++;
  const utter = new SpeechSynthesisUtterance(text);
  const voice = settings.voice && speechSynthesis.getVoices().find((v) => v.name === settings.voice);
  if (voice) utter.voice = voice;
  utter.rate = settings.rate ?? 1;
  utter.pitch = settings.pitch ?? 1;
  utter.volume = settings.volume ?? 1;
  utter.onstart = () => startedSpeaking(gen, mood);
  utter.onend = () => finishedOne(gen);
  utter.onerror = () => finishedOne(gen);
  speechSynthesis.speak(utter);
}

window.buddy.onTestVoice(() => {
  wake();
  interrupt();
  replyDone = true;
  showBubble("Hi! This is how I sound now.");
  speakLocal("Hi! This is how I sound now.", "happy");
});

function queueAudio(buffer, mood) {
  pending++;
  audioQueue.push({ buffer, mood });
  if (!audioPlaying) playNext();
}

function playNext() {
  const { buffer, mood } = audioQueue.shift() ?? {};
  if (!buffer) {
    audioPlaying = false;
    return;
  }
  audioPlaying = true;
  const gen = speechGen;
  const url = URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
  const audio = new Audio(url);
  audio.volume = settings.volume ?? 1;
  currentAudio = audio;
  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    URL.revokeObjectURL(url);
    if (gen !== speechGen) return;
    finishedOne(gen);
    playNext();
  };
  audio.onplay = () => startedSpeaking(gen, mood);
  audio.onended = done;
  audio.onerror = done;
  audio.play().catch(done);
}

function stopSpeaking() {
  speechGen++;
  pending = 0;
  audioQueue.length = 0;
  audioPlaying = false;
  speechSynthesis.cancel();
  currentAudio?.pause();
  currentAudio = null;
  if (handsFreePaused) resumeHandsFree({ now: true });
}

// The user started a new turn: cut Buddy off, drop the rest of the old reply,
// and tell the backend to stop generating it.
function interrupt() {
  stopSpeaking();
  closeApproval(); // the backend cancels the turn, which also withdraws the request
  if (!replyDone) muted = true;
  send(JSON.stringify({ type: "cancel" }));
}

// ---------- Microphone ----------
// With "Keep the microphone ready" (tray, on by default) the mic stays open and
// the last half second is kept here, never sent, so a recording can start with
// the moment before the key went down: opening the mic takes 0.3-1 s, and the
// hold-to-talk key waits 250 ms, which used to cut off the first word. Without
// it the mic opens only while holding to talk. Hands-free keeps it open too.
const MIN_RECORDING_SAMPLES = 16000 * 0.3; // shorter than 0.3 s counts as an accidental tap
const PREROLL_CHUNKS = 5; // 0.1 s each
const MIC_BLOCKED = "(I can't hear you: the microphone is unavailable or blocked in Windows privacy settings)";
let audioCtx, micStream, micSource, micNode;
let micOpening = null; // the open in progress, so two callers don't open two streams
let preroll = []; // the last PREROLL_CHUNKS chunks while nobody is talking
let listening = false; // holding to talk
let listenSource = null; // "button" | "key" | "toggle": only the same input ends it
let samplesSent = 0;

function openMic() {
  if (micStream) return Promise.resolve();
  micOpening ??= (async () => {
    if (!audioCtx) {
      audioCtx = new AudioContext({ sampleRate: 16000 });
      await audioCtx.audioWorklet.addModule("mic-worklet.js");
    }
    await audioCtx.resume();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    // Unplugged or switched off: close, so the next use opens the mic again.
    micStream.getAudioTracks()[0]?.addEventListener("ended", closeMic);
    micSource = audioCtx.createMediaStreamSource(micStream);
    micNode = new AudioWorkletNode(audioCtx, "pcm-capture");
    micNode.port.onmessage = (e) => onMicChunk(e.data);
    micSource.connect(micNode);
  })().finally(() => (micOpening = null));
  return micOpening;
}

// Every part has to go: an old worklet left connected kept sending the closed
// stream's silence, interleaved with the next recording (every other 0.1 s chunk
// silent after one use, two of three after two), so voice got worse with each press.
function closeMic() {
  micSource?.disconnect();
  micSource = null;
  if (micNode) {
    micNode.port.onmessage = null;
    micNode.port.postMessage("stop");
    micNode.disconnect();
    micNode = null;
  }
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null;
  preroll = [];
}

// Whether the mic should stay open when nobody is holding to talk.
const micWanted = () => handsFree !== "off" || settings.micReady !== false;

function onMicChunk(pcm) {
  if (listening) {
    samplesSent += pcm.byteLength / 2;
    send(pcm);
  } else if (handsFree !== "off" && !handsFreePaused) {
    send(pcm); // the backend finds where speech starts and ends
  } else if (pending > 0) {
    preroll = []; // Buddy is speaking: don't start a recording with its voice
  } else {
    preroll.push(pcm);
    if (preroll.length > PREROLL_CHUNKS) preroll.shift();
  }
}

// Open or close the mic to match the settings (when nobody is holding to talk).
async function syncMic() {
  if (listening) return;
  if (!micWanted()) {
    closeMic();
    return;
  }
  try {
    await openMic();
  } catch (error) {
    console.warn("Microphone unavailable:", error); // reported when the user tries to talk
  }
}

async function startListening(source) {
  if (listening) return;
  listening = true;
  listenSource = source;
  samplesSent = 0;
  wake();
  interrupt();
  if (handsFree !== "off") send(JSON.stringify({ type: "listen", mode: "push" })); // until the key is released
  talkBtn.classList.add("listening");
  avatar.setState("listening");
  // The half second before the press (the mic is usually open already). Not
  // counted in samplesSent, so a quick accidental tap is still discarded.
  for (const chunk of preroll) send(chunk);
  preroll = [];

  try {
    await openMic();
    if (!listening && !micWanted()) closeMic(); // released before the mic was ready
  } catch (error) {
    console.error("Microphone unavailable:", error);
    listening = false;
    listenSource = null;
    talkBtn.classList.remove("listening");
    avatar.setState("idle");
    showBubble(MIC_BLOCKED);
  }
}

function stopListening(source) {
  if (!listening || (source && source !== listenSource)) return;
  listening = false;
  listenSource = null;
  touch();
  talkBtn.classList.remove("listening");
  if (!micWanted()) closeMic();
  if (samplesSent < MIN_RECORDING_SAMPLES) {
    // Too short to be speech: discard it on the backend too.
    send(JSON.stringify({ type: "cancel" }));
    avatar.setState("idle");
  } else {
    send(JSON.stringify({ type: "end" }));
    avatar.setState("thinking");
  }
  if (handsFree !== "off") send(JSON.stringify({ type: "listen", mode: handsFree }));
}

// ---------- Hands-free talking ----------
// With hands-free on (tray menu), the mic stays open and streams to the backend,
// which finds where speech starts and ends. "vad" answers everything; "wake"
// answers only sentences with the wake word ("Hey Buddy, ..."). Streaming pauses
// while Buddy speaks so it doesn't hear itself, and resumes a moment after; in
// wake mode that also opens a short window to reply without the wake word.
let handsFree = "off";
let handsFreePaused = false;
let resumeTimer = null;
let heardSpeech = false; // the backend hears speech meant for Buddy right now

async function setHandsFree(mode) {
  if (mode === handsFree) return;
  handsFree = mode;
  document.body.classList.toggle("handsfree", mode !== "off");
  send(JSON.stringify({ type: "listen", mode: mode === "off" ? "push" : mode }));
  if (mode === "off") {
    heardSpeech = false;
    talkBtn.classList.remove("hearing");
    syncMic();
    return;
  }
  try {
    await openMic();
  } catch (error) {
    console.error("Microphone unavailable:", error);
    showBubble(MIC_BLOCKED);
  }
}

function pauseHandsFree() {
  clearTimeout(resumeTimer);
  resumeTimer = null;
  handsFreePaused = true;
}

// Buddy stopped speaking: listen again. { now: true } when it was cut off.
function resumeHandsFree({ now = false } = {}) {
  clearTimeout(resumeTimer);
  resumeTimer = null;
  if (!handsFreePaused) return;
  const resume = () => {
    resumeTimer = null;
    handsFreePaused = false;
    // Tells the backend to start fresh; after a finished reply, the follow-up window opens.
    if (handsFree !== "off") send(JSON.stringify({ type: "listen", mode: handsFree, followup: !now && replyDone }));
  };
  if (now) resume();
  else resumeTimer = setTimeout(resume, 400); // let the last sound fade first
}

// Hold the mic button, hold the push-to-talk key anywhere, or toggle with Ctrl+Shift+Space.
talkBtn.addEventListener("mousedown", () => startListening("button"));
talkBtn.addEventListener("mouseup", () => stopListening("button"));
talkBtn.addEventListener("mouseleave", () => stopListening("button"));
window.buddy.onPushToTalk((down) => (down ? startListening("key") : stopListening("key")));
window.buddy.onToggleTalk(() => (listening ? stopListening() : startListening("toggle")));

// ---------- Typed input ----------
textInput.addEventListener("keydown", (e) => {
  touch();
  if (e.key !== "Enter" || !textInput.value.trim()) return;
  const text = textInput.value.trim();
  textInput.value = "";
  interrupt();
  showBubble(text, { user: true, sticky: true });
  send(JSON.stringify({ type: "text", text }));
});

// ---------- Startup loader ----------
// Shown while Buddy starts (or reconnects) until the backend, speech recognition
// and the language model are ready. While it shows, the app asks the backend for
// {"type": "status"} once a second.
const loader = document.getElementById("loader");
const loaderTitle = document.getElementById("loader-title");
const loaderTime = document.getElementById("loader-time");
const loaderHint = document.getElementById("loader-hint");
const steps = Object.fromEntries([...document.querySelectorAll("#loader-steps li")].map((li) => [li.dataset.step, li]));
const LMSTUDIO_GRACE_MS = 30000; // LM Studio's server may take a while to come up

let loading = false;
let loadStarted = 0;
let llmOfflineSince = 0;
let statusTimer = null;
let fadeTimer = null;
let sttWarned = false;

function setStep(name, state, label) {
  steps[name].className = state; // "" = waiting, "active", "done", "error"
  if (label) steps[name].textContent = label;
}

function showLoader(title) {
  loading = true;
  loadStarted = Date.now();
  llmOfflineSince = 0;
  clearTimeout(fadeTimer);
  loader.classList.remove("hidden", "fading", "ready");
  loaderTitle.textContent = title;
  loaderTime.textContent = "";
  loaderHint.textContent = "";
  setStep("backend", "active", "Starting Buddy");
  setStep("stt", "", "Speech recognition");
  setStep("llm", "", "Language model");
  if (!listening && pending === 0 && replyDone) avatar.setState("thinking");
}

function finishLoading() {
  loading = false;
  stopStatusPolling();
  loader.classList.add("ready");
  loaderTitle.textContent = "Ready!";
  loaderHint.textContent = "";
  if (!listening && pending === 0 && replyDone) avatar.setState("idle");
  touch();
  fadeTimer = setTimeout(() => {
    loader.classList.add("fading");
    fadeTimer = setTimeout(() => loader.classList.add("hidden"), 400);
  }, 1200);
}

function startStatusPolling() {
  stopStatusPolling();
  send(JSON.stringify({ type: "status" }));
  statusTimer = setInterval(() => send(JSON.stringify({ type: "status" })), 1000);
}

function stopStatusPolling() {
  clearInterval(statusTimer);
  statusTimer = null;
}

setInterval(() => {
  if (loading) loaderTime.textContent = `${Math.round((Date.now() - loadStarted) / 1000)} s`;
}, 500);

function applyStatus(status) {
  if (!loading) return;
  const model = status.model_name || "the language model";
  let hint = "";

  if (status.stt === "ready") setStep("stt", "done", "Speech recognition ready");
  else if (status.stt === "error") setStep("stt", "error", "Speech recognition failed");
  else setStep("stt", "active", "Loading speech recognition");

  if (status.llm === "offline") {
    llmOfflineSince ||= Date.now();
    if (Date.now() - llmOfflineSince < LMSTUDIO_GRACE_MS) {
      setStep("llm", "active", "Waiting for LM Studio");
    } else {
      setStep("llm", "error", "LM Studio isn't running");
      hint = "Open LM Studio and start its server (Developer tab).";
    }
  } else {
    llmOfflineSince = 0;
    if (status.llm === "ready") setStep("llm", "done", `${model} ready`);
    else if (status.llm === "missing") {
      setStep("llm", "error", `${model} isn't in LM Studio`);
      hint = "Download it in LM Studio, or change LLM_MODEL in backend\.env.";
    } else {
      setStep("llm", "active", `Loading ${model}`);
      hint = "Loading the model into memory can take a minute or two.";
    }
  }
  loaderHint.textContent = hint;

  // Speech recognition failing doesn't block Buddy: typing still works.
  if (status.stt !== "loading" && status.llm === "ready") {
    if (status.stt === "error" && !sttWarned) {
      sttWarned = true;
      showBubble("(Speech recognition didn't load, so voice won't work. Typing still does. See logs\backend.log)");
    }
    finishLoading();
  }
}

showLoader("Buddy is waking up\u2026");
