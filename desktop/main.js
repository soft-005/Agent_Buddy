// Electron main process: a transparent, frameless, always-on-top overlay
// window that lets clicks pass through to the desktop except over Buddy's own
// controls. It also owns the tray menu (settings, avatars, quit), window size
// and position, docking to the screen edge, the global push-to-talk key, and
// tracking the mouse pointer for the avatar's eye contact.

const { app, BrowserWindow, Menu, Tray, dialog, globalShortcut, ipcMain, screen, session, shell } = require("electron");
const os = require("os");
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const settingsStore = require("./settings");
const { KEYS, createPushToTalk } = require("./ptt");

const { SIZES } = settingsStore;
const MODELS_DIR = path.join(__dirname, "assets", "models");
const ICON = path.join(__dirname, "assets", "icon.png");
const MARGIN = 16; // gap from the right screen edge in the default position

// The launcher (../buddy.ps1) writes this file when it starts the backend in the
// background, so quitting Buddy from the tray can stop that backend too.
const PROJECT_ROOT = path.join(__dirname, "..");
const LAUNCHER = path.join(PROJECT_ROOT, "buddy.ps1");
const BACKEND_PID_FILE = path.join(PROJECT_ROOT, "logs", "backend.pid");
const TOOLS_FILE = path.join(PROJECT_ROOT, "backend", "tools.json"); // what Buddy may do on this PC
const BACKEND_ENV = path.join(PROJECT_ROOT, "backend", ".env");
const PROVIDERS_FILE = path.join(PROJECT_ROOT, "backend", "providers.json");
const DEFAULT_PORT = 8818; // the backend's default PORT

// A setting from backend/.env (NAME=value lines), or fallback.
function backendSetting(name, fallback) {
  try {
    const match = fs.readFileSync(BACKEND_ENV, "utf8").match(new RegExp(`^\\s*${name}\\s*=\\s*(.*?)\\s*$`, "m"));
    const value = match?.[1].replace(/^["']|["']$/g, "");
    if (value) return value;
  } catch {
    // no .env: the backend's defaults
  }
  return fallback;
}

// Set NAME=value in backend/.env, keeping the rest of the file.
function setBackendSetting(name, value) {
  let text = "";
  try {
    text = fs.readFileSync(BACKEND_ENV, "utf8");
  } catch {
    // no .env yet: start one
  }
  const line = new RegExp(`^\\s*${name}\\s*=.*$`, "m");
  text = line.test(text) ? text.replace(line, `${name}=${value}`) : `${text.replace(/\s*$/, "")}\n${name}=${value}\n`;
  fs.writeFileSync(BACKEND_ENV, text);
}

const localPort = () => parseInt(backendSetting("PORT", ""), 10) || DEFAULT_PORT;

// The wake word for the tray menu's label: the first of WAKE_WORDS in backend/.env.
function wakeWord() {
  const word = backendSetting("WAKE_WORDS", "buddy").split(",")[0].trim();
  return word ? word.charAt(0).toUpperCase() + word.slice(1) : "Buddy";
}

// ---------- Connection ----------
// Buddy's backend on this PC, at the PORT in backend/.env (8818 by default), unless
// the Connection card (tray) set another address, or BUDDY_SERVER_URL is set.
function toWebSocketUrl(address) {
  let text = address.trim();
  if (!/^[a-z]+:\/\//i.test(text)) text = `ws://${text}`;
  const url = new URL(text.replace(/^http/i, "ws"));
  if (!url.port) url.port = String(DEFAULT_PORT);
  if (url.pathname === "/" || !url.pathname) url.pathname = "/ws";
  return url.href;
}

function serverUrl() {
  if (process.env.BUDDY_SERVER_URL) return process.env.BUDDY_SERVER_URL;
  if (settings.server) {
    try {
      return toWebSocketUrl(settings.server);
    } catch {
      // a broken saved address: use this PC
    }
  }
  return `ws://127.0.0.1:${localPort()}/ws`;
}

const serverLabel = () => serverUrl().replace(/^wss?:\/\//, "").replace(/\/ws$/, "");

// From the Connection card: a port number (Buddy on this PC: saved as PORT in
// backend/.env, and the backend restarts on it), an address like
// 192.168.1.42:8818 (Buddy on another PC), or nothing (back to this PC).
// Returns a message for the card.
function setConnection(value) {
  const text = String(value || "").trim();
  if (/^:?\d+$/.test(text)) {
    const port = Number(text.replace(":", ""));
    if (port < 1024 || port > 65535) return { ok: false, message: "Use a port between 1024 and 65535." };
    const oldPort = localPort();
    update({ server: "" });
    if (port !== oldPort) {
      setBackendSetting("PORT", port);
      restartBackend(oldPort);
      rebuildTrayMenu(); // the Connection item shows the new port
    }
    win?.webContents.send("server-url", serverUrl());
    return { ok: true, message: port !== oldPort ? `Restarting Buddy's backend on port ${port}…` : `Using this PC, port ${port}.` };
  }
  if (text) {
    try {
      toWebSocketUrl(text);
    } catch {
      return { ok: false, message: "That doesn't look like an address. Try 192.168.1.42:8818, or just a port." };
    }
  }
  update({ server: text });
  win?.webContents.send("server-url", serverUrl());
  return { ok: true, message: text ? `Connecting to ${serverLabel()}…` : "Using Buddy on this PC." };
}

// The launcher stops the backend (on the old port too) and starts it on the new one.
function restartBackend(oldPort) {
  if (!fs.existsSync(LAUNCHER)) return;
  const { spawn } = require("child_process");
  const child = spawn(
    "powershell.exe",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", LAUNCHER, "restart-backend", "-OldPort", String(oldPort), "-Popup"],
    { stdio: "ignore", windowsHide: true, detached: true }
  );
  child.unref();
}

// ---------- Language model ----------
// The models in backend/providers.json; the tray's Model menu switches between them
// (the backend switches live and saves the choice in backend/.env).
function listModels() {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, "utf8")).providers || {};
    return Object.entries(providers).flatMap(([provider, p]) =>
      (p.models || []).map((m) => ({ provider, model: m.id, name: m.name || m.id }))
    );
  } catch {
    return [];
  }
}

function modelMenu() {
  const models = listModels();
  const provider = backendSetting("LLM_PROVIDER", "lmstudio");
  const current = backendSetting("LLM_MODEL", "") || models.find((m) => m.provider === provider)?.model;
  if (!models.length) return [{ label: "No models in backend/providers.json", enabled: false }];
  return models.map((m) => ({
    label: `${m.name}  (${m.provider})`,
    type: "radio",
    checked: m.provider === provider && m.model === current,
    click: () => win?.webContents.send("switch-model", { provider: m.provider, model: m.model }),
  }));
}

// The first allowed folder in tools.json: Buddy's own workspace.
function buddyFolder() {
  let folder = "~/Documents/Buddy";
  try {
    folder = JSON.parse(fs.readFileSync(TOOLS_FILE, "utf8")).allowed_folders?.[0] || folder;
  } catch {
    // missing or broken tools.json: use the default
  }
  return path.resolve(folder.replace(/^~(?=$|[\\/])/, os.homedir()));
}

function openBuddyFolder() {
  const folder = buddyFolder();
  fs.mkdirSync(folder, { recursive: true });
  shell.openPath(folder);
}

// Commands a second launch can send to the running Buddy, e.g. `electron . toggle`.
const COMMANDS = ["show", "hide", "toggle", "quit"];
const cliCommand = process.argv.slice(1).map((a) => a.replace(/^-+/, "")).find((a) => COMMANDS.includes(a));

let win;
let tray;
let ptt;
let settings;
let voices = []; // speechSynthesis voice names reported by the renderer
let docked = false;
let undockedBounds = null; // where Buddy was before docking moved it down
let movingByCode = false; // true while Buddy moves its own window (not saved as your position)

// ---------- Avatar file ----------
// The .vrm files in assets/models, alphabetically.
function listAvatars() {
  try {
    return fs.readdirSync(MODELS_DIR).filter((n) => n.toLowerCase().endsWith(".vrm")).sort();
  } catch {
    return []; // folder missing: the placeholder shows
  }
}

// What "Automatic" picks: avatar.vrm, otherwise the first .vrm (the backend serves the same one to the phone).
function automaticAvatar(names) {
  return names.includes("avatar.vrm") ? "avatar.vrm" : names[0];
}

// The avatar chosen in the tray menu, else the BUDDY_AVATAR env var, else the
// automatic choice. Returns a file:// URL or null.
function findAvatarUrl() {
  const names = listAvatars();
  let file;
  if (settings.avatar && names.includes(settings.avatar)) file = path.join(MODELS_DIR, settings.avatar);
  else if (process.env.BUDDY_AVATAR) file = path.resolve(process.env.BUDDY_AVATAR);
  else file = automaticAvatar(names) && path.join(MODELS_DIR, automaticAvatar(names));
  return file && fs.existsSync(file) ? pathToFileURL(file).href : null;
}

// Copy a .vrm from anywhere into assets/models and switch to it.
async function addAvatar() {
  const { canceled, filePaths } = await dialog.showOpenDialog({
    title: "Add an avatar",
    filters: [{ name: "VRM avatars", extensions: ["vrm"] }],
    properties: ["openFile"],
  });
  if (canceled || !filePaths.length) return;
  const source = path.resolve(filePaths[0]);
  let name = path.basename(source);
  try {
    if (path.dirname(source) !== path.resolve(MODELS_DIR)) {
      fs.mkdirSync(MODELS_DIR, { recursive: true });
      const { name: stem, ext } = path.parse(name);
      for (let n = 2; fs.existsSync(path.join(MODELS_DIR, name)); n++) name = `${stem} (${n})${ext}`;
      fs.copyFileSync(source, path.join(MODELS_DIR, name));
    }
  } catch (e) {
    dialog.showErrorBox("Couldn't add the avatar", e.message);
    return;
  }
  update({ avatar: name });
}

function openAvatarsFolder() {
  fs.mkdirSync(MODELS_DIR, { recursive: true });
  shell.openPath(MODELS_DIR);
}

// Keep the tray's avatar list current when files are added or removed by hand.
let avatarWatcher = null;
function watchAvatars() {
  let timer;
  try {
    avatarWatcher = fs.watch(MODELS_DIR, () => {
      clearTimeout(timer);
      timer = setTimeout(rebuildTrayMenu, 500);
    });
  } catch {
    // folder missing: the list refreshes when the menu is rebuilt for another reason
  }
}

// ---------- Eye contact ----------
// The renderer only sees the pointer while it's over Buddy's window, so the
// main process polls it and sends its position relative to the window.
let lastPointer = "";
let pointerTimer = null;
function pollPointer() {
  if (!win || win.isDestroyed() || !win.isVisible() || !settings.eyeContact) return;
  const p = screen.getCursorScreenPoint();
  const b = win.getBounds();
  const key = `${p.x},${p.y},${b.x},${b.y}`;
  if (key === lastPointer) return; // the avatar looks back at you once the pointer rests
  lastPointer = key;
  win.webContents.send("pointer", { x: p.x - b.x, y: p.y - b.y });
}

// ---------- Window size and position ----------
function defaultBounds() {
  const { workArea: wa } = screen.getPrimaryDisplay();
  const { width, height } = SIZES[settings.size];
  return { x: wa.x + wa.width - width - MARGIN, y: wa.y + wa.height - height, width, height };
}

function isOnScreen(b) {
  return screen.getAllDisplays().some(({ workArea: w }) =>
    b.x < w.x + w.width - 40 && b.x + b.width > w.x + 40 && b.y < w.y + w.height - 40 && b.y + b.height > w.y + 40
  );
}

function initialBounds() {
  const { width, height } = SIZES[settings.size];
  if (settings.position) {
    const saved = { ...settings.position, width, height };
    if (isOnScreen(saved)) return saved;
  }
  return defaultBounds();
}

// Windows display scaling can round the result by a pixel or two.
function setBoundsByCode(bounds) {
  movingByCode = true;
  win.setBounds(bounds);
  setTimeout(() => (movingByCode = false), 300);
}

// Same bottom-right corner, new size: Buddy grows up and to the left.
function anchored(b, size) {
  return { x: b.x + b.width - size.width, y: b.y + b.height - size.height, width: size.width, height: size.height };
}

function applySize() {
  const size = SIZES[settings.size];
  if (docked && undockedBounds) undockedBounds = anchored(undockedBounds, size);
  setBoundsByCode(anchored(win.getBounds(), size));
}

function moveToCorner() {
  update({ position: null });
  if (docked) wake();
  undockedBounds = null;
  setBoundsByCode(defaultBounds());
}

// ---------- Docking ----------
// When idle, the renderer slides the avatar down so only its head peeks out.
// If Buddy isn't already at the bottom of the screen, the window first moves
// down so the head peeks from the screen edge; waking moves it back.
function setDocked(on) {
  if (on === docked || !win) return;
  docked = on;
  const wa = screen.getDisplayMatching(win.getBounds()).workArea;
  if (on) {
    const b = win.getBounds();
    const bottom = wa.y + wa.height;
    undockedBounds = null;
    if (b.y + b.height < bottom - 2) {
      undockedBounds = b;
      setBoundsByCode({ ...b, y: bottom - b.height });
    }
  } else if (undockedBounds) {
    setBoundsByCode(undockedBounds);
    undockedBounds = null;
  }
  rebuildTrayMenu();
}

function wake() {
  if (!win) return;
  if (!win.isVisible()) win.showInactive();
  win.webContents.send("wake");
}

// ---------- Settings ----------
function update(changes) {
  settings = { ...settings, ...changes };
  settingsStore.save(settings);
  if (win && "size" in changes) applySize();
  if (ptt && "pttKey" in changes) ptt.setKey(settings.pttKey);
  if (win && "eyeContact" in changes && !settings.eyeContact) {
    lastPointer = "";
    win.webContents.send("pointer", null);
  }
  if (win) win.webContents.send("settings", settings);
  rebuildTrayMenu();
}

// ---------- Tray menu ----------
function radio(options, key) {
  return options.map(([value, label]) => ({
    label,
    type: "radio",
    checked: settings[key] === value,
    click: () => update({ [key]: value }),
  }));
}

function avatarMenu() {
  const names = listAvatars();
  const automatic = automaticAvatar(names);
  const label = (name) => name.replace(/\.vrm$/i, "");
  // A chosen file that was deleted counts as automatic.
  const chosen = names.includes(settings.avatar) ? settings.avatar : "";
  return [
    ...[["", automatic ? `Automatic (${label(automatic)})` : "Automatic"], ...names.map((n) => [n, label(n)])].map(
      ([value, text]) => ({ label: text, type: "radio", checked: chosen === value, click: () => update({ avatar: value }) })
    ),
    { type: "separator" },
    { label: "Add avatar…", click: addAvatar },
    { label: "Open avatars folder", click: openAvatarsFolder },
  ];
}

function rebuildTrayMenu() {
  if (!tray) return;
  const visible = win && !win.isDestroyed() && win.isVisible();
  const menu = Menu.buildFromTemplate([
    { label: visible ? "Hide Buddy" : "Show Buddy", click: toggleVisible },
    { label: "Wake Buddy up", enabled: docked, click: wake },
    { label: "New conversation", click: () => win?.webContents.send("new-conversation") },
    { label: "Model", submenu: modelMenu() },
    {
      label: `Connection: ${serverLabel()}…`,
      click: () => {
        if (!win) return;
        wake();
        win.show();
        win.focus(); // so the card's field can be typed in
        win.webContents.send("edit-connection", { server: settings.server, port: localPort(), url: serverUrl() });
      },
    },
    { type: "separator" },
    { label: "Avatar", submenu: avatarMenu() },
    {
      label: "Eyes follow the mouse pointer",
      type: "checkbox",
      checked: settings.eyeContact,
      click: () => update({ eyeContact: !settings.eyeContact }),
    },
    { label: "Size", submenu: radio(Object.entries(SIZES).map(([k, v]) => [k, v.label]), "size") },
    {
      label: "Hide in corner when idle",
      submenu: radio(
        [[0, "Never"], [30, "After 30 seconds"], [60, "After 1 minute"], [120, "After 2 minutes"], [300, "After 5 minutes"]],
        "dockAfterSeconds"
      ),
    },
    {
      label: ptt?.available ? "Hold-to-talk key" : "Hold-to-talk key (unavailable)",
      enabled: !!ptt?.available,
      submenu: radio([...Object.entries(KEYS), ["", "Off"]], "pttKey"),
    },
    {
      label: "Keep the microphone ready (catches your first word)",
      type: "checkbox",
      checked: settings.micReady,
      click: () => update({ micReady: !settings.micReady }),
    },
    {
      label: "Hands-free talking",
      submenu: radio(
        [
          ["off", "Off (hold a key or the mic button)"],
          ["vad", "Always listening"],
          ["wake", `Only after “Hey ${wakeWord()}”`],
        ],
        "handsFree"
      ),
    },
    { type: "separator" },
    { label: "Voice", submenu: radio([["", "System default"], ...voices.map((v) => [v, v])], "voice") },
    {
      label: "Speaking speed",
      submenu: radio([[0.8, "Slow"], [0.9, "A bit slow"], [1, "Normal"], [1.15, "A bit fast"], [1.3, "Fast"]], "rate"),
    },
    {
      label: "Voice pitch",
      submenu: radio([[0.7, "Low"], [0.85, "A bit low"], [1, "Normal"], [1.2, "A bit high"], [1.4, "High"]], "pitch"),
    },
    { label: "Volume", submenu: radio([[0.4, "Quiet"], [0.7, "Medium"], [1, "Full"]], "volume") },
    { label: "Test voice", click: () => win?.webContents.send("test-voice") },
    { type: "separator" },
    { label: "Open Buddy's folder", click: openBuddyFolder },
    { label: "Edit permissions…", click: () => shell.openPath(TOOLS_FILE) },
    { type: "separator" },
    { label: "Move back to corner", click: moveToCorner },
    { label: "Quit Buddy", click: () => app.quit() },
  ]);
  tray.setContextMenu(menu);
}

function toggleVisible() {
  if (!win) return;
  if (win.isVisible()) win.hide();
  else win.showInactive();
  rebuildTrayMenu();
}

function runCommand(command) {
  if (!win) return;
  if (command === "quit") app.quit();
  else if (command === "hide") win.hide();
  else if (command === "toggle") toggleVisible();
  else {
    win.showInactive();
    wake();
  }
}

// Stop the backend the launcher started (the launcher checks it is really Buddy's).
// It must run synchronously: Electron kills its child processes when it exits,
// even detached ones, so a background stop command would die with it.
function stopLauncherBackend() {
  if (!fs.existsSync(BACKEND_PID_FILE) || !fs.existsSync(LAUNCHER)) return;
  spawnSync(
    "powershell.exe",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", LAUNCHER, "stop-backend"],
    { stdio: "ignore", windowsHide: true, timeout: 15000 }
  );
}

// ---------- Window ----------
function createWindow() {
  win = new BrowserWindow({
    ...initialBounds(),
    transparent: true,
    frame: false,
    resizable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    icon: ICON,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // "screen-saver" level keeps Buddy above fullscreen-ish windows too.
  win.setAlwaysOnTop(true, "screen-saver");
  // Start click-through; the renderer turns it off while hovering controls.
  win.setIgnoreMouseEvents(true, { forward: true });
  win.loadFile(path.join(__dirname, "renderer", "index.html"));

  // Remember where you drag Buddy (not moves Buddy makes itself).
  win.on("moved", () => {
    if (movingByCode || docked) return;
    const { x, y } = win.getBounds();
    update({ position: { x, y } });
  });
  win.on("show", rebuildTrayMenu);
  win.on("hide", rebuildTrayMenu);
}

// Only one Buddy at a time. Starting it again passes its command (show by
// default) to the running one and exits.
if (!app.requestSingleInstanceLock({ command: cliCommand || "show" }) || cliCommand === "quit") {
  app.quit(); // "quit" with no Buddy running: nothing to do
} else {
  app.on("second-instance", (_event, _argv, _cwd, data) => runCommand(data?.command || "show"));

  app.whenReady().then(() => {
    settings = settingsStore.load();

    // Allow microphone access for the renderer.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      callback(permission === "media");
    });

    ipcMain.handle("get-avatar-url", () => findAvatarUrl());
    ipcMain.handle("get-settings", () => settings);
    ipcMain.handle("get-server-url", () => serverUrl());
    ipcMain.handle("set-connection", (_event, value) => setConnection(value));
    ipcMain.on("model-changed", () => rebuildTrayMenu()); // the backend saved the new model in .env
    ipcMain.on("set-click-through", (_event, ignore) => {
      if (win) win.setIgnoreMouseEvents(ignore, { forward: true });
    });
    ipcMain.on("set-docked", (_event, on) => setDocked(!!on));
    ipcMain.on("voices", (_event, names) => {
      voices = Array.isArray(names) ? names : [];
      rebuildTrayMenu();
    });
    ipcMain.on("resize-step", (_event, step) => {
      const order = Object.keys(SIZES);
      const i = Math.max(0, Math.min(order.length - 1, order.indexOf(settings.size) + Math.sign(step)));
      if (order[i] !== settings.size) update({ size: order[i] });
    });

    createWindow();
    if (cliCommand === "hide") win.once("ready-to-show", () => win.hide());

    tray = new Tray(ICON);
    tray.setToolTip("Buddy");
    tray.on("click", toggleVisible);
    rebuildTrayMenu();
    watchAvatars();
    pointerTimer = setInterval(pollPointer, 50);

    // Hold a key anywhere to talk (see ptt.js).
    ptt = createPushToTalk({
      onStart: () => {
        if (!win) return;
        if (!win.isVisible()) win.showInactive();
        win.webContents.send("ptt", true);
      },
      onStop: () => win && win.webContents.send("ptt", false),
    });
    ptt.setKey(settings.pttKey);
    rebuildTrayMenu();

    // Global hotkey toggles listening even when Buddy is not focused.
    globalShortcut.register("CommandOrControl+Shift+Space", () => {
      if (win) win.webContents.send("toggle-talk");
    });
  });

  app.on("will-quit", () => {
    clearInterval(pointerTimer); // the window is already destroyed
    avatarWatcher?.close();
    globalShortcut.unregisterAll();
    ptt?.stop();
    stopLauncherBackend();
  });
  app.on("window-all-closed", () => app.quit());
}
