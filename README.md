# Grounded RAG and Tool-Calling Agent

An LLM system built for a setting where the answer is not enough: a user has to be
able to see *where each claim came from* and check it. Originally built for the
AMP AI Expo (May 2026) over ESG disclosure data, but the pattern is domain-neutral
and is the one regulated industries need from generative AI.

**The problem.** An LLM asked about a company will answer either way, whether or
not it has the data. In a reporting or analysis context that is worse than useless,
because a confident wrong number is harder to catch than a missing one.

**The approach.** Retrieve before generating, and label every part of the answer by
where it came from. Two modes, deliberately:

| Mode | Who decides what to retrieve | Cost | When it earns its place |
|---|---|---|---|
| **RAG** | Application code, fixed path | 1 model call | Retrieval pattern is predictable |
| **Agent** | The model, via tool calls | 1–8 calls | Question shape varies, needs chaining |

The agent is not the default. It costs more and is harder to test, so it should have
to justify itself against the simpler option.

**What makes it auditable.** Bounded turn cap rather than an open loop. Every tool
call traced with arguments, outcome and latency. Tool failures returned to the model
as results so it recovers rather than dying. Enforced markers separating retrieved
data from model-generated context. An 18-test suite covering exactly these properties,
runnable without an API key or the dataset.

Observed on a two-part question: 3 turns, chaining `search_companies` →
`get_company_metrics` → `get_materiality_coverage`. The sequence was chosen by the
model, not hardcoded. Asked about a company not in the dataset, it surfaced the
closest match and flagged that it was a subsidiary rather than the parent, instead
of answering from memory.

**Stack:** Python, Anthropic API (Claude), Flask, SQLite, Docker, pytest.

---

## Two pages

| Page | What it does |
|---|---|
| **1. ESG Knowledge Agent** | Free-form Q&A. Searches our internal KG first; if a company is mentioned, surfaces matched data. Extends with broader ESG/SASB context, clearly labelled `[KG-grounded]` vs `[LLM-extended]`. |
| **2. ESG Analytics for Reporting** | Pick a company → see its SASB material topics, disclosure coverage, and time-series trends. Drafts a 5-section editable report (Exec summary, Industry materiality, Disclosure coverage, Trend analysis, Gaps & recommendations). Tick what you want, export to **PDF**. |

## Data layered into the demo

- **Companies** — 61,272 unique companies merged from 4 source CSVs into `data/companies_master.csv`, each mapped to a SASB industry. *Not committed: derived from a licensed Clarity AI dataset. Rebuild with `scripts/build_company_master.py` if you hold a licence.* Built by [scripts/build_company_master.py](scripts/build_company_master.py).
- **SASB materiality** — Hand-curated from the 6 official SASB industry standards (PDFs) into [data/sasb_materiality.json](data/sasb_materiality.json): 39 disclosure topics, 101 metrics across the 6 industries below.
- **ESG values** — 937,089 metric rows from Clarity AI's dataset, filtered to companies in the 6 demo industries, indexed in [data/esg_metrics.sqlite](data/esg_metrics.sqlite). Built by [scripts/build_metrics_db.py](scripts/build_metrics_db.py).

### Six industries in scope
1. Asset Management & Custody Activities (AMP's core)
2. Commercial Banks (AMP Bank)
3. Insurance
4. Real Estate
5. Metals & Mining
6. Electric Utilities & Power Generators

The architecture supports the other 71 SASB industries — add a curated entry to `sasb_materiality.json`, re-run `build_metrics_db.py`, and they show up.

## Two retrieval modes

| Mode | Who decides what to retrieve | Entry point |
|---|---|---|
| **RAG (page 1 + 2)** | Our code. `server.py` resolves the company from the question, passes a fixed context blob, one model call. | `llm.answer_question()` |
| **Agent** | The model. Given tool definitions, it chooses which to call and in what order, observing each result before deciding the next step. | `agent.run()` |

### Agent loop (`app/agent.py`)

Five tools wrap the same `store.py` methods the rest of the app uses, so there is
one source of truth for data access: `search_companies`, `get_company_metrics`,
`get_metric_timeseries`, `get_materiality_coverage`, `get_industry_materiality`.

Design decisions worth noting:

- **Hard turn cap** (`MAX_TURNS = 8`) rather than `while True`. A looping model is
  a bug you get billed for.
- **Full trace.** Every tool call records name, arguments, success, latency and a
  result preview. An agent you cannot audit is not deployable in a regulated setting.
- **Errors go back to the model** as `tool_result` with `is_error`, rather than
  raising. The model can recover by trying a different query instead of the run dying.
- **Grounding markers.** The system prompt requires `[retrieved]` on anything from a
  tool result and `[general]` on wider context, so a reader can see which is which.
- **Cached prefix reused** from `llm.py`, so the materiality reference stays on the
  cheap path (~11K cached tokens read per run in testing).

Observed on a two-part question ("what emissions does Westpac report, and how
complete is their SASB disclosure?"): 3 turns, chaining
`search_companies` -> `get_company_metrics` -> `get_materiality_coverage`. The
sequence was chosen by the model, not hardcoded.

## How the LLM is used

- Model: **Claude Sonnet 4.6** (`claude-sonnet-4-6`).
- Prompt caching: the system prompt + full materiality reference (~4,400 tokens) is cached once per 5-minute window — second-and-later calls **read 4,893 tokens at 1/10× cost** (verified in smoke tests).
- Architecture: KG-first, LLM-extend. Every response is labelled so the audience knows what's in the data vs what is wider context.

## Run

```bash
cp .env.example .env
# Edit .env — paste your ANTHROPIC_API_KEY (https://console.anthropic.com/settings/keys)

./run.sh
# open http://localhost:5050
```

The `run.sh` script auto-creates the venv on first run.

## First-time setup (fresh clone)

The repo commits the curated data (`data/sasb_materiality.json`,
`data/companies_master.csv`) but **not the 270MB SQLite metrics index** — it's
derived data, rebuildable in ~15 seconds.

```bash
# 1) Install deps (the run scripts also do this on first run)
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# 2) Rebuild the ESG metrics SQLite. You need the Clarity AI dataset locally.
#    Point at where your CSV chunks live:
AMP_ESG_METRICS_DIR="/path/to/2.ESG Metrics Datasets Sorted(Clarity AI)" \
  venv/bin/python scripts/build_metrics_db.py

# 3) (Only if you also want to regenerate the company-industry CSV.)
AMP_INDUSTRY_MATCHING_DIR="/path/to/AMP/Industry Matching List(Clarity AI dataset)" \
  venv/bin/python scripts/build_company_master.py
```

Then `./run.sh` and you're live at http://localhost:5050.

## Files

- [app/server.py](app/server.py) — Flask routes
- [app/store.py](app/store.py) — read-only data access (companies, materiality, ESG metrics, coverage analysis)
- [app/llm.py](app/llm.py) — Anthropic SDK client with prompt caching
- [app/pdf.py](app/pdf.py) — Markdown → PDF via ReportLab
- [templates/index.html](templates/index.html) — single-page UI with two top-level tabs
- [static/app.js](static/app.js), [static/styles.css](static/styles.css) — frontend
- [scripts/build_company_master.py](scripts/build_company_master.py) — merges 4 company CSVs into one trusted set
- [scripts/build_metrics_db.py](scripts/build_metrics_db.py) — filters 5M-row ESG dataset → SQLite
- [data/sasb_materiality.json](data/sasb_materiality.json) — curated SASB industry materiality
- [data/companies_master.csv](data/companies_master.csv) — 61k companies → SASB industry

## Sharing the demo online

Two paths depending on how long you need it.

### Option A — Quick share via ngrok (laptop stays running)

For the booth itself or quick demos with colleagues.

```bash
# Terminal 1 — run the demo with the password gate ON
ACCESS_PASSWORD="ampexpo2026" ./run.sh

# Terminal 2 — expose it
ngrok http 5050
```

ngrok prints a `https://*.ngrok-free.app` URL. Share that + the password.
When you close ngrok or your laptop sleeps, the link dies.

### Option B — Render (always-on public link)

A real public URL backed by a Docker image. ~30 min from zero.

**Prerequisites:** GitHub repo with this code committed, free Render account.

1. **Rotate your Anthropic API key** at https://console.anthropic.com/settings/keys
   (the original one was pasted into a conversation log and should not be reused
   publicly).
2. Push this directory to a GitHub repo.
3. In Render: New → Web Service → connect the repo. Render detects [render.yaml](render.yaml)
   automatically.
4. When prompted for env vars, fill in:
   - `ANTHROPIC_API_KEY` — your fresh key
   - `ACCESS_PASSWORD` — any shared password you want visitors to type
   - `ACCESS_COOKIE_SECRET` — Render generates this automatically (per `render.yaml`)
5. First build takes ~5 min. Subsequent deploys are ~2 min.

The free Render tier sleeps after 15 minutes of inactivity — for the booth
itself I'd recommend the `starter` plan (~$7/month, always-on), then downgrade
afterwards.

### Security on the public link

Three layers are already wired in:

1. **Shared password** — `ACCESS_PASSWORD` env var. Without it, the gate is
   disabled (local dev). With it, every page + API endpoint requires a signed
   cookie obtained via `/login`.
2. **Per-IP rate limit** — `30 /api/ask`/hour, `60 /api/company/.../report-section`/hour,
   `10 /api/generate-pdf`/hour. Hard-coded in [app/server.py](app/server.py).
3. **API key never client-side** — it lives in env vars; the browser only
   ever sees the dashboard's own responses.

If you suspect abuse, just rotate `ACCESS_PASSWORD` in Render — existing cookies
become invalid immediately because the signing secret is derived from it.

## Growing the knowledge graph

The booth narrative: **the LLM fills in what the KG doesn't have, you backfill what users actually ask.** Operationally:

1. New company missing from `companies_master.csv` → add to source CSVs and re-run `build_company_master.py`.
2. New industry → add to `sasb_materiality.json`, drop the SASB PDF in `AMP/77 Industries/`, re-run `build_metrics_db.py`.
3. New metric mappings → extend `_TOPIC_KEYWORDS` in [app/store.py](app/store.py) so coverage analysis improves.
