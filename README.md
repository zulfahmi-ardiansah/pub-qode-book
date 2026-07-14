# Qodebook — UNSPSC ↔ KBLI

![Version](https://img.shields.io/badge/version-alpha-blue) ![Python](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/fastapi-0.115+-009688?logo=fastapi&logoColor=white) ![SQLite](https://img.shields.io/badge/sqlite-3-003B57?logo=sqlite&logoColor=white)

**Qodebook** bridges two code systems that describe different things: what a business **sells** (UNSPSC) and what a business **does** (KBLI).

Both are strict hierarchies, both are published by different bodies for different purposes, and nothing joins them. So a question as ordinary as *"a company registered under KBLI 01111 — what would it actually be selling?"* has no lookup table to answer it. UNSPSC has ~158,000 entries; KBLI has ~2,400. Matching them by hand is not work anyone finishes.

This repository does two things about that. `process/mapper.py` builds the missing link with an LLM, narrowing 559 UNSPSC families down to the handful a KBLI activity plausibly produces, and writes each pair into SQLite. `serve/app.py` then serves that database as a bilingual web app — search either taxonomy, read what a code covers, see where it sits in its hierarchy, and follow it across to the codes it links to on the other side.

See [definition.md](definition.md) for what the two taxonomies are and how their codes are shaped.

> **The mapping is machine-generated.** It is not an official correspondence table. Some links are wrong. Check any link you intend to rely on.

---

## How It Works

Three folders, one direction of flow. `process/` writes the database, `data/` holds it, `serve/` reads it — and only reads it: every connection the app opens is `mode=ro` with `PRAGMA query_only`, so no request can mutate what the mapper built.

```
process/mapper.py  ──writes──▶  data/database.sqlite  ──reads──▶  serve/app.py
     (LLM cascade)                  (3 tables, 1 view)              (FastAPI)
```

### The mapping cascade

Handing an LLM all 559 UNSPSC families at once produces mush. So each KBLI activity is classified in four passes, each one scoped to what the pass before it chose:

| # | Pass | Pool | What happens |
|---|------|------|-------------|
| 1 | Segment group (L0) | 10 groups | Broad recall pass. Group titles are coarse and carry no description, so each group is rendered annotated with the titles of the segments beneath it. A group missed here is lost for good, so the prompt favours recall. |
| 2 | Segment (L1) | 58 segments, scoped to the chosen groups | Keeps any segment that could hold the activity's output in any form. Still recall-oriented — a later pass prunes. |
| 3 | Family (L2) | 559 families, scoped to the chosen segments | Keeps every family that holds the output in any of its forms or stages. Produces the candidate list. |
| 4 | Strict filter | just the candidates from pass 3 | The precision pass. Keeps a family only if a specific good or service the activity actually delivers can be named for it. An empty list is a correct answer here. |

Each surviving family becomes one `map_master` row. The KBLI activity is not sent alone — `build_context` walks its ancestor chain up to the root and hands the model the full narrowing hierarchy, so *Pertanian Jagung* arrives with *Pertanian Tanaman Semusim* and *Pertanian, Kehutanan dan Perikanan* above it.

### What that produces

| Table / view | Rows | What it is |
|--------------|------|------------|
| `master_unspsc` | 158,473 | UNSPSC: 10 groups, 58 segments, 559 families, 7,998 classes, 149,848 commodities |
| `master_kbli` | 2,444 | KBLI: 22 kategori, 87 golongan pokok, 257 golongan, 519 subgolongan, 1,559 kelompok |
| `map_master` | 5,236 | The links: 1,559 KBLI kelompok × 553 UNSPSC families, many-to-many |
| `map_view` | 5,236 | `map_master` with both titles joined on, so it reads without a lookup |

The mapping is made at exactly one altitude: **UNSPSC family** ↔ **KBLI kelompok**. The app fills in the rest — a UNSPSC class or commodity inherits the links of its family, and a KBLI golongan rolls up the links of the kelompok beneath it. Both cases say so on the page, so a borrowed link never passes as a direct one.

---

## Requirements

### Python 3.13+

Download from [python.org](https://www.python.org/downloads/). Verify with:

```bash
python --version
```

### An OpenAI-compatible LLM endpoint

Only needed to **run the mapper**. Reading the app needs nothing but the database. The client is the `openai` SDK pointed at any compatible base URL — the default targets [OpenRouter](https://openrouter.ai/).

---

## Installation

**1. Clone the repository**

```bash
git clone <repo-url>
cd adw-pdc-unspsc-kbli-mapper
```

**2. Create a virtual environment**

```bash
python -m venv .venv
```

**3. Activate it and install dependencies**

Windows:
```bat
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux / macOS:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

**4. Create your `.env`**

```bash
cp .env.example .env
```

The app runs with the defaults as-is. Fill in `LLM_API_KEY` only if you intend to run the mapper.

---

## Usage

### Run with Docker (recommended)

The image bundles Python, the app, and the database. Nothing else is needed on the host.

```bash
docker compose up --build
```

Open <http://localhost:8000>.

**To serve on a different port**, set `PORT` — in `.env`, or inline for one run:

```bash
PORT=9000 docker compose up          # now on http://localhost:9000
```

`PORT` means one thing throughout: the port the app is served on, inside the container and on your machine alike. `docker compose` reads it from `.env` automatically.

Without compose:

```bash
docker build -t qodebook .
docker run -p 9000:9000 -e PORT=9000 qodebook
```

`data/database.sqlite` is baked into the image, and compose also mounts it read-only from the host — so re-run the mapper, restart the container, and the new mapping is served without a rebuild. The mapper itself is not in the image: it writes to the database, and the container runs as an unprivileged user that owns nothing it could write to.

### Run locally

```bash
uvicorn serve.app:app --reload
```

Open <http://127.0.0.1:8000>, or pass `--port 9000` to move it. The database ships with the repository, so there is nothing to build first.

### Run the mapper

```bash
python process/mapper.py
```

It walks every KBLI kelompok that is not already in `map_master`, runs the four-pass cascade, and commits after each activity — so an interrupted run resumes exactly where it stopped. Progress streams to the console, one line per pass:

```
14:22:07    map | [3/1559] 01112 (Kelompok) Pertanian Kedelai
14:22:11     l0 | 2 groups -> 10000000, 50000000
14:22:15     l1 | 3 segments -> 10100000, 10150000, 50300000
14:22:21     l2 | 6 candidates -> 10151500, 10151600, ...
14:22:26     ok | 4 families -> 10151600, 10171500, 50301500, 50303600
```

Both commands work from any directory — `SQLITE_PATH` is resolved from the project root, not from wherever you happened to run them.

---

## Configuration

Everything is environment variables, read from `.env` at the project root. Start from [.env.example](.env.example).

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `SQLITE_PATH` | no | `data/database.sqlite` | Path to the database. Relative paths resolve from the project root |
| `PORT` | no | `8000` | The port the app is served on. Read by `docker compose`. Running uvicorn directly, pass `--port` instead |
| `LLM_BASE_URL` | no | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | mapper only | — | API key for that endpoint |
| `LLM_MODEL` | no | `deepseek/deepseek-v4-flash` | Model id. Must support JSON-schema structured output |
| `LLM_PROVIDER` | no | — | Comma-separated OpenRouter providers to pin the run to |
| `LLM_BUDGET_USD` | no | — | Spend cap. Empty or `0` disables the guard |
| `LLM_INPUT_PRICE` | no | — | USD per 1,000,000 input tokens |
| `LLM_OUTPUT_PRICE` | no | — | USD per 1,000,000 output tokens |

### The budget guard

Set `LLM_BUDGET_USD` above zero and every response's token usage is priced against `LLM_INPUT_PRICE` / `LLM_OUTPUT_PRICE` and added to a running total. The moment that total reaches the cap the run aborts mid-activity — cleanly, because the run is resumable. Leave the three keys empty and no spend is tracked at all.

### Failure handling

| Situation | What the mapper does |
|-----------|---------------------|
| Transient API error | Three attempts in total, backing off 1s then 2s between them |
| Rate limit (429) | Waits 60s and retries **once**. A second 429 is fatal to the whole run — the endpoint is telling you to stop |
| No families fit an activity | Logged as an error for that KBLI, run continues. An empty result is a legitimate answer from the filter pass |
| Budget reached | Run stops. Re-run later to continue from that activity |

---

## The App

Four routes, three pages.

| Route | What it serves |
|-------|---------------|
| `/` | Pick a taxonomy. Shows what each one is, how many codes it holds, and how the two sides are linked |
| `/browse/{name}` | The hierarchy: a lazy-loading tree plus flat search across code, title and definition |
| `/browse/{name}/{code}` | One code — its position in the hierarchy, its definition, its siblings, its children, and the codes it links to across the bridge |
| `/download/{what}.{ext}` | A whole table as CSV or XLSX. `what` is `unspsc`, `kbli` or `mapping`; `ext` is `csv` or `xlsx` |

Backed by three JSON endpoints the client calls directly: `/api/tree/{name}?parent=` (children of one node, so the tree fetches branches only as they open), `/api/search/{name}?q=` (capped at 100 rows, with the true total reported alongside), and `/lang/{code}` (switches language and returns you to the page you were on).

**Features:** bilingual EN / ID · dark mode · lazy hierarchy tree · full-text search across code, title and definition · cross-taxonomy links with inheritance and roll-up · CSV and XLSX export.

### Language

The UI runs in English or Indonesian, chosen by a `lang` cookie. Level names are **always** taken from the `LEVELS` table in [serve/app.py](serve/app.py) and never from the database's `category` column — that column holds English for UNSPSC ("Commodity") and Indonesian for KBLI ("Kelompok"), which would otherwise mix two languages on one screen. The same string table is handed to the client, so JavaScript renders the tree in whatever language the page was rendered in.

### Downloads

Three tables, two formats. CSV is streamed a chunk at a time — UNSPSC is 158k rows, so the file is built as it is sent rather than assembled in memory first, and it leads with a BOM so Excel reads the Indonesian titles as UTF-8 instead of mangling them. XLSX is written through openpyxl's write-only workbook (one row in memory at a time) to a temp file that is deleted once the response has been sent; its header row is bold, frozen, and labelled in the language the page was in.

---

## Project Structure

```
/
├── data/
│   ├── structure.sql              # The schema: 3 tables + map_view. The whole truth of the database
│   ├── database.sqlite            # The built database — committed, so the app ships with its data
│   └── dump_*.sql                 # Plain-SQL dumps of each table, for reloading elsewhere
│
├── process/
│   └── mapper.py                  # The LLM cascade. The only thing that writes to the database
│
├── serve/
│   ├── app.py                     # FastAPI reader — routes, exports, string tables
│   ├── templates/                 # Jinja2: base, home, browse, detail
│   └── static/                    # css, js, img
│
├── Dockerfile                     # The reader, served. Mapper and secrets stay out of the image
├── docker-compose.yml             # One service, one PORT
│
├── definition.md                  # What UNSPSC and KBLI are, and how their codes are shaped
├── requirements.txt               # Python dependencies
├── .env.example                   # Annotated config — copy to .env
└── README.md
```

### Key Files Explained

| File | Purpose |
|------|---------|
| `process/mapper.py` | The full pipeline — pool loading, context building, the four-pass cascade, the budget guard, and the writes |
| `serve/app.py` | Every route, both string tables (EN / ID), the level-name tables, and the CSV / XLSX exporters |
| `serve/static/js/app.js` | Theme toggle, lazy tree, search, list paging, download popup. Every init is a no-op on pages without its markup, so one script serves them all |
| `data/structure.sql` | The schema. Keep it in step with the database — the mapping download reads `map_view`, which is defined here |
| `definition.md` | Read this first if the code masks (`xxxx0000`, `xxxxx`) mean nothing to you yet |

---

## Rebuilding From Nothing

The database is committed, so this is only for starting over.

**1. Create the schema**

```bash
sqlite3 data/database.sqlite < data/structure.sql
```

**2. Load the two taxonomies**

```bash
sqlite3 data/database.sqlite < data/dump_master_unspsc.sql
sqlite3 data/database.sqlite < data/dump_master_kbli.sql
```

`master_unspsc` and `master_kbli` are source data — the mapper reads them and never writes them. It has nothing to map until they are loaded.

**3. Build the mapping**

```bash
python process/mapper.py
```

Or skip the LLM entirely and load the existing mapping: `sqlite3 data/database.sqlite < data/dump_map_master.sql`.

---

## Troubleshooting

### `Database not found at ...`

**Cause:** `SQLITE_PATH` points somewhere the database is not. Both the app and the mapper resolve relative paths from the **project root**, not from your current directory.

**Solution:**
- Check `.env` — the value should be `data/database.sqlite`
- If you moved the database, use an absolute path
- The app raises this as a 500 with the full resolved path in the message; the mapper refuses to start rather than let sqlite3 quietly create an empty file and fail later on a missing table

---

### `no such table: map_view`

**Cause:** The database was built from a `structure.sql` that predates the view, or the view was dropped.

**Solution:** Recreate it — the definition is at the bottom of [data/structure.sql](data/structure.sql). Only the mapping download depends on it; the rest of the app keeps working without it.

---

### `rate limit persisted, aborting`

**Cause:** The endpoint returned 429 twice — once, then again after a 60-second wait. The mapper treats that as the provider telling you to stop, and kills the run rather than hammering it.

**Solution:**
- Wait, then re-run. Nothing is lost: every mapped activity is already committed, and the run resumes from the first unmapped one
- If it keeps happening, pin a specific provider with `LLM_PROVIDER`, or move to a model with a higher limit

---

### `budget exceeded, aborting`

**Cause:** Accumulated token cost reached `LLM_BUDGET_USD`. Working as intended.

**Solution:** Raise the cap, or clear it to disable the guard. Re-run to continue from where the spend ran out.

---

### The mapper logs `EmptyClassificationError` for many activities

**Cause:** Either the model is genuinely finding no fit, or it is not honouring the response schema and its answers are being discarded — every pass validates the returned codes against the pool it offered and drops anything not in it.

**Solution:**
- Confirm `LLM_MODEL` supports JSON-schema structured output. A model that ignores the schema will fail every pass
- Watch an `l0` line: if a broad first pass over 10 groups returns nothing, the problem is the model, not the activity

---

### A code's page shows links but the mapping is not its own

**Cause:** Not a bug. `map_master` links UNSPSC families to KBLI kelompok and nothing else. A code above or below that altitude borrows from the nearest linked relative — a class or commodity inherits its family's links, a golongan rolls up the kelompok beneath it.

**Solution:** Read the note under the heading. It names the code the links were borrowed from.

---

### `Bind for 0.0.0.0:8000 failed: port is already allocated`

**Cause:** Something else on your machine already holds that port — often a local `uvicorn` still running from a previous session.

**Solution:** Stop it, or move the app: `PORT=9000 docker compose up`. Set `PORT` in `.env` to make it stick.

---

### Excel shows the Indonesian titles as mojibake

**Cause:** Excel read the CSV as the local codepage instead of UTF-8.

**Solution:** The CSV already leads with a UTF-8 BOM, which is what tells Excel otherwise — if it still mangles, use the XLSX download instead, which carries no encoding ambiguity.
