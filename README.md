# neuroglyphics.py — Engine Reference

The command-line engine that runs the system. One Python file, standard library only, no installs, no dependencies. It lives at the root of the vault (`neuroglyphics.py`) so the code travels with your notes.

For *what to do*, see [[Workflow]]. This is *how the tool works*.

---

## Get it running

**This repo is a vault template. The folder you clone becomes your vault.**

You need Python 3 — macOS and most Linux distros already have it. Check with `python3 --version`.

### 1. Clone it under whatever name you want your vault to have

```bash
git clone https://github.com/jeffrey-allan-brown/neuroglyphics-engine.git my-vault
cd my-vault
```

No git? Download the ZIP from the repo's green **Code** button, unzip it, and `cd` into the folder. Everything below works the same.

### 2. Scaffold

```bash
python3 neuroglyphics.py init
```

This creates `Codex/`, `Constellations/`, `Covenant.md`, `Wonder List.md`, and the hidden `.neuroglyphics/` — including a **salt unique to you**, generated on the spot. Two vaults never share one.

`init` only creates what's missing, so it's safe to re-run. It never overwrites a note, a signed Covenant, your Wonder List, the ledger, or `state.json`.

### 3. Add the alias

```bash
echo "alias glyph='python3 \"$PWD/neuroglyphics.py\" --vault \"$PWD\"'" >> ~/.zshrc
source ~/.zshrc
```

Run that from inside the vault — `$PWD` bakes in the absolute path, so `glyph` works from anywhere. On bash, use `~/.bashrc`.

```bash
glyph status
```

Sign the Covenant, sketch a Constellation, feed the Wonder List. Then forge your first glyph.

### 4. Point Obsidian at it

Open Obsidian → **Open folder as vault** → pick the folder you just cloned. `.neuroglyphics/` stays invisible inside the app because of the leading dot.

---

## Your cards never enter this repo

`.gitignore` excludes everything you and the engine generate — `state.json`, the ledger, sealed folios, `Codex/`, `Constellations/`, your Covenant, your Wonder List. Only the engine and its docs are tracked.

That has one very useful consequence: **your clone stays a clean copy of the engine**, so updates are just

```bash
git pull
```

and your cards are never part of the merge.

**If you'd rather back the vault up in your own private repo**, open `.gitignore` and delete the block marked `vault contents` — you *want* those files tracked. Two cautions: point `origin` at your own repo first (`git remote set-url origin …`), and keep that repo **private**. `state.json` holds your salt.

## How the vault gets found

Two different rules, on purpose.

**`init` always scaffolds where you're standing.** It ignores `--vault` entirely, so a pinned alias can never silently re-initialize some other directory when you meant *here*. Pass a path to override: `glyph init ~/somewhere/else`. If the target sits inside an existing vault, it warns and asks before nesting.

**Every other command finds the nearest enclosing vault**, walking up from your current directory the way git finds `.git/`. Run `glyph status` from `Codex/` or four levels deep in `Constellations/` and it resolves to the vault root.

`--vault` is a **fallback, not an override** — it applies only when you aren't inside a vault at all. That ordering is what makes a pinned alias useful: standing in your home directory, `glyph status` still reports on your main vault; standing inside a different vault, it reports on *that* one. To force a specific vault, `cd` there.

| Where you are | What `glyph status` targets |
|---|---|
| Inside a vault (any depth) | That vault |
| Inside a *different* vault | That one — the pin doesn't win |
| Outside any vault | The `--vault` fallback from your alias |
| Outside any vault, no alias | Error, with instructions |

---

## What it creates

```
my-vault/
├── neuroglyphics.py          the engine          ← tracked
├── README.md                 this file           ← tracked
├── .gitignore                                    ← tracked
├── Codex/                    one .md per glyph — your cards
├── Constellations/           your maps (you write these)
├── Wonder List.md            curiosities, one per bullet
├── Covenant.md               sign it
└── .neuroglyphics/           ← hidden from Obsidian
    ├── state.json            counters, tokens, pity, ciphers, salt
    ├── bench.json            harvested questions awaiting a forge
    ├── ledger.md             append-only seal/reveal log with hashes
    ├── sealed/*.ng           encrypted folio contents
    └── revealed/*.ng         opened folios, kept for the record
```

Everything not marked *tracked* is gitignored — it's yours, not the repo's.

`init` creates `Codex/`, `Constellations/`, `Covenant.md`, `Wonder List.md`, and `.neuroglyphics/`. Any other folder you add to the vault is left alone.

The leading dot on `.neuroglyphics/` is load-bearing: Obsidian hides dotfolders, so sealed contents are invisible from inside the app by construction. Don't rename it.

---

## Commands

### `glyph init [path]`
Scaffolds a vault in the current directory, or at `path` if given. Idempotent — run it whenever. Ignores `--vault`; warns before nesting inside an existing vault.

### `glyph wonder [item]`
Appends to the Wonder List. Interactive if no argument.
```bash
glyph wonder "how mirrors are made"
```

### `glyph spark`
Logs a study session for today. Five sparks in a rolling seven days = **Kindled Week**, which makes every card in the next sealed folio roll twice and keep the better. Once per day; running it twice is a no-op.

### `glyph assay [question]`
Harvests an exam question. **Do this while studying, not after.**

| Flag | Effect |
|---|---|
| *(none)* | Question goes to the bench, attaches to your next `forge` |
| `--for "Card Name"` | Attaches directly to an already-forged, not-yet-sealed glyph |

```bash
glyph assay "Why did the Estates-General deadlock over voting by order?"
glyph assay "What breaks if the barrier is higher?" --for "The Coulomb Barrier"
```

Errors if the target card is already sealed — its assay is committed and can't be added to.

### `glyph forge`
Creates a glyph. Interactive by default; every flag has a prompt.

| Flag | Effect |
|---|---|
| `--name` | Card name (becomes the filename) |
| `--constellation` | Which map it belongs to. Use `Unsorted` if none fits |
| `--type` | `Concept` · `Technique` · `Figure` · `Work` · `Event` · `Instrument` |
| `--grade` | `Node` · `Branch` · `Keystone` · `Capstone` |
| `--proof` | Comma-separated: `Recall,Exposition` |
| `--lore` | One sentence in the card's voice |
| `--quiz 95` | Score from an **externally authored** exam. ≥90 doubles this card's roll immediately |
| `--decode "mirrors"` | This glyph decoded an active Cipher (substring match). Doubles the roll |
| `--branch` | This forge closed a branch. Banks a Gilt Token |
| `--capstone` | Seals the Gilded Folio immediately at tripled odds |

**Every third forge auto-seals a folio.** You'll see `SEALED` and nothing more.

### Map-aware forging

Cards live **in place**. Forging `React` writes to `Constellations/frontend development/React.md` — the node note *becomes* the card, so your `binds:` edges stay wired and the Obsidian graph keeps working.

Before prompting, the engine searches `Constellations/**` for a note whose filename matches (case-insensitively) and pre-fills from it:

| From the node note | Fills |
|---|---|
| Filename | `name` — adopts the map's capitalisation, so `react` → `React` |
| Parent folder | `constellation` |
| `**class:** instrument` | `type` → `Instrument` |
| `> [!abstract]` callout | `lore` |
| `**arm:**` | Shown for confirmation |

On success it prints `✦ On the map — frontend development · VI. The Scaffold`, then flips `#unforged` → `#forged` and `**status:** unforged` → `forged`.

If no node note matches, you get `○ Not on any map. Forging unmapped.` — a signal the glyph is drifting and may want a constellation later.

Any flag you pass still wins over the map.

**Forging a name that already has a note is safe.** The engine *adopts* the note instead of overwriting it:

- Your body content is preserved completely — every heading, every paragraph
- Existing frontmatter fills any field you didn't supply
- An existing `glyph:` ID is kept, and the counter isn't advanced
- Harvested assay questions append to the existing `## Assay` section
- You'll see `↩ Adopting existing note — your writing is preserved.`

This is the intended path for the draft workflow: write the note first with `stage: draft`, study into it, then forge when the proof lands.

**Already-sealed cards are refused.** If the note's `stage` is `sealed` or `revealed`, forging aborts — it's a card, not a draft, and re-forging would duplicate it. Edit the note directly instead.

### `glyph seal`
Manual sealing. Rarely needed — `forge` handles it.

| Flag | Effect |
|---|---|
| `--now` | Seal with fewer than 3 glyphs in the queue |
| `--gilded` | Force tripled odds |

### `glyph reveal`
Opens the **oldest** sealed folio. Verifies the hash against the ledger first — a mismatch aborts with a covenant warning and changes nothing.

Per card: Assay (free recall, then sealed questions) → flip → finish. Then the Echo recall check, then Cipher draws. Moves the folio to `revealed/` and logs it.

### `glyph status`
Everything at a glance: counts by finish, forge queue, sealed folios waiting, Gilt Tokens, Kindled Week progress, pity counters, active ciphers, the fading queue, cards due for firing, and any queued card with no assay attached.

Also sweeps for newly-faded glyphs — running it is how fading gets applied.

### `glyph restore "Name"`
Fresh two-part exam on a faded glyph: blank-page recall plus an applied task matched to the card's `type`. Never reuses the spent questions. Faded **Keystones and Capstones** return as **Palimpsest**.

### `glyph token --reason "..."`
Banks a Gilt Token manually. Use for Seals, which are yours to judge.
```bash
glyph token --reason "Seal: Cartographer"
```

---

## Who owns which field

The engine writes some frontmatter and reads the rest. Hand-editing an engine-owned field will be silently overwritten.

| Engine writes | You write |
|---|---|
| `glyph`, `sigil`, `stage`, `finish`, `assay` | `name`, `constellation`, `type`, `grade` |
| `last_fired`, `interval_days`, `status` (on reveal/restore) | `proof`, `lore`, `forged` |
| `first_edition`, `cipher_decode` | `quiz_excellence` |

Full field meanings: [[Glyph — field reference]]

**`stage: draft`** is the useful escape hatch. The engine only sweeps `stage: forged` cards into folios, so a draft can never seal unproven. Set it by hand when you want a card to exist before its proof does.

---

## state.json

```json
{
  "season": "S1",
  "glyph_counter": 14,
  "folio_counter": 5,
  "gilt_tokens": 1,
  "folios_since_etched": 3,
  "folios_since_illuminated": 12,
  "active_ciphers": ["how mirrors are made"],
  "sparks": ["2026-08-10"],
  "salt": "59a0763142fcdb49"
}
```

**Don't lose the salt.** It's the decryption key for every sealed folio. Losing it makes unopened folios unreadable — the cards are still safe in `Codex/`, but their finishes are gone.

**Season rollover:** set `"season": "S2"` and `"glyph_counter": 0`. Do the closing ritual first.

**Hand-created cards:** if you write a card manually with `glyph: S1-001`, bump `glyph_counter` to match so the next forge doesn't collide.

---

## Tuning

All constants sit at the top of `neuroglyphics.py`:

| Constant | Default | What |
|---|---|---|
| `FOLIO_SIZE` | `3` | Forges per folio |
| `TABLE_STANDARD` | 68/20/8/3/1 | Finish odds |
| `TABLE_GILDED` | 24/40/24/9/3 | Gilded Folio odds |
| `PITY_ETCHED_AT` | `10` | Dry folios before a guaranteed Etched+ |
| `PITY_ILLUM_AT` | `25` | Dry folios before a guaranteed Illuminated |
| `INTERVALS` | `14,30,90,365` | Firing schedule in days |
| `ASSAY_PASS` | `70` | Below this, a card enters fading |
| `ASSAY_EXCELLENCE` | `90` | At or above, banks a Gilt Token |

Covenant rule 4: **tune between seasons, never retroactively.**

---

## How sealing actually works

At seal time the engine rolls contents and finishes, applies modifiers and pity, sweeps in your harvested questions, serializes to JSON, encrypts it with a SHA-256 keystream derived from your salt, and appends the *plaintext's* hash to `ledger.md`. At reveal it decrypts, re-hashes, and compares.

**Honest threat model:** the encryption is anti-*peek* friction, not security against yourself — you own the salt, so a determined cheater could decode a sealed folio. But a determined cheater could also steam open an envelope. The hash makes tampering *detectable*; the covenant makes it pointless. Same trust model as solitaire.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No vault here, and none above …` | You're outside any vault and have no `--vault` fallback. `cd` into one, or `init` here |
| Commands hit the wrong vault | You're standing inside a different vault — the nearest one always wins over `--vault`. `cd` to the one you meant |
| `sits inside an existing vault` | You tried to nest vaults. Almost always a mistake; answer `n` |
| `FAILS hash verification` | A sealed file was modified. Nothing is changed; investigate before proceeding |
| `cannot be decoded` | Wrong salt — usually a `state.json` restored from a different vault |
| `… is already sealed` | You tried to `assay --for` a committed card. Harvest for the next glyph instead |
| `is already sealed — it's a card, not a draft` | You re-forged a revealed card. Edit the note directly, or use a different name |
| `Only 2/3 glyphs in the queue` | Use `seal --now` if you really want a short folio |
| `Nothing in the forge queue` | Every forged card is already sealed |
| Fading not appearing | Run `glyph status` — that's what applies the sweep |
| Colors look wrong | Output isn't a TTY. Piping strips ANSI on purpose |

**Backups:** `.neuroglyphics/` is the only irreplaceable part. Your cards are plain markdown; the state, ledger, and salt are not — and this repo's `.gitignore` deliberately keeps them out of git, so cloning the template does **not** back you up.

Pick one and actually do it:

- **A sync service** — put the vault in iCloud / Dropbox / Syncthing. Simplest, covers everything.
- **Your own private repo** — delete the `vault contents` block in `.gitignore`, `git remote set-url origin` to a repo you own, and keep it private. You lose clean `git pull` updates from upstream, but you get versioned history of every card.

Either way, back up before your first `seal`. After that, losing the salt costs you every unopened folio.
