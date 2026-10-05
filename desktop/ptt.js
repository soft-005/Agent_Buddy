// Global push-to-talk: hold a key anywhere in Windows to talk to Buddy.
//
// Electron's globalShortcut only reports key presses, not releases, so this
// uses uiohook-napi, a keyboard hook that observes keys without blocking them.
// Nothing is recorded or sent: only the chosen key's down/up is acted on.
//
// To avoid false starts, talking begins after the key is held HOLD_MS, and
// pressing any other key (or a mouse button) first cancels it, so shortcuts
// like Right Ctrl+C still work normally.

const HOLD_MS = 250;

// Keys offered in the tray menu, by uiohook key name.
const KEYS = {
  CtrlRight: "Right Ctrl",
  AltRight: "Right Alt",
  CapsLock: "Caps Lock",
  F8: "F8",
  F9: "F9",
  Space: "Space (also types spaces in other apps)",
};

function createPushToTalk({ onStart, onStop }) {
  let hook;
  try {
    hook = require("uiohook-napi");
  } catch (e) {
    console.warn("Push-to-talk key unavailable (uiohook-napi failed to load):", e.message);
    return { available: false, setKey() {}, stop() {} };
  }
  const { uIOhook, UiohookKey } = hook;

  let keycode = null;
  let downAt = 0;
  let timer = null;
  let talking = false;

  const cancelPending = () => {
    if (downAt && !talking) clearTimeout(timer);
  };

  uIOhook.on("keydown", (e) => {
    if (keycode === null) return;
    if (e.keycode !== keycode) {
      cancelPending(); // a combination like Right Ctrl+C, not push-to-talk
      return;
    }
    if (downAt) return; // auto-repeat while held
    downAt = Date.now();
    timer = setTimeout(() => {
      talking = true;
      onStart();
    }, HOLD_MS);
  });

  uIOhook.on("keyup", (e) => {
    if (e.keycode !== keycode || !downAt) return;
    clearTimeout(timer);
    downAt = 0;
    if (talking) {
      talking = false;
      onStop();
    }
  });

  uIOhook.on("mousedown", cancelPending);
  try {
    uIOhook.start();
  } catch (e) {
    console.warn("Push-to-talk key unavailable (keyboard hook failed to start):", e.message);
    return { available: false, setKey() {}, stop() {} };
  }

  return {
    available: true,
    setKey(name) {
      keycode = name && UiohookKey[name] !== undefined ? UiohookKey[name] : null;
    },
    stop() {
      uIOhook.stop();
    },
  };
}

module.exports = { KEYS, createPushToTalk };
