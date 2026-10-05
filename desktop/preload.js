// Exposes a small, safe API to the renderer.

const { contextBridge, ipcRenderer } = require("electron");

const on = (channel, callback) => ipcRenderer.on(channel, (_event, ...args) => callback(...args));

contextBridge.exposeInMainWorld("buddy", {
  // ws://127.0.0.1:<PORT in backend/.env>/ws, the address set on the Connection card,
  // or the BUDDY_SERVER_URL env var. onServerUrl fires when it changes.
  getServerUrl: () => ipcRenderer.invoke("get-server-url"),
  onServerUrl: (callback) => on("server-url", callback),
  // Connection card (tray): opens with {server, port, url}; setConnection returns {ok, message}.
  onEditConnection: (callback) => on("edit-connection", callback),
  setConnection: (value) => ipcRenderer.invoke("set-connection", value),

  // Tray's Model menu: {provider, model} to ask the backend for; modelChanged updates the menu.
  onSwitchModel: (callback) => on("switch-model", callback),
  modelChanged: () => ipcRenderer.send("model-changed"),

  setClickThrough: (ignore) => ipcRenderer.send("set-click-through", ignore),
  // file:// URL of the avatar model, or null when assets/models has no .vrm
  getAvatarUrl: () => ipcRenderer.invoke("get-avatar-url"),

  // Settings live in the main process (tray menu); the renderer reads and follows them.
  getSettings: () => ipcRenderer.invoke("get-settings"),
  onSettings: (callback) => on("settings", callback),
  reportVoices: (names) => ipcRenderer.send("voices", names),
  resizeStep: (step) => ipcRenderer.send("resize-step", step),

  setDocked: (docked) => ipcRenderer.send("set-docked", docked),
  onWake: (callback) => on("wake", callback),

  onToggleTalk: (callback) => on("toggle-talk", callback),
  onPushToTalk: (callback) => on("ptt", callback), // callback(true) on key down, (false) on release
  onTestVoice: (callback) => on("test-voice", callback),
  onNewConversation: (callback) => on("new-conversation", callback),

  // The mouse pointer anywhere on screen, relative to this window ({x, y}), or null
  // when eye contact is off. Sent only when it moves.
  onPointer: (callback) => on("pointer", callback),
});
