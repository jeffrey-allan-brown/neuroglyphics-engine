#!/usr/bin/env python3
"""
GLYPHDECK — a visual console for the Neuroglyphics engine.

    cd ~/Documents/shared_drive/neuroglyphics
    python3 glyphdeck.py

Opens http://127.0.0.1:7373 in your browser. Buttons and forms build real
`neuroglyphics.py` invocations and run them under a pseudo-terminal, so the
engine behaves exactly as it does in your shell — colours, pauses, prompts
and all. The reveal ritual still flips one card at a time; you press the
button instead of Enter.

What this program does NOT do:

  * It never writes a card, a folio, the ledger, or state.json. Every
    mutation goes through the engine. This file only reads the vault to
    draw the dashboard, and pipes your keystrokes to a subprocess.
  * It never runs `init`. A stray init once created a nested vault under
    Guides/; the deck refuses to be the thing that does that again.
  * It never flips a glyph to forged on your behalf. No glyph without proof.

Pure stdlib. Localhost only. Ctrl-C to stop.
"""

import argparse
import errno
import importlib.util
import json
import os
import pty
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 7373


# ---------------------------------------------------------------- the vault

def find_vault(explicit=None):
    """Nearest enclosing vault wins, same rule the engine uses."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    candidates.append(HERE)
    candidates.append(Path.cwd().resolve())
    for start in candidates:
        for c in [start, *start.parents]:
            if (c / ".neuroglyphics" / "state.json").exists() and (c / "neuroglyphics.py").exists():
                return c
    return None


def load_engine(vault):
    """Import neuroglyphics.py as a module so the deck reuses its parsers.

    Importing only defines constants and functions — main() is guarded — so
    nothing runs and nothing is written.
    """
    spec = importlib.util.spec_from_file_location("neuroglyphics_engine", vault / "neuroglyphics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def nested_vault_warning(vault, ng):
    """The Guides/ accident: a second .neuroglyphics/ hiding under the real one."""
    stray = []
    for p in vault.rglob(".neuroglyphics"):
        if p.resolve() != (vault / ".neuroglyphics").resolve() and p.is_dir():
            stray.append(str(p.relative_to(vault)))
    return stray


# ---------------------------------------------------------------- read-only snapshot

def snapshot(vault, ng):
    """Everything the dashboard draws. Reads only — never writes.

    Deliberately does not call cmd_status: that one performs the fading
    sweep, which is a mutation. The same arithmetic is redone here without
    touching any card.
    """
    v = ng.Vault(vault)
    state = v.state()
    glyphs = v.glyphs()

    def days_since(iso):
        try:
            return (date.today() - date.fromisoformat(str(iso))).days
        except Exception:
            return 0

    revealed, queue = [], []
    finishes = {}
    fading, due = [], []

    for g in glyphs:
        item = {
            "glyph": g.get("glyph", ""),
            "name": g.get("name", ""),
            "constellation": g.get("constellation", ""),
            "type": g.get("type", ""),
            "grade": g.get("grade", ""),
            "proof": g.get("proof", ""),
            "finish": g.get("finish", ""),
            "sigil": g.get("sigil", ""),
            "status": g.get("status", ""),
            "stage": g.get("stage", ""),
            "lore": g.get("lore", ""),
            "assay": g.get("assay", ""),
        }
        if g.get("stage") == "revealed":
            revealed.append(item)
            f = g.get("finish", "")
            finishes[f] = finishes.get(f, 0) + 1
            overdue = days_since(g.get("last_fired")) - int(g.get("interval_days") or 0)
            item["overdue_days"] = overdue
            if g.get("status") == "fading":
                fading.append(item)
            elif g.get("status") == "bright":
                # The sweep the engine would perform, computed without writing.
                if days_since(g.get("last_fired")) > 2 * int(g.get("interval_days") or 0):
                    item["would_fade"] = True
                    fading.append(item)
                elif overdue > 0:
                    due.append(item)
        elif g.get("stage") == "forged":
            try:
                _, body = v.parse_note(g["_path"])
                item["assay_questions"] = ng.read_assay(body)
            except Exception:
                item["assay_questions"] = []
            queue.append(item)

    due.sort(key=lambda x: -x.get("overdue_days", 0))

    # Unforged node cards: the map, waiting. These populate the forge form.
    nodes = []
    if v.constellations.exists():
        for p in sorted(v.constellations.rglob("*.md")):
            fm, body = v.parse_note(p)
            if fm.get("glyph"):
                continue
            node = ng.parse_node_note(body)
            if not node.get("class"):
                continue  # a map document, not a node card
            binds = re.search(r"^\*\*binds:\*\*\s*(.+?)\s*$", body, re.M)
            nodes.append({
                "name": p.stem,
                "constellation": p.parent.name,
                "class": node.get("class", ""),
                "arm": node.get("arm", ""),
                "lore": node.get("lore", ""),
                "type": ng.CLASS_TO_TYPE.get(node.get("class", "").lower(), ""),
                "binds": binds.group(1) if binds else "",
                "has_guide": bool(re.search(r"^## Guide\s*$", body, re.M)),
            })

    wonder = []
    if v.wonder.exists():
        for line in v.wonder.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
            if m:
                wonder.append({"text": m.group(1), "struck": m.group(1).startswith("~~")})

    sealed = sorted(v.sealed_dir.glob("folio-*.ng")) if v.sealed_dir.exists() else []
    revealed_folios = sorted(v.revealed_dir.glob("folio-*.ng")) if v.revealed_dir.exists() else []

    return {
        "vault": str(vault),
        "season": state.get("season", ""),
        "glyph_counter": state.get("glyph_counter", 0),
        "folio_counter": state.get("folio_counter", 0),
        "gilt_tokens": state.get("gilt_tokens", 0),
        "kindled_days": ng.kindled_days(state),
        "sparked_today": ng.today() in state.get("sparks", []),
        "sparks": state.get("sparks", [])[-14:],
        "folios_since_etched": state.get("folios_since_etched", 0),
        "folios_since_illuminated": state.get("folios_since_illuminated", 0),
        "active_ciphers": state.get("active_ciphers", []),
        "revealed": revealed,
        "queue": queue,
        "finishes": finishes,
        "fading": fading,
        "due": due[:8],
        "bench": v.bench_questions(),
        "sealed_waiting": len(sealed),
        "sealed_names": [p.stem for p in sealed],
        "revealed_folios": len(revealed_folios),
        "constellations": sorted(v.mapped_constellations()),
        "nodes": nodes,
        "wonder": wonder,
        "stray_vaults": nested_vault_warning(vault, ng),
        "config": {
            "folio_size": ng.FOLIO_SIZE,
            "pity_etched_at": ng.PITY_ETCHED_AT,
            "pity_illum_at": ng.PITY_ILLUM_AT,
            "assay_pass": ng.ASSAY_PASS,
            "assay_excellence": ng.ASSAY_EXCELLENCE,
            "types": ng.TYPES,
            "grades": ng.GRADES,
            "proofs": ng.PROOFS,
            "finish_order": ng.FINISH_ORDER,
            "intervals": ng.INTERVALS,
        },
        "now": time.time(),
    }


# ---------------------------------------------------------------- command whitelist

def build_argv(payload, ng):
    """Turn a validated form payload into an argv list. Never a shell string.

    `init` is absent on purpose — see the module docstring.
    """
    cmd = payload.get("command")
    a = payload.get("args") or {}
    argv = []

    def text(key, limit=2000):
        val = a.get(key)
        if val is None:
            return None
        val = str(val).strip()
        if not val:
            return None
        if len(val) > limit:
            raise ValueError(f"{key} is too long")
        if "\x00" in val or "\n" in val:
            raise ValueError(f"{key} must be a single line")
        return val

    if cmd in ("status", "spark", "reveal"):
        argv = [cmd]

    elif cmd == "seal":
        argv = ["seal"]
        if a.get("now"):
            argv.append("--now")
        if a.get("gilded"):
            argv.append("--gilded")

    elif cmd == "token":
        argv = ["token"]
        reason = text("reason", 300)
        if reason:
            argv += ["--reason", reason]

    elif cmd == "wonder":
        item = text("item", 500)
        if not item:
            raise ValueError("The Wonder List needs something to wonder about.")
        argv = ["wonder", item]

    elif cmd == "assay":
        q = text("question", 1000)
        if not q:
            raise ValueError("An assay needs a question.")
        argv = ["assay", q]
        forcard = text("for", 200)
        if forcard:
            argv += ["--for", forcard]

    elif cmd == "restore":
        name = text("name", 200)
        if not name:
            raise ValueError("Which glyph?")
        argv = ["restore", name]

    elif cmd == "forge":
        name = text("name", 200)
        if not name:
            raise ValueError("A glyph needs a name.")
        argv = ["forge", "--name", name]
        constellation = text("constellation", 200)
        if constellation:
            argv += ["--constellation", constellation]
        if a.get("type"):
            if a["type"] not in ng.TYPES:
                raise ValueError(f"Unknown type {a['type']!r}")
            argv += ["--type", a["type"]]
        if a.get("grade"):
            if a["grade"] not in ng.GRADES:
                raise ValueError(f"Unknown grade {a['grade']!r}")
            argv += ["--grade", a["grade"]]
        proof = text("proof", 200)
        if proof:
            for part in [p.strip() for p in proof.split(",") if p.strip()]:
                if part not in ng.PROOFS:
                    raise ValueError(f"Unknown proof {part!r} — one of {', '.join(ng.PROOFS)}")
            argv += ["--proof", proof]
        lore = text("lore", 1000)
        if lore:
            argv += ["--lore", lore]
        quiz = a.get("quiz")
        if quiz not in (None, ""):
            q = int(quiz)
            if not 0 <= q <= 100:
                raise ValueError("Quiz score is a percentage.")
            argv += ["--quiz", str(q)]
        if a.get("branch"):
            argv.append("--branch")
        if a.get("capstone"):
            argv.append("--capstone")

    else:
        raise ValueError(f"{cmd!r} is not a ritual this deck performs.")

    return argv


# ---------------------------------------------------------------- pty sessions

class Session:
    """One engine invocation, running under a pty so the ritual keeps its manners."""

    def __init__(self, vault, argv):
        self.id = uuid.uuid4().hex[:12]
        self.argv = argv
        self.started = time.time()
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.exit_code = None

        self.master, slave = pty.openpty()
        env = dict(os.environ, TERM="xterm-256color", PYTHONUNBUFFERED="1", COLUMNS="86", LINES="40")
        self.proc = subprocess.Popen(
            [sys.executable, str(vault / "neuroglyphics.py"), *argv],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=str(vault), env=env, close_fds=True, start_new_session=True,
        )
        os.close(slave)
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        sel = selectors.DefaultSelector()
        sel.register(self.master, selectors.EVENT_READ)
        while True:
            for _ in sel.select(timeout=0.4):
                try:
                    chunk = os.read(self.master, 8192)
                except OSError as e:
                    chunk = b"" if e.errno in (errno.EIO, errno.EBADF) else b""
                if not chunk:
                    self._finish()
                    return
                with self.lock:
                    self.buf.extend(chunk)
            if self.proc.poll() is not None:
                # Drain whatever is still in the pipe, then stop.
                try:
                    while True:
                        chunk = os.read(self.master, 8192)
                        if not chunk:
                            break
                        with self.lock:
                            self.buf.extend(chunk)
                except OSError:
                    pass
                self._finish()
                return

    def _finish(self):
        try:
            self.exit_code = self.proc.wait(timeout=5)
        except Exception:
            self.exit_code = -1
        try:
            os.close(self.master)
        except OSError:
            pass
        sel_done = True  # noqa: F841

    def read_from(self, offset):
        with self.lock:
            data = bytes(self.buf[offset:])
            total = len(self.buf)
        return data.decode("utf-8", "replace"), total

    def send(self, text):
        if self.exit_code is not None:
            return False
        try:
            os.write(self.master, text.encode("utf-8"))
            return True
        except OSError:
            return False

    def kill(self):
        if self.exit_code is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass

    def info(self):
        return {
            "id": self.id,
            "argv": self.argv,
            "running": self.exit_code is None,
            "exit_code": self.exit_code,
            "started": self.started,
        }


SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def reap_sessions():
    with SESSIONS_LOCK:
        stale = [
            sid for sid, s in SESSIONS.items()
            if s.exit_code is not None and time.time() - s.started > 3600
        ]
        for sid in stale:
            SESSIONS.pop(sid, None)


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "Glyphdeck"
    vault = None
    ng = None

    def log_message(self, fmt, *args):
        pass  # the console is the UI; the terminal stays quiet

    # -- helpers

    def _guard(self):
        """Localhost only, and no cross-origin browser can talk to us."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
            self._json({"error": "glyphdeck serves localhost only"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
            self._json({"error": "cross-origin request refused"}, 403)
            return False
        return True

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # -- routes

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        query = dict(
            kv.split("=", 1) for kv in self.path.split("?")[1].split("&")
            if "=" in kv
        ) if "?" in self.path else {}

        if path in ("/", "/index.html"):
            page = HERE / "glyphdeck.html"
            if not page.exists():
                self.send_error(500, "glyphdeck.html is missing from the vault")
                return
            data = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/vault":
            try:
                self._json(snapshot(self.vault, self.ng))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return

        m = re.match(r"^/api/session/([0-9a-f]+)$", path)
        if m:
            with SESSIONS_LOCK:
                s = SESSIONS.get(m.group(1))
            if not s:
                self._json({"error": "no such session"}, 404)
                return
            offset = int(query.get("offset", 0))
            # Hold briefly for new output so the UI feels live without hammering.
            deadline = time.time() + 8
            while time.time() < deadline:
                text, total = s.read_from(offset)
                if text or s.exit_code is not None:
                    break
                time.sleep(0.12)
            else:
                text, total = s.read_from(offset)
            out = s.info()
            out["text"] = text
            out["offset"] = total
            self._json(out)
            return

        self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        try:
            payload = self._body()
        except Exception:
            self._json({"error": "bad JSON"}, 400)
            return

        if path == "/api/run":
            reap_sessions()
            try:
                argv = build_argv(payload, self.ng)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 400)
                return
            with SESSIONS_LOCK:
                live = [s for s in SESSIONS.values() if s.exit_code is None]
                if live:
                    self._json({
                        "error": "A ritual is already running. Finish or cancel it first.",
                        "session": live[0].info(),
                    }, 409)
                    return
                s = Session(self.vault, argv)
                SESSIONS[s.id] = s
            out = s.info()
            out["command_line"] = "python3 neuroglyphics.py " + " ".join(
                (f'"{x}"' if " " in x or '"' in x else x) for x in argv
            )
            self._json(out)
            return

        m = re.match(r"^/api/session/([0-9a-f]+)/(input|kill)$", path)
        if m:
            with SESSIONS_LOCK:
                s = SESSIONS.get(m.group(1))
            if not s:
                self._json({"error": "no such session"}, 404)
                return
            if m.group(2) == "kill":
                s.kill()
                self._json(s.info())
            else:
                text = payload.get("text", "")
                if len(text) > 4000:
                    self._json({"error": "too much input"}, 400)
                    return
                self._json({"sent": s.send(text), **s.info()})
            return

        self.send_error(404)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Glyphdeck — visual console for Neuroglyphics")
    ap.add_argument("--vault", help="vault root (default: this script's vault)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    vault = find_vault(args.vault)
    if vault is None:
        sys.exit(
            "No vault found. Put glyphdeck.py beside neuroglyphics.py in your "
            "vault, or pass --vault PATH."
        )
    if not (HERE / "glyphdeck.html").exists():
        sys.exit(f"glyphdeck.html must sit beside glyphdeck.py ({HERE}).")

    ng = load_engine(vault)
    Handler.vault = vault
    Handler.ng = ng

    stray = nested_vault_warning(vault, ng)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"

    print()
    print(f"  ◈ GLYPHDECK — {vault}")
    print(f"    {url}   (Ctrl-C to close the deck)")
    if stray:
        print(f"    ⚠ nested .neuroglyphics/ found at: {', '.join(stray)}")
    print("    The engine does the writing; this deck only presses its buttons.")
    print()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  The deck closes. The Codex remains.\n")
    finally:
        with SESSIONS_LOCK:
            for s in SESSIONS.values():
                s.kill()
        httpd.server_close()


if __name__ == "__main__":
    main()
