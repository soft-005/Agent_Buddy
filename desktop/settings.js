// Buddy's desktop settings, saved as JSON in Electron's userData folder
// (%APPDATA%\buddy-desktop\settings.json on Windows). Changed from the tray menu.

const { app } = require("electron");
const fs = require("fs");
const path = require("path");

const SIZES = {
  small: { label: "Small", width: 280, height: 400 },
  medium: { label: "Medium", width: 360, height: 520 },
  large: { label: "Large", width: 460, height: 660 },
};

const DEFAULTS = {
  size: "medium",
  position: null, // {x, y} after you drag Buddy; null = bottom-right corner
  dockAfterSeconds: 60, // idle time before Buddy hides with its head peeking out; 0 = never
  pttKey: "CtrlRight", // key to hold for push-to-talk (see ptt.js); "" = off
  voice: "", // speechSynthesis voice name; "" = system default
  rate: 1.0, // speaking speed, 0.5-2
  pitch: 1.0, // voice pitch, 0-2
  volume: 1.0, // 0-1
  avatar: "", // a .vrm file name in assets/models; "" = automatic (see findAvatarUrl in main.js)
  handsFree: "off", // "off" | "vad" (always listening) | "wake" (only after the wake word)
  eyeContact: true, // the avatar's eyes and head follow the mouse pointer
  micReady: true, // keep the mic open (audio stays on this PC until you talk) so the first word isn't cut off
  server: "", // Buddy on another PC ("192.168.1.42:8818"); "" = this PC, at PORT in backend/.env
};

const HANDS_FREE = ["off", "vad", "wake"];

const file = () => path.join(app.getPath("userData"), "settings.json");

function load() {
  try {
    const saved = JSON.parse(fs.readFileSync(file(), "utf8"));
    const merged = { ...DEFAULTS, ...saved };
    if (!SIZES[merged.size]) merged.size = DEFAULTS.size;
    if (!HANDS_FREE.includes(merged.handsFree)) merged.handsFree = DEFAULTS.handsFree;
    return merged;
  } catch {
    return { ...DEFAULTS };
  }
}

function save(settings) {
  try {
    fs.mkdirSync(path.dirname(file()), { recursive: true });
    fs.writeFileSync(file(), JSON.stringify(settings, null, 2));
  } catch (e) {
    console.warn("Could not save settings:", e.message);
  }
}

module.exports = { SIZES, DEFAULTS, load, save };
