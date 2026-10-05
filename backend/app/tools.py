"""Tools Buddy can use on the PC, with approval guardrails and restricted folders.

Configured in backend/tools.json:
  - approval per tool: "auto" (runs without asking), "ask" (the user must tap
    Allow in the desktop or phone app), "off" (not offered to the model).
  - allowed_folders: the only folders the file tools can see or change.
    Paths are resolved (symlinks and ".." included) before the check.

Every action that changes something defaults to "ask". run_command runs a
PowerShell command in the first allowed folder with a time limit. Results are
cut to MAX_RESULT_CHARS so they fit a small local model's context.
"""

from __future__ import annotations

import datetime
import difflib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import asyncio

log = logging.getLogger("buddy.tools")

TOOLS_FILE = Path(__file__).resolve().parent.parent / "tools.json"
MAX_RESULT_CHARS = 1500
MAX_READ_BYTES = 1_000_000
CREATE_NO_WINDOW = 0x08000000  # Windows: don't flash a console for child processes

DEFAULT_CONFIG = {
    "enabled": True,
    "allowed_folders": ["~/Documents/Buddy"],
    "approval": {},
    "approval_timeout_seconds": 60,
    "command_timeout_seconds": 30,
}
DEFAULT_APPROVAL = {
    "get_time": "auto",
    "list_folder": "auto",
    "read_file": "auto",
    "open_file": "ask",
    "write_file": "ask",
    "open_app": "ask",
    "open_url": "ask",
    "search_web": "ask",
    "run_command": "ask",
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


# Receives {"tool", "title", "detail"} and returns True if the user allowed it.
Approver = Callable[[dict], Awaitable[bool]]


async def deny_all(_request: dict) -> bool:
    return False


class ToolError(Exception):
    """A tool failed; the message goes back to the model."""


def _clip(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... (cut, {len(text)} characters in total)"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _powershell(script: str, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run PowerShell without a window. Output is forced to UTF-8 (no BOM) and decoded
    as UTF-8: by default PowerShell writes the console code page, which garbles or
    loses accented and non-Latin text (file names, app names)."""
    utf8 = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", utf8 + script],
        cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    done.stdout = (done.stdout or "").lstrip("﻿")
    done.stderr = (done.stderr or "").lstrip("﻿")
    return done


class Toolbox:
    def __init__(self, path: Path = TOOLS_FILE) -> None:
        config = dict(DEFAULT_CONFIG)
        try:
            config.update(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.error("Could not read %s (%s); tools are off until it's fixed", path.name, e)
            config["enabled"] = False
        self.enabled = bool(config.get("enabled", True))
        self.approval = {**DEFAULT_APPROVAL, **config.get("approval", {})}
        self.approval_timeout = float(config.get("approval_timeout_seconds", 60))
        self.command_timeout = float(config.get("command_timeout_seconds", 30))
        self.folders = [Path(os.path.expanduser(f)).resolve() for f in config.get("allowed_folders", [])]
        if self.enabled and self.folders and not self.folders[0].exists():
            self.folders[0].mkdir(parents=True, exist_ok=True)  # Buddy's own workspace
        self._apps: list[tuple[str, str]] = []
        self._apps_at = 0.0
        self._specs = self._build_specs()
        if self.enabled:
            offered = ", ".join(self.offered()) or "none"
            log.info("Tools on: %s. Folders: %s", offered, ", ".join(map(str, self.folders)) or "none")
        if "open_app" in self.offered():
            threading.Thread(target=self._preload_apps, name="start-apps", daemon=True).start()

    def _preload_apps(self) -> None:
        try:
            self._start_apps()
        except Exception as e:  # noqa: BLE001 - tried again when an app is opened
            log.warning("Could not list Start menu apps yet: %s", e)

    # ---------- what the model sees ----------

    def offered(self) -> list[str]:
        if not self.enabled:
            return []
        return [name for name in self._specs if self.approval.get(name, "ask") != "off"]

    def schemas(self) -> list[dict]:
        """Provider-neutral tool definitions: name, description, parameters (JSON Schema)."""
        return [
            {"name": name, "description": self._specs[name]["description"], "parameters": self._specs[name]["parameters"]}
            for name in self.offered()
        ]

    def prompt(self) -> str:
        """Instructions added to the system prompt when tools are on."""
        if not self.offered():
            return ""
        folders = "; ".join(str(f) for f in self.folders) or "none"
        return (
            "You can act on the user's Windows PC with tools. When the user asks you to do something "
            "on the PC, or asks about the time, the date or their files, call the matching tool "
            "instead of answering from memory. Never guess the time, the date or what a file says. "
            "Call the tool in the same reply; don't just say you will. "
            "Some actions need the user's approval in the app. If one is denied, accept it and don't "
            f"try again. Files can only be read or changed in these folders: {folders}. Only say "
            "you did something if the tool result says it worked."
        )

    # ---------- running ----------

    async def run(self, call: ToolCall, approve: Approver) -> tuple[str, bool, str]:
        """Run one tool call. Returns (result for the model, is_error, title for the user)."""
        spec = self._specs.get(call.name)
        if spec is None or call.name not in self.offered():
            return f"There is no tool called {call.name}.", True, call.name
        args = call.arguments if isinstance(call.arguments, dict) else {}
        missing = [p for p in spec["parameters"].get("required", []) if not str(args.get(p, "")).strip()]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}.", True, call.name
        title = call.name
        try:
            # Before running: "Create x.txt", not "Replace". In a thread: describing an
            # app looks it up in the Start menu (PowerShell), which can take seconds.
            title, detail = await asyncio.to_thread(spec["describe"], args)
            if self.approval.get(call.name) == "ask":
                if not await approve({"tool": call.name, "title": title, "detail": detail}):
                    log.info("denied: %s", title)
                    return "The user didn't allow this, so it was not done.", True, title
            log.info("running: %s", title)
            result = await asyncio.to_thread(spec["run"], args)
            return _clip(result), False, title
        except ToolError as e:
            return str(e), True, title
        except Exception as e:  # noqa: BLE001 - report any failure to the model
            log.exception("tool %s failed", call.name)
            return f"{call.name} failed: {e}", True, title

    # ---------- folders ----------

    def _resolve(self, path: str) -> Path:
        """A path inside the allowed folders, or ToolError."""
        if not self.folders:
            raise ToolError("No folders are allowed. Add some to allowed_folders in tools.json.")
        raw = Path(os.path.expanduser(path.strip().strip('"'))) if path.strip() else self.folders[0]
        full = (raw if raw.is_absolute() else self.folders[0] / raw).resolve()
        for root in self.folders:
            if full == root or root in full.parents:
                return full
        allowed = "; ".join(str(f) for f in self.folders)
        raise ToolError(f"{full} is outside the folders Buddy may use ({allowed}).")

    # ---------- tool definitions ----------

    def _build_specs(self) -> dict:
        def obj(properties: dict, required: list[str]) -> dict:
            return {"type": "object", "properties": properties, "required": required}

        path_prop = {"type": "string", "description": "File or folder path, absolute or relative to Buddy's folder"}
        return {
            "get_time": {
                "description": "Get the current local date and time. Always use this; you don't know the time or date otherwise.",
                "parameters": obj({}, []),
                "describe": lambda a: ("Check the time", ""),
                # "Monday, 5 October 2026, 08:40": no leading zero, which a small model misread.
                "run": lambda a: (lambda n: f"{n:%A}, {n.day} {n:%B %Y}, {n:%H:%M}")(datetime.datetime.now()),
            },
            "list_folder": {
                "description": "List the files in one of the allowed folders. An empty path lists Buddy's folder.",
                "parameters": obj({"path": path_prop}, []),
                "describe": lambda a: (f"List {a.get('path') or 'Buddy folder'}", ""),
                "run": self._list_folder,
            },
            "read_file": {
                "description": "Read a text file in an allowed folder.",
                "parameters": obj({"path": path_prop}, ["path"]),
                "describe": lambda a: (f"Read {a.get('path')}", ""),
                "run": self._read_file,
            },
            "write_file": {
                "description": "Create or change a text file in an allowed folder.",
                "parameters": obj(
                    {
                        "path": path_prop,
                        "content": {"type": "string", "description": "The text to write"},
                        "append": {"type": "boolean", "description": "Add to the end instead of replacing"},
                    },
                    ["path", "content"],
                ),
                "describe": self._describe_write,
                "run": self._write_file,
            },
            "open_file": {
                "description": "Open a file or folder from an allowed folder with its usual app.",
                "parameters": obj({"path": path_prop}, ["path"]),
                "describe": lambda a: (f"Open {Path(str(a.get('path'))).name or a.get('path')}", str(self._resolve(str(a.get("path", ""))))),
                "run": self._open_file,
            },
            "open_app": {
                "description": "Open an installed app by name, for example Notepad, Calculator, Spotify or Chrome.",
                "parameters": obj({"name": {"type": "string", "description": "The app's name"}}, ["name"]),
                "describe": self._describe_app,
                "run": self._open_app,
            },
            "open_url": {
                "description": "Open a web page in the browser.",
                "parameters": obj({"url": {"type": "string", "description": "An http or https address"}}, ["url"]),
                "describe": lambda a: ("Open a web page", str(a.get("url"))),
                "run": self._open_url,
            },
            "search_web": {
                "description": "Search the web in the browser.",
                "parameters": obj({"query": {"type": "string", "description": "What to search for"}}, ["query"]),
                "describe": lambda a: (f"Search the web for \"{a.get('query')}\"", ""),
                "run": self._search_web,
            },
            "run_command": {
                "description": "Run a PowerShell command on the PC and get its output. Use only when no other tool fits.",
                "parameters": obj({"command": {"type": "string", "description": "The PowerShell command"}}, ["command"]),
                "describe": lambda a: ("Run a PowerShell command", str(a.get("command"))),
                "run": self._run_command,
            },
        }

    # ---------- implementations (blocking; called in a thread) ----------

    def _list_folder(self, args: dict) -> str:
        if not str(args.get("path", "")).strip() and len(self.folders) > 1:
            return "Allowed folders:\n" + "\n".join(str(f) for f in self.folders)
        folder = self._resolve(str(args.get("path", "")))
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder.")
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:60]:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                lines.append(f"{entry.name} ({max(1, entry.stat().st_size // 1024)} KB)")
        more = f"\n... and {len(entries) - 60} more" if len(entries) > 60 else ""
        return f"{folder}:\n" + ("\n".join(lines) if lines else "(empty)") + more

    def _read_file(self, args: dict) -> str:
        path = self._resolve(str(args["path"]))
        if not path.is_file():
            raise ToolError(f"{path} is not a file.")
        if path.stat().st_size > MAX_READ_BYTES:
            raise ToolError(f"{path.name} is too big to read ({path.stat().st_size // 1024} KB).")
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            raise ToolError(f"{path.name} isn't a text file.")
        return data.decode("utf-8", errors="replace")

    def _describe_write(self, args: dict) -> tuple[str, str]:
        path = self._resolve(str(args.get("path", "")))
        action = "Add to" if args.get("append") else ("Replace" if path.exists() else "Create")
        preview = str(args.get("content", ""))[:300]
        return f"{action} {path.name}", f"{path}\n\n{preview}"

    def _write_file(self, args: dict) -> str:
        path = self._resolve(str(args["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        with path.open("a" if args.get("append") else "w", encoding="utf-8") as f:
            f.write(content)
        return f"Saved {path} ({len(content)} characters)."

    def _open_file(self, args: dict) -> str:
        path = self._resolve(str(args["path"]))
        if not path.exists():
            raise ToolError(f"{path} doesn't exist.")
        os.startfile(path)  # noqa: S606 - Windows: open with the default app
        return f"Opened {path}."

    def _start_apps(self) -> list[tuple[str, str]]:
        """(name, AppID) of Start menu apps, from Get-StartApps (cached for 10 minutes)."""
        if self._apps and time.monotonic() - self._apps_at < 600:
            return self._apps
        # Slow on a busy PC (over 20 s seen), so the toolbox starts loading it at startup.
        out = _powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=60).stdout
        items = json.loads(out) if out.strip() else []
        if isinstance(items, dict):
            items = [items]
        self._apps = [(i["Name"], i["AppID"]) for i in items if i.get("Name") and i.get("AppID")]
        self._apps_at = time.monotonic()
        return self._apps

    def _find_app(self, name: str) -> tuple[str, str]:
        wanted = _norm(name)
        if not wanted:
            raise ToolError("Which app?")
        best, best_score = None, 0.0
        for app_name, app_id in self._start_apps():
            have = _norm(app_name)
            if have == wanted:
                score = 3.0
            elif have.startswith(wanted):
                score = 2.0
            elif re.search(rf"\b{re.escape(wanted)}\b", have):
                score = 1.5
            else:
                score = difflib.SequenceMatcher(None, wanted, have).ratio()
                score = score if score >= 0.75 else 0.0
            if score > best_score or (score == best_score and best and len(app_name) < len(best[0])):
                best, best_score = (app_name, app_id), score
        if best is None:
            names = [n for n, _ in self._apps]
            close = difflib.get_close_matches(name, names, n=4, cutoff=0.4)
            hint = f" Similar: {', '.join(close)}." if close else ""
            raise ToolError(f"No app called {name} is installed.{hint}")
        return best

    def _describe_app(self, args: dict) -> tuple[str, str]:
        try:
            app_name, _ = self._find_app(str(args.get("name", "")))
        except ToolError:
            app_name = str(args.get("name", "an app"))
        return f"Open {app_name}", ""

    def _open_app(self, args: dict) -> str:
        app_name, app_id = self._find_app(str(args["name"]))
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=CREATE_NO_WINDOW)
        return f"Opened {app_name}."

    def _open_url(self, args: dict) -> str:
        url = str(args["url"]).strip()
        if not re.match(r"^https?://", url, re.I):
            if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", url, re.I):
                url = "https://" + url
            else:
                raise ToolError("Only http and https web addresses can be opened.")
        webbrowser.open(url)
        return f"Opened {url} in the browser."

    def _search_web(self, args: dict) -> str:
        query = str(args["query"]).strip()
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
        return f"Searched the web for {query}."

    def _run_command(self, args: dict) -> str:
        if not self.folders:
            raise ToolError("No folders are allowed, so commands can't run.")
        try:
            done = _powershell(str(args["command"]), timeout=self.command_timeout, cwd=self.folders[0])
        except subprocess.TimeoutExpired:
            raise ToolError(f"The command took longer than {self.command_timeout:g} s and was stopped.") from None
        output = (done.stdout or "") + (f"\n[errors]\n{done.stderr}" if done.stderr.strip() else "")
        return f"Exit code {done.returncode}.\n{output.strip() or '(no output)'}"
