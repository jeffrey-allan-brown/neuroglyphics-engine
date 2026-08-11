#!/usr/bin/env python3
"""
NEUROGLYPHICS - Phase 0 engine.

Forge glyphs, seal folios, reveal them. Pure Python stdlib; no installs.
Run from your vault root, or pass --vault PATH.

Commands:
  init      scaffold a new vault
  assay     harvest an exam question while studying (before or after forging)
  forge     create a glyph (auto-seals a folio on every 3rd forge)
  seal      manually seal (used rarely; forge normally triggers it)
  reveal    open the oldest sealed folio, one card at a time
  status    codex stats, pity counters, tokens, fading queue
  spark     log a study session today (fuels Kindled Week)
  token     bank a Gilt Token (completed a branch, earned a Seal)
  restore   recall-check a faded glyph; Keystones+ return as Palimpsest
  wonder    add an item to the Wonder List
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------- constants

SEASON_DEFAULT = "S1"
FOLIO_SIZE = 3  # fresh glyphs per folio

TYPES = ["Concept", "Technique", "Figure", "Work", "Event", "Instrument"]
GRADES = ["Node", "Branch", "Keystone", "Capstone"]
PROOFS = ["Recall", "Artifact", "Exposition", "Practice"]

FINISH_ORDER = ["Matte", "Gilt", "Etched", "Aurora", "Illuminated"]
TABLE_STANDARD = [(68, "Matte"), (20, "Gilt"), (8, "Etched"), (3, "Aurora"), (1, "Illuminated")]
TABLE_GILDED = [(24, "Matte"), (40, "Gilt"), (24, "Etched"), (9, "Aurora"), (3, "Illuminated")]
TABLE_PITY_ETCHED = [(75, "Etched"), (20, "Aurora"), (5, "Illuminated")]

PITY_ETCHED_AT = 10   # folios without an Etched+ before guarantee
PITY_ILLUM_AT = 25    # folios without an Illuminated before guarantee

INTERVALS = [14, 30, 90, 365]  # days; past the last one a glyph goes Fast
ECHO_CAP = "Aurora"            # echo upgrades stop here

ASSAY_PASS = 70        # below this, the glyph enters the Codex already fading
ASSAY_EXCELLENCE = 90  # at or above, banks a Gilt Token for the next folio

C = {
    "Matte": "",
    "Gilt": "\033[33m",
    "Etched": "\033[36m",
    "Aurora": "\033[35m",
    "Illuminated": "\033[1;93m",
    "Palimpsest": "\033[2;37m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

FM_ORDER = [
    "glyph", "name", "constellation", "type", "grade", "proof", "forged",
    "stage", "finish", "first_edition", "cipher_decode", "quiz_excellence",
    "assay", "sigil", "last_fired", "interval_days", "status", "lore",
]

NOTE_BODY = """
# {name}

*Notes, proof links, sources.*

## Assay

*Questions harvested while studying. Sealed at forge; they open cold on Reveal Day.*
"""

COVENANT = """# The Honest Forge Covenant

1. **No Glyph without proof.** Ever.
2. **Rolls are rolled once.** Finishes are committed at seal time. No re-rolls, no peeking.
3. **Fading is real.** No freezing timers, no grace periods beyond the written rule.
4. **Rules may soften for next season, never retroactively.**

Signed: ______________________   Date: ______________
"""

WONDER_SEED = """# Wonder List

Everything you're curious about. One per line. Ciphers are drawn from here.

Costs nothing to add. Nothing here is a to-do — undecoded curiosities return to the
pool at season's end, and that is the system working, not you falling behind.

- How mirrors are made
- Why sourdough works
"""


# ---------------------------------------------------------------- utilities

def is_tty():
    return sys.stdout.isatty() and sys.stdin.isatty()


def cprint(text="", color=""):
    if color and is_tty():
        print(f"{C.get(color, '')}{text}{C['reset']}")
    else:
        print(text)


def pause(seconds):
    if is_tty():
        time.sleep(seconds)


def wait_enter(prompt):
    try:
        input(prompt if is_tty() else "")
    except EOFError:
        pass


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or (default or "")


def ask_choice(prompt, options, default=1):
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = ask(prompt, str(default))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw in options:
            return raw
        print("    ?")


def today():
    return date.today().isoformat()


def sigil_for(constellation, name):
    """Deterministic braille sigil: same knowledge, same mark, forever."""
    h = hashlib.sha256(f"{constellation}/{name}".lower().encode()).digest()
    return "".join(chr(0x2800 + b) for b in h[:4])


def roll(table, rng):
    n = rng.randint(1, sum(w for w, _ in table))
    acc = 0
    for weight, finish in table:
        acc += weight
        if n <= acc:
            return finish
    return table[-1][1]


def roll_card(table, times, rng):
    results = [roll(table, rng) for _ in range(times)]
    return max(results, key=FINISH_ORDER.index)


def keystream_xor(data: bytes, key: str) -> bytes:
    out = bytearray()
    i, ctr = 0, 0
    while i < len(data):
        block = hashlib.sha256(f"{key}:{ctr}".encode()).digest()
        for b in block:
            if i >= len(data):
                break
            out.append(data[i] ^ b)
            i += 1
        ctr += 1
    return bytes(out)


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "-", name).strip()


LEGACY_STATUS = {"lit": "bright"}  # renamed Aug 2026; old vaults still read fine

# ---- node notes (hand-written, one per node, inside Constellations/<name>/)

CLASS_TO_TYPE = {
    "concept": "Concept", "technique": "Technique", "instrument": "Instrument",
    "figure": "Figure", "work": "Work", "event": "Event",
}


def parse_node_note(body):
    """Pull class / arm / lore out of a hand-written node note.

    Recognises the conventions used in the map notes:
        > [!abstract] instrument
        > The layer that argues with the bones...
        **class:** instrument
        **arm:** II. The Triad
    """
    out = {}
    for field in ("class", "arm", "status"):
        m = re.search(rf"^\*\*{field}:\*\*\s*(.+?)\s*$", body, re.M)
        if m:
            out[field] = m.group(1).strip()

    m = re.search(r"^>\s*\[!\w+\][^\n]*\n((?:^>.*\n?)+)", body, re.M)
    if m:
        lines = [ln.lstrip(">").strip() for ln in m.group(1).splitlines()]
        lore = " ".join(ln for ln in lines if ln)
        if lore:
            out["lore"] = lore
    return out


def mark_node_forged(body):
    """Flip the unforged markers a node note carries before it becomes a card."""
    body = re.sub(r"^#unforged[ \t]*$", "#forged", body, flags=re.M)
    body = re.sub(r"^(\*\*status:\*\*)[ \t]*unforged[ \t]*$", r"\1 forged", body, flags=re.M)
    return body


# ---- assay section handling (questions live in the note until seal strips them)

def assay_bounds(body):
    """Return (start_of_content, end_of_section) for '## Assay', or None."""
    m = re.search(r"^## Assay\s*$", body, re.M)
    if not m:
        return None
    rest = body[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return m.end(), m.end() + (nxt.start() if nxt else len(rest))


def read_assay(body):
    b = assay_bounds(body)
    if not b:
        return []
    out = []
    for line in body[b[0]:b[1]].splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if m and not m.group(1).startswith("*"):
            out.append(m.group(1))
    return out


def append_assay(body, question):
    b = assay_bounds(body)
    if not b:
        return body.rstrip() + f"\n\n## Assay\n\n- {question}\n"
    section = body[b[0]:b[1]].rstrip()
    return body[:b[0]] + section + f"\n- {question}\n\n" + body[b[1]:]


def strip_assay(body, n):
    b = assay_bounds(body)
    if not b:
        return body
    if n == 0:
        note = "*Forged unexamined — no questions were harvested.*"
    elif n == 1:
        note = "*1 question sealed. It opens cold on Reveal Day.*"
    else:
        note = f"*{n} questions sealed. They open cold on Reveal Day.*"
    return body[:b[0]] + f"\n\n{note}\n\n" + body[b[1]:]


# ---------------------------------------------------------------- vault

class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.codex = root / "Codex"
        self.constellations = root / "Constellations"
        self.wonder = root / "Wonder List.md"
        self.meta = root / ".neuroglyphics"
        self.sealed_dir = self.meta / "sealed"
        self.revealed_dir = self.meta / "revealed"
        self.ledger = self.meta / "ledger.md"
        self.state_file = self.meta / "state.json"
        self.bench = self.meta / "bench.json"

    def bench_questions(self):
        if self.bench.exists():
            return json.loads(self.bench.read_text(encoding="utf-8"))
        return []

    def save_bench(self, items):
        self.bench.write_text(json.dumps(items, indent=2), encoding="utf-8")

    @staticmethod
    def find_root(start=None):
        """Walk up from `start` looking for a vault, the way git finds .git/."""
        p = (start or Path.cwd()).resolve()
        for candidate in [p, *p.parents]:
            if (candidate / ".neuroglyphics" / "state.json").exists():
                return candidate
        return None

    @staticmethod
    def locate(path_arg=None):
        """Nearest enclosing vault wins; --vault is the fallback when you're outside one.

        This ordering is deliberate: a pinned --vault in your shell alias should be a
        default, not an override, or standing inside a vault would never work.
        """
        root = Vault.find_root()
        if root is None and path_arg:
            candidate = Path(path_arg).expanduser()
            if (candidate / ".neuroglyphics" / "state.json").exists():
                root = candidate
        if root is None:
            where = Path.cwd()
            sys.exit(
                f"No vault here, and none above {where}.\n"
                f"Run `init` to scaffold one in the current directory, "
                f"or cd into an existing vault."
            )
        return Vault(root)

    def init(self):
        for d in (self.codex, self.constellations, self.sealed_dir, self.revealed_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not (self.root / "Covenant.md").exists():
            (self.root / "Covenant.md").write_text(COVENANT, encoding="utf-8")
        if not self.wonder.exists():
            self.wonder.write_text(WONDER_SEED, encoding="utf-8")
        if not self.ledger.exists():
            self.ledger.write_text("# Folio Ledger\n\n", encoding="utf-8")
        if not self.state_file.exists():
            state = {
                "season": SEASON_DEFAULT,
                "glyph_counter": 0,
                "folio_counter": 0,
                "gilt_tokens": 0,
                "folios_since_etched": 0,
                "folios_since_illuminated": 0,
                "active_ciphers": [],
                "sparks": [],
                "salt": hashlib.sha256(str(random.random()).encode()).hexdigest()[:16],
            }
            self.save_state(state)
        cprint(f"Vault ready at {self.root}", "bold")
        print("  Sign the Covenant, sketch a Constellation, feed the Wonder List.")
        print("  Then: forge your first glyph.")

    def state(self):
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state):
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # ---- notes

    def parse_note(self, path):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        fm = {}
        body = text
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip('"')
        if fm.get("status") in LEGACY_STATUS:
            fm["status"] = LEGACY_STATUS[fm["status"]]
        fm["_path"] = path
        return fm, body

    def write_note(self, path, fm, body=""):
        lines = ["---"]
        for key in FM_ORDER:
            if key in fm:
                val = str(fm[key])
                if key in ("lore", "name") or ":" in val:
                    val = f'"{val}"'
                lines.append(f"{key}: {val}")
        lines.append("---")
        path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")

    def glyph_files(self):
        """Every file that could hold a glyph.

        Cards live in place, inside Constellations/<constellation>/, so the map
        stays wired in Obsidian's graph. Codex/ is still scanned for cards forged
        before that change.
        """
        files = []
        if self.constellations.exists():
            files.extend(sorted(self.constellations.rglob("*.md")))
        if self.codex.exists():
            files.extend(sorted(self.codex.glob("*.md")))
        return files

    def glyphs(self):
        out = []
        for p in self.glyph_files():
            fm, _ = self.parse_note(p)
            if fm.get("glyph"):
                out.append(fm)
        return out

    def find_node_note(self, name):
        """Locate a hand-written node note by filename, case-insensitively."""
        if not self.constellations.exists():
            return None
        for p in sorted(self.constellations.rglob("*.md")):
            if p.stem.lower() == name.lower():
                return p
        return None

    def mapped_constellations(self):
        """Constellation names taken from folders under Constellations/."""
        if not self.constellations.exists():
            return set()
        return {d.name for d in self.constellations.iterdir() if d.is_dir()}

    def card_path(self, constellation, name):
        return self.constellations / constellation / f"{safe_filename(name)}.md"

    def find_glyph(self, name):
        for fm in self.glyphs():
            if fm["name"].lower() == name.lower() or fm["glyph"].lower() == name.lower():
                return fm
        return None

    def wonder_items(self, exclude_active=None):
        exclude = set(exclude_active or [])
        items = []
        if self.wonder.exists():
            for line in self.wonder.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*[-*]\s+(?!~~)(.+?)\s*$", line)
                if m and m.group(1) not in exclude:
                    items.append(m.group(1))
        return items

    def strike_wonder(self, item):
        if not self.wonder.exists():
            return
        lines = self.wonder.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines:
            m = re.match(r"^(\s*[-*]\s+)(.+?)\s*$", line)
            if m and m.group(2) == item:
                out.append(f"{m.group(1)}~~{item}~~ ✦ decoded {today()}")
            else:
                out.append(line)
        self.wonder.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- commands

def cmd_init(args):
    """Always scaffolds where you're standing (or at an explicit path).

    Deliberately ignores --vault: a pinned alias must never silently re-init some
    other directory when you meant "here".
    """
    target = Path(args.path).expanduser().resolve() if args.path else Path.cwd()

    existing = Vault.find_root(target)
    if existing and existing != target:
        cprint(f"  ⚠ {target} sits inside an existing vault at {existing}.", "bold")
        print("    Nesting vaults will confuse every command that walks upward.")
        if not ask("    Scaffold here anyway? (y/n)", "n").lower().startswith("y"):
            sys.exit("  Nothing created.")

    target.mkdir(parents=True, exist_ok=True)
    Vault(target).init()


def cmd_spark(args):
    v = Vault.locate(args.vault)
    state = v.state()
    if today() not in state["sparks"]:
        state["sparks"].append(today())
        state["sparks"] = state["sparks"][-60:]
        v.save_state(state)
        n = kindled_days(state)
        cprint(f"✦ Spark logged. {n}/5 days this week toward a Kindled Week.", "Gilt")
    else:
        print("Already sparked today. Go study anyway.")


def kindled_days(state):
    cutoff = date.today() - timedelta(days=6)
    return len({d for d in state["sparks"] if date.fromisoformat(d) >= cutoff})


def cmd_token(args):
    v = Vault.locate(args.vault)
    state = v.state()
    state["gilt_tokens"] += 1
    v.save_state(state)
    reason = args.reason or "unspecified"
    cprint(f"◈ Gilt Token banked ({reason}). Tokens: {state['gilt_tokens']}", "Gilt")


def cmd_assay(args):
    v = Vault.locate(args.vault)
    question = args.question or ask("Question (something you couldn't answer an hour ago)")
    if not question:
        return

    if args.forcard:
        fm = v.find_glyph(args.forcard)
        if not fm:
            sys.exit(f"No glyph named {args.forcard!r}.")
        if fm["stage"] != "forged":
            sys.exit(
                f"{fm['name']} is already {fm['stage']} — its assay is sealed. "
                "Harvest for the next glyph instead."
            )
        path = fm["_path"]
        fm2 = dict(fm)
        fm2.pop("_path")
        _, body = v.parse_note(path)
        v.write_note(path, fm2, append_assay(body, question))
        n = len(read_assay(append_assay(body, question)))
        cprint(f"  ⚖ Harvested onto {fm['name']} ({n} question{'s' if n != 1 else ''}).", "Etched")
        return

    items = v.bench_questions()
    items.append(question)
    v.save_bench(items)
    cprint(f"  ⚖ On the bench ({len(items)}). Attaches to your next forge.", "Etched")


def cmd_wonder(args):
    v = Vault.locate(args.vault)
    item = args.item or ask("What are you curious about?")
    if not item:
        return
    with v.wonder.open("a", encoding="utf-8") as f:
        f.write(f"- {item}\n")
    print(f"Added to the Wonder List: {item}")


def cmd_forge(args):
    v = Vault.locate(args.vault)
    state = v.state()
    interactive = not args.name

    print()
    cprint("⚒  THE FORGE", "bold")
    name = args.name or ask("Name of the glyph")
    if not name:
        sys.exit("A glyph needs a name.")

    existing = {g["constellation"] for g in v.glyphs()}

    # Does a hand-written node note already describe this glyph?
    node_path = v.find_node_note(name)
    node = {}
    if node_path:
        _, node_body_raw = v.parse_note(node_path)
        node = parse_node_note(node_body_raw)
        name = node_path.stem                       # adopt the map's capitalisation
        node_constellation = node_path.parent.name
        cprint(f"  ✦ On the map — {node_constellation} · {node.get('arm', 'no arm')}", "Etched")
    else:
        node_constellation = None
        if interactive:
            cprint("  ○ Not on any map. Forging unmapped.", "dim")

    choices = sorted(v.mapped_constellations() | existing)
    if args.constellation:
        constellation = args.constellation
    elif node_constellation:
        constellation = node_constellation
    elif interactive and choices:
        constellation = ask(f"Constellation ({', '.join(choices)} or new)")
    else:
        constellation = ask("Constellation") if interactive else "Unsorted"

    mapped_type = CLASS_TO_TYPE.get(node.get("class", "").lower())
    if args.type:
        ctype = args.type
    elif mapped_type:
        ctype = mapped_type
    elif interactive:
        ctype = ask_choice("Type", TYPES)
    else:
        ctype = "Concept"

    if args.grade:
        grade = args.grade
    elif interactive:
        grade = ask_choice("Grade", GRADES)
    else:
        grade = "Node"

    if args.proof:
        proof = args.proof
    elif interactive:
        print(f"    ({', '.join(PROOFS)})")
        proof = ask("Proof(s), comma-separated", "Recall")
    else:
        proof = "Recall"

    if args.lore is not None:
        lore = args.lore
    elif node.get("lore"):
        lore = node["lore"]                          # already written on the map
        if interactive:
            cprint(f"  ✦ Lore from the map: \"{lore[:60]}…\"", "dim")
    elif interactive:
        lore = ask("Lore line (one sentence, in the card's voice)")
    else:
        lore = ""

    quiz = args.quiz
    if quiz is None and interactive and "Recall" in proof:
        raw = ask("Quiz score % (blank if none)")
        quiz = int(raw) if raw.isdigit() else None
    excellence = quiz is not None and quiz >= 90

    # cipher decode?
    decode_item = None
    active = state["active_ciphers"]
    if args.decode:
        matches = [c for c in active if args.decode.lower() in c.lower()]
        decode_item = matches[0] if matches else None
    elif interactive and active:
        print("    Active ciphers:")
        for i, c in enumerate(active, 1):
            print(f"      {i}. {c}")
        raw = ask("Does this glyph decode one? (number or blank)")
        if raw.isdigit() and 1 <= int(raw) <= len(active):
            decode_item = active[int(raw) - 1]

    # Adopt an existing note rather than overwrite it.
    note_path = node_path or v.card_path(constellation, name)
    existing_fm = existing_body = None
    if note_path.exists():
        existing_fm, existing_body = v.parse_note(note_path)
        stage = existing_fm.get("stage", "")
        if stage in ("sealed", "revealed"):
            sys.exit(
                f"'{name}' is already {stage} — it's a card, not a draft.\n"
                f"Re-forging would duplicate it. Edit the note directly, or "
                f"forge under a different name."
            )
        cprint(f"  ↩ Adopting existing note — your writing is preserved.", "Etched")

    if existing_fm is not None and str(existing_fm.get("glyph", "")).strip():
        gid = existing_fm["glyph"]          # keep the ID, don't burn a counter slot
    else:
        state["glyph_counter"] += 1
        gid = f"{state['season']}-{state['glyph_counter']:03d}"
    first_edition = constellation not in existing

    fm = {
        "glyph": gid,
        "name": name,
        "constellation": constellation,
        "type": ctype,
        "grade": grade,
        "proof": proof,
        "forged": today(),
        "stage": "forged",
        "finish": "unrolled",
        "first_edition": str(first_edition).lower(),
        "cipher_decode": str(bool(decode_item)).lower(),
        "quiz_excellence": str(excellence).lower(),
        "sigil": sigil_for(constellation, name),
        "last_fired": today(),
        "interval_days": INTERVALS[0],
        "status": "bright",
        "lore": lore,
    }
    path = note_path
    path.parent.mkdir(parents=True, exist_ok=True)

    # An existing note is the user's writing. Never clobber it — adopt it.
    if existing_fm is not None:
        for key, value in existing_fm.items():
            if key.startswith("_"):
                continue
            if not str(fm.get(key, "")).strip() and str(value).strip():
                fm[key] = value          # keep what they wrote where we have nothing
        if str(existing_fm.get("glyph", "")).strip():
            fm["glyph"] = existing_fm["glyph"]   # identity is already established
        body = mark_node_forged(existing_body)
    else:
        body = NOTE_BODY.format(name=name)

    harvested = v.bench_questions()
    for q in harvested:
        body = append_assay(body, q)
    if harvested:
        v.save_bench([])
    v.write_note(path, fm, body)

    if decode_item:
        state["active_ciphers"].remove(decode_item)
        v.strike_wonder(decode_item)
        cprint(f"  ⨂ Cipher decoded: {decode_item}", "Etched")

    if args.branch:
        state["gilt_tokens"] += 1
        cprint(f"  ◈ Branch complete — Gilt Token banked ({state['gilt_tokens']}).", "Gilt")

    v.save_state(state)
    tags = []
    if first_edition:
        tags.append("FIRST EDITION")
    if excellence:
        tags.append("quiz excellence")
    suffix = f"  [{', '.join(tags)}]" if tags else ""
    cprint(f"\n  {fm['sigil']}  {gid} · {name} forged.{suffix}", "bold")
    if harvested:
        cprint(f"  ⚖ {len(harvested)} assay question(s) attached from the bench.", "Etched")
    else:
        cprint("  ⚖ No assay questions. Harvest some before this seals: glyph assay", "dim")

    queue = [g for g in v.glyphs() if g["stage"] == "forged"]
    if args.capstone:
        cprint("\n  ◆ CAPSTONE. The Constellation closes. Sealing the Gilded Folio…", "Illuminated")
        seal_folio(v, gilded=True)
    elif len(queue) >= FOLIO_SIZE:
        print()
        seal_folio(v)
    else:
        print(f"  Forge queue: {len(queue)}/{FOLIO_SIZE} toward the next folio.")


def cmd_seal(args):
    v = Vault.locate(args.vault)
    queue = [g for g in v.glyphs() if g["stage"] == "forged"]
    if not queue:
        sys.exit("Nothing in the forge queue.")
    if len(queue) < FOLIO_SIZE and not args.now:
        sys.exit(f"Only {len(queue)}/{FOLIO_SIZE} glyphs in the queue. Pass --now to seal short.")
    seal_folio(v, gilded=args.gilded)


def seal_folio(v: Vault, gilded=False):
    state = v.state()
    queue = sorted(
        [g for g in v.glyphs() if g["stage"] == "forged"], key=lambda g: g["glyph"]
    )[:FOLIO_SIZE]
    if not queue:
        return

    rng = random.SystemRandom()
    modifiers = []

    folio_twice = False
    if state["gilt_tokens"] > 0:
        state["gilt_tokens"] -= 1
        folio_twice = True
        modifiers.append("gilt-token")
    if kindled_days(state) >= 5:
        folio_twice = True
        modifiers.append("kindled")
    if gilded:
        modifiers.append("gilded")

    table = TABLE_GILDED if gilded else TABLE_STANDARD
    cards = []
    for g in queue:
        card_twice = g.get("quiz_excellence") == "true" or g.get("cipher_decode") == "true"
        times = 2 if (folio_twice or card_twice) else 1
        finish = roll_card(table, times, rng)
        _, body = v.parse_note(g["_path"])
        cards.append({
            "glyph": g["glyph"],
            "name": g["name"],
            "finish": finish,
            "questions": read_assay(body),
        })

    # pity
    def best(cs):
        return max((c["finish"] for c in cs), key=FINISH_ORDER.index)

    if state["folios_since_illuminated"] + 1 >= PITY_ILLUM_AT and best(cards) != "Illuminated":
        rng.choice(cards)["finish"] = "Illuminated"
        modifiers.append("pity-illuminated")
    elif state["folios_since_etched"] + 1 >= PITY_ETCHED_AT and FINISH_ORDER.index(best(cards)) < FINISH_ORDER.index("Etched"):
        rng.choice(cards)["finish"] = roll(TABLE_PITY_ETCHED, rng)
        modifiers.append("pity-etched")

    if FINISH_ORDER.index(best(cards)) >= FINISH_ORDER.index("Etched"):
        state["folios_since_etched"] = 0
    else:
        state["folios_since_etched"] += 1
    if best(cards) == "Illuminated":
        state["folios_since_illuminated"] = 0
    else:
        state["folios_since_illuminated"] += 1

    # echo slot
    revealed = [
        g for g in v.glyphs()
        if g["stage"] == "revealed" and g.get("status") != "fast"
    ]
    echo = None
    if revealed:
        def overdue(g):
            days = (date.today() - date.fromisoformat(g["last_fired"])).days
            return days / max(int(g["interval_days"]), 1)
        weights = [max(overdue(g), 0.1) for g in revealed]
        echo = rng.choices(revealed, weights=weights, k=1)[0]["name"]

    # cipher slot(s) — first-folio rule: no echo candidate → second cipher
    pool = v.wonder_items(exclude_active=state["active_ciphers"])
    ciphers = []
    want = 1 if echo else 2
    for _ in range(min(want, len(pool))):
        pick = rng.choice(pool)
        pool.remove(pick)
        ciphers.append(pick)
    state["active_ciphers"].extend(ciphers)

    state["folio_counter"] += 1
    fid = f"folio-{state['folio_counter']:03d}"
    result = {
        "folio": fid,
        "sealed": today(),
        "gilded": gilded,
        "modifiers": modifiers,
        "cards": cards,
        "echo": echo,
        "ciphers": ciphers,
    }

    plaintext = json.dumps(result, indent=2).encode("utf-8")
    digest = hashlib.sha256(plaintext).hexdigest()
    enc = keystream_xor(plaintext, f"{state['salt']}:{fid}")
    (v.sealed_dir / f"{fid}.ng").write_bytes(enc)
    with v.ledger.open("a", encoding="utf-8") as f:
        mods = ",".join(modifiers) if modifiers else "none"
        f.write(f"- {fid} · sealed {today()} · modifiers: {mods} · sha256 `{digest}`\n")

    n_questions = 0
    for g, card in zip(queue, cards):
        g2 = dict(g)
        path = g2.pop("_path")
        g2["stage"] = "sealed"
        _, body = v.parse_note(path)
        n_questions += len(card["questions"])
        v.write_note(path, g2, strip_assay(body, len(card["questions"])))

    v.save_state(state)
    mods = f"  ({', '.join(modifiers)})" if modifiers else ""
    cprint(f"  ▣ {fid} SEALED{mods}. Contents decided. Hash in the ledger.", "bold")
    if n_questions:
        cprint(f"    {n_questions} assay question(s) sealed out of the notes.", "dim")
    cprint("    No re-rolls. No peeking. See you on Reveal Day.", "dim")


def frame_card(fm, finish=None):
    W = 37
    def row(text, color=None):
        text = text[:W - 4]
        pad = W - 4 - len(text)
        inner = f"{text}{' ' * pad}"
        if color and is_tty():
            inner = f"{C.get(color, '')}{inner}{C['reset']}"
        return f"  │ {inner} │"
    lines = [f"  ┌{'─' * (W - 2)}┐"]
    lines.append(row(f"{fm['sigil']}   {fm['glyph']}", "dim"))
    lines.append(row(fm["name"].upper(), "bold"))
    lines.append(row(f"{fm['type']} · {fm['constellation']}"))
    lines.append(row(f"● {fm['grade']}"))
    if fm.get("lore"):
        words = f'"{fm["lore"]}"'.split()
        line = ""
        for word in words:
            if line and len(line) + 1 + len(word) > W - 4:
                lines.append(row(line, "dim"))
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            lines.append(row(line, "dim"))
    if fm.get("first_edition") == "true":
        lines.append(row("✶ First Edition", "Gilt"))
    lines.append(f"  └{'─' * (W - 2)}┘")
    return "\n".join(lines)


def run_assay(questions, name):
    """Free recall first, then the sealed questions. Returns a percentage."""
    cprint(f"\n  ⚖ ASSAY — {name}", "bold")
    print("    Blank page. Write everything you know about this. No notes, no cues.")
    wait_enter("    — press Enter when the page is full —")
    print(f"\n    Now the {len(questions)} sealed question(s). For each: was it already")
    print("    on your page? Not 'could you answer it now' — recognition is cheap.")
    correct = 0
    for i, q in enumerate(questions, 1):
        print(f"\n    {i}. {q}")
        if ask("       On your page? (y/n)", "y").lower().startswith("y"):
            correct += 1
    pct = round(100 * correct / len(questions))
    color = "Gilt" if pct >= ASSAY_EXCELLENCE else ("dim" if pct < ASSAY_PASS else "")
    cprint(f"\n    Assay: {correct}/{len(questions)} — {pct}%", color)
    return pct


def cmd_reveal(args):
    v = Vault.locate(args.vault)
    state = v.state()
    sealed = sorted(v.sealed_dir.glob("folio-*.ng"))
    if not sealed:
        sys.exit("No sealed folios. Go forge.")

    path = sealed[0]
    fid = path.stem
    plaintext = keystream_xor(path.read_bytes(), f"{state['salt']}:{fid}")
    digest = hashlib.sha256(plaintext).hexdigest()

    ledger_text = v.ledger.read_text(encoding="utf-8")
    m = re.search(rf"{fid} · sealed (\S+) · .*sha256 `([0-9a-f]+)`", ledger_text)
    if not m:
        sys.exit(f"{fid} has no ledger entry. The covenant is broken; investigate.")
    if m.group(2) != digest:
        sys.exit(f"{fid} FAILS hash verification. The seal was tampered with. Covenant broken.")

    try:
        result = json.loads(plaintext.decode("utf-8"))
    except Exception:
        sys.exit(f"{fid} cannot be decoded. Wrong salt or corrupted file.")

    print()
    title = "▣  GILDED FOLIO" if result.get("gilded") else f"▣  {fid.upper()}"
    cprint(title, "Illuminated" if result.get("gilded") else "bold")
    cprint(f"   sealed {result['sealed']} · verified against ledger ✓", "dim")
    if result["modifiers"]:
        cprint(f"   modifiers: {', '.join(result['modifiers'])}", "dim")

    for card in result["cards"]:
        fm = v.find_glyph(card["glyph"])
        questions = card.get("questions") or []
        score = None

        if questions:
            score = run_assay(questions, card["name"])
            if score >= ASSAY_EXCELLENCE:
                state["gilt_tokens"] += 1
                v.save_state(state)
                cprint(
                    f"    ◈ Excellence. Gilt Token banked for the next folio "
                    f"({state['gilt_tokens']}).", "Gilt"
                )
            elif score < ASSAY_PASS:
                cprint("    Thin. This one enters the Codex already fading.", "dim")
        elif fm:
            cprint(f"\n  ⚖ {card['name']} — no assay. Unexamined.", "dim")

        wait_enter("\n  — press Enter to flip —")
        if fm:
            print(frame_card(fm))
        pause(0.9)
        finish = card["finish"]
        flair = {"Matte": "", "Gilt": " ✦", "Etched": " ✦✦", "Aurora": " ✦✦✦", "Illuminated": " ☀ ☀ ☀"}[finish]
        cprint(f"        {finish.upper()}{flair}", finish)
        if finish == "Illuminated":
            cprint("        — an ILLUMINATED. Frame it. —", "Illuminated")
        if fm:
            fm2 = dict(fm)
            p = fm2.pop("_path")
            fm2["stage"] = "revealed"
            fm2["finish"] = finish
            if score is not None:
                fm2["assay"] = f"{score}%"
                if score < ASSAY_PASS:
                    fm2["status"] = "fading"
            _, body = v.parse_note(p)
            v.write_note(p, fm2, body)

    if result.get("echo"):
        echo_fm = v.find_glyph(result["echo"])
        if echo_fm:
            print()
            cprint(f"  ↺ ECHO — {echo_fm['name']} returns.", "Etched")
            print("    60 seconds: say aloud everything you remember. Then check your note.")
            wait_enter("    — press Enter when done —")
            passed = ask("Honest verdict — did you hold it? (y/n)", "y").lower().startswith("y")
            fm2 = dict(echo_fm)
            p = fm2.pop("_path")
            _, body = v.parse_note(p)
            if passed:
                old = fm2["finish"]
                if old in FINISH_ORDER and FINISH_ORDER.index(old) < FINISH_ORDER.index(ECHO_CAP):
                    fm2["finish"] = FINISH_ORDER[FINISH_ORDER.index(old) + 1]
                    cprint(f"    Upgraded: {old} → {fm2['finish']}", fm2["finish"])
                else:
                    print("    Held at its finish — the memory is the reward.")
                idx = INTERVALS.index(int(fm2["interval_days"])) if int(fm2["interval_days"]) in INTERVALS else -1
                if idx == len(INTERVALS) - 1 or idx == -1:
                    fm2["status"] = "fast"
                    cprint("    This glyph is now FAST — fade-proof. Done forever.", "bold")
                else:
                    fm2["interval_days"] = INTERVALS[idx + 1]
                fm2["last_fired"] = today()
                fm2["status"] = fm2.get("status") if fm2.get("status") == "fast" else "bright"
            else:
                fm2["status"] = "fading"
                cprint("    It slips. Marked fading — restore it when you're ready.", "dim")
            v.write_note(p, fm2, body)

    for item in result.get("ciphers", []):
        print()
        cprint(f"  ⨂ CIPHER — {item}", "Aurora")
        print("    Active this season. Forge a glyph about it to decode: rolls twice, wears the stamp.")
    if not result.get("ciphers"):
        cprint("\n  The Wonder List ran dry — no cipher this folio. Feed it.", "dim")

    path.rename(v.revealed_dir / path.name)
    with v.ledger.open("a", encoding="utf-8") as f:
        f.write(f"- {fid} · revealed {today()} · verified ✓\n")
    print()
    cprint("  The Codex grows.", "bold")


def cmd_restore(args):
    v = Vault.locate(args.vault)
    fm = v.find_glyph(args.name or ask("Which glyph?"))
    if not fm:
        sys.exit("No such glyph.")
    if fm.get("status") != "fading":
        sys.exit(f"{fm['name']} isn't fading (status: {fm.get('status')}).")
    cprint(f"  ↻ RESTORATION — {fm['name']}", "bold")
    cprint("    Fresh exam — the old assay questions are spent. Two parts:", "dim")
    print("\n    1. Blank page. Everything you know. No notes.")
    wait_enter("       — press Enter when the page is full —")
    applied = {
        "Concept": "explain it to someone who will ask you *why*",
        "Technique": "do it again, cold, with no reference open",
        "Figure": "argue what they'd have done differently in another decade",
        "Work": "reconstruct its argument or structure from memory",
        "Event": "say what would have changed if it hadn't happened",
        "Instrument": "use it under a constraint you never practiced",
    }.get(fm.get("type", ""), "use it for something real")
    print(f"\n    2. Applied task — {applied}.")
    wait_enter("       — press Enter when done —")
    print()
    passed = ask("Honest verdict — restored? (y/n)", "y").lower().startswith("y")
    fm2 = dict(fm)
    p = fm2.pop("_path")
    _, body = v.parse_note(p)
    if not passed:
        print("    Not yet. Study it again; the card waits.")
        return
    fm2["status"] = "bright"
    fm2["last_fired"] = today()
    fm2["interval_days"] = INTERVALS[0]
    if fm2["grade"] in ("Keystone", "Capstone"):
        fm2["finish"] = "Palimpsest"
        cprint("    ✧ PALIMPSEST — the finish no pack can hold. Earned only by coming back.", "Palimpsest")
    else:
        cprint("    Restored and bright.", "Gilt")
    v.write_note(p, fm2, body)


def cmd_status(args):
    v = Vault.locate(args.vault)
    state = v.state()
    glyphs = v.glyphs()
    revealed = [g for g in glyphs if g["stage"] == "revealed"]
    queue = [g for g in glyphs if g["stage"] == "forged"]
    sealed_n = len(list(v.sealed_dir.glob("*.ng")))

    # fading sweep
    for g in glyphs:
        if g["stage"] == "revealed" and g.get("status") == "bright":
            days = (date.today() - date.fromisoformat(g["last_fired"])).days
            if days > 2 * int(g["interval_days"]):
                fm2 = dict(g)
                p = fm2.pop("_path")
                fm2["status"] = "fading"
                _, body = v.parse_note(p)
                v.write_note(p, fm2, body)
                g["status"] = "fading"

    print()
    cprint(f"◈ THE CODEX — season {state['season']}", "bold")
    print(f"  Glyphs revealed: {len(revealed)}   forge queue: {len(queue)}/{FOLIO_SIZE}   sealed folios waiting: {sealed_n}")
    by_finish = {}
    for g in revealed:
        by_finish[g["finish"]] = by_finish.get(g["finish"], 0) + 1
    if by_finish:
        order = FINISH_ORDER + ["Palimpsest"]
        line = "   ".join(
            f"{f}: {by_finish[f]}" for f in order if f in by_finish
        )
        print(f"  Finishes — {line}")
    print(f"  Gilt Tokens: {state['gilt_tokens']}   Kindled Week: {kindled_days(state)}/5 days")
    bench = v.bench_questions()
    if bench:
        print(f"  Assay bench: {len(bench)} question(s) waiting for your next forge")
    unexamined = [
        g for g in queue if not read_assay(v.parse_note(g["_path"])[1])
    ]
    if unexamined:
        cprint("  In the forge queue with no assay — harvest before they seal:", "dim")
        for g in unexamined:
            print(f"    ⚖ {g['name']}")
    print(
        f"  Pity — Etched+: {state['folios_since_etched']}/{PITY_ETCHED_AT} folios"
        f"   Illuminated: {state['folios_since_illuminated']}/{PITY_ILLUM_AT}"
    )
    if state["active_ciphers"]:
        print("  Active ciphers:")
        for c in state["active_ciphers"]:
            print(f"    ⨂ {c}")
    fading = [g for g in glyphs if g.get("status") == "fading"]
    if fading:
        cprint("  Fading — restore them:", "dim")
        for g in fading:
            print(f"    ↻ {g['name']} ({g['grade']})")
    due = [
        g for g in revealed
        if g.get("status") == "bright"
        and (date.today() - date.fromisoformat(g["last_fired"])).days > int(g["interval_days"])
    ]
    if due:
        cprint("  Due for firing (echo will find them, or beat it to the punch):", "dim")
        for g in due[:5]:
            print(f"    ↺ {g['name']}")
    print()


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Neuroglyphics — Phase 0 engine")
    ap.add_argument(
        "--vault",
        help="fallback vault, used only when you're not inside one "
             "(the nearest enclosing vault always wins)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="scaffold a vault in the current directory")
    i.add_argument("path", nargs="?", help="where to scaffold (default: here)")

    a = sub.add_parser("assay", help="harvest an exam question while studying")
    a.add_argument("question", nargs="?")
    a.add_argument("--for", dest="forcard", help="attach to an already-forged, unsealed glyph")

    f = sub.add_parser("forge", help="create a glyph")
    f.add_argument("--name")
    f.add_argument("--constellation")
    f.add_argument("--type", choices=TYPES)
    f.add_argument("--grade", choices=GRADES)
    f.add_argument("--proof")
    f.add_argument("--lore")
    f.add_argument("--quiz", type=int, help="quiz score percent")
    f.add_argument("--decode", help="active cipher this glyph decodes (substring)")
    f.add_argument("--branch", action="store_true", help="this forge completed a branch (banks a Gilt Token)")
    f.add_argument("--capstone", action="store_true", help="this is a Capstone: seal the Gilded Folio now")

    s = sub.add_parser("seal", help="manually seal a folio")
    s.add_argument("--now", action="store_true", help="seal even with fewer than 3 glyphs")
    s.add_argument("--gilded", action="store_true")

    sub.add_parser("reveal", help="open the oldest sealed folio")
    sub.add_parser("status", help="codex stats")
    sub.add_parser("spark", help="log a study session today")

    t = sub.add_parser("token", help="bank a Gilt Token")
    t.add_argument("--reason")

    r = sub.add_parser("restore", help="recall-check a faded glyph")
    r.add_argument("name", nargs="?")

    w = sub.add_parser("wonder", help="add to the Wonder List")
    w.add_argument("item", nargs="?")

    args = ap.parse_args()
    {
        "init": cmd_init,
        "assay": cmd_assay,
        "forge": cmd_forge,
        "seal": cmd_seal,
        "reveal": cmd_reveal,
        "status": cmd_status,
        "spark": cmd_spark,
        "token": cmd_token,
        "restore": cmd_restore,
        "wonder": cmd_wonder,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
