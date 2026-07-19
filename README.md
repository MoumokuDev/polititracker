# TruthTracker

Self-hostable accountability dashboards for US federal officials across all three
branches. The core value is **verifiable provenance**: paste a claim like
"Official X said Y" and get the actual primary-source utterance (or its honest
absence) with date, context, and link — plus longitudinal views of statements
vs. actions (votes, filings, campaign finance).

**Anti-fabrication guarantees baked into the architecture:**

- No generative model anywhere in the serving path. Ingestion is parsing,
  matching is retrieval, and everything displayed is a verbatim extract from a
  stored raw payload (`source_fetch`).
- Every displayed fact links to its primary source (`statement.source_url` is
  `NOT NULL` by schema).
- "No match" answers report actual corpus coverage (`source_package`) — the
  system never conflates "they never said it" with "we never ingested that week."
- The Congressional Record distinction between floor-delivered and
  inserted/extended remarks is preserved (`source_type`), because members
  routinely submit speeches that were never spoken.

## Status

Phase 1, build step 2 complete:

- **Step 1** — scaffold, schema/migrations, figure entities seeded from
  [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators)
  (Texas delegation pilot) plus manually verified executive and judicial figures.
- **Step 2** — ingestion adapters: House roll-call votes (Congress.gov API),
  Senate roll-call votes (senate.gov LIS XML), Congressional Record statements
  (GovInfo CREC via package MODS member attribution), bill enrichment with
  policy areas. Speaker-turn extraction is covered by unit tests against a
  captured fixture, and every stored utterance is verified to be a verbatim
  substring of its raw source payload.
- **Step 3** — claim search, the demo-able core: contiguous-slice chunking
  (chunks reassemble to the exact original text — unit-tested property),
  local bge-small-en-v1.5 embeddings into pgvector, hybrid retrieval
  (HNSW cosine + Postgres websearch full-text, fused with Reciprocal Rank
  Fusion), `GET /api/search` JSON endpoint, and a server-rendered search UI
  at `/`. Every response carries corpus-coverage metadata; results below the
  similarity threshold are reported as "no strong match" — never as proof a
  statement wasn't made. `python -m truthtracker.cli index-statements` chunks
  and embeds anything new; the worker runs it daily after ingestion.

- **Graphical directory UI** — `/` is a portrait directory arranged by
  government structure: President and VP, the Cabinet in statutory
  line-of-succession order (acting officials labeled), the Supreme Court by
  seniority, and the Texas delegation by district — vacant seats shown
  honestly as vacant. Each portrait opens a figure page with stat tiles,
  recent roll-call votes (verbatim positions, primary-source links), recent
  verbatim statements, role history, and a claim search scoped to that
  figure. Portraits are public-domain congressional photographs downloaded
  locally by `fetch-portraits` with a provenance manifest (source URL,
  sha256, fetch time); figures without a canonical portrait source render as
  neutral monograms. Party is displayed as text — no partisan color coding
  anywhere.

- **Step 4** — campaign finance + the joined record: openFEC adapter ingesting
  per-cycle candidate-committee totals (committee level ONLY — no itemized
  contributor data, per 52 U.S.C. 30111(a)(4)); figure pages now show one
  chronological activity stream where statements and votes interleave, plus a
  campaign-finance section linking each cycle to FEC.gov. `ingest-finance`
  runs weekly via the worker. `scripts/backfill.py` backfills the full 119th
  Congress (votes + Congressional Record + embeddings); it is idempotent and
  resumable, and logs to `backfill.log`.

- **Step 5** — executive + judicial statement sources: Supreme Court opinions
  via CourtListener (author-attributed via structured person records, hard
  request-budget guard for the 50/hr limit, supremecourt.gov PDFs as primary
  sources) and presidential documents via the Federal Register API (executive
  orders, proclamations, memoranda — attributed to the signing president).
  Coverage reporting is now per-source everywhere. Promise tracking (verbatim
  quotes + evidence + attributed editorial assessments) lives on every profile.

- **Step 6** — topics + position drift: taxonomy is the CRS policy areas
  Congress.gov assigns to bills (votes join factually through their bill).
  Statements are tagged by two labeled methods — `bill_reference` (the
  statement cites a bill; that bill's policy area, confidence 1.0) and
  `embedding_similarity_v1` (thresholded, ≤2 tags) — recomputed daily, always
  displayed as machine navigation aids with method and confidence, never as
  record. Per-figure topic pages show statements beside votes oldest-first
  (`/figures/{slug}/topics/{id}`), with per-topic vote tallies.
- **Transparency layer** — accountability records (documented public events
  only, presumption-of-innocence labeling), STOCK Act filing indexes with
  official PDF links, promise tracking with attributed editorial assessment.

**Phase 1 build order: complete.** Future work: Whisper video adapter
(phase 2), transaction-level PTR parsing, RECAP docket tracking, Voteview
DW-NOMINATE as a cited source, editor authentication before public deploys,
CourtListener bulk data for historical SCOTUS backfill (REST budget is 125/day).

## Ingestion commands

```bash
python -m truthtracker.cli seed-figures
python -m truthtracker.cli ingest-crec --start 2026-07-01 --end 2026-07-16
python -m truthtracker.cli ingest-house-votes [--limit N]
python -m truthtracker.cli ingest-senate-votes [--limit N]
python -m truthtracker.cli enrich-bills [--limit N]
```

All adapters are idempotent (re-runs skip ingested units) and record every run
in `ingestion_run`; CREC coverage is tracked per daily issue in
`source_package`. The worker container runs these daily (12:15–13:15 UTC).

## Stack

Python 3.12 · FastAPI · PostgreSQL 16 + pgvector · SQLAlchemy 2 + Alembic ·
Docker Compose. Embeddings (build step 3) will use bge-small-en-v1.5 (384-dim)
running locally — no cloud dependencies.

## Quickstart (development)

```bash
cp .env.example .env        # then fill in keys
docker compose up -d db     # Postgres 16 + pgvector

uv venv --python 3.12 .venv
.venv\Scripts\activate       # Windows
uv pip install -e ".[dev]"

alembic upgrade head                     # create schema
python -m truthtracker.cli seed-figures  # TX delegation + exec/judicial seeds
python -m truthtracker.api               # dev server (required on Windows —
                                         # sets the selector event loop psycopg needs)
```

Then: `GET /healthz`, `GET /figures`, `GET /figures/{slug}`.

## Full stack (deployment target)

```bash
docker compose up -d        # db + api + worker
```

## Layout

```
src/truthtracker/
  config.py          settings from .env
  db.py              sync + async engines/sessions
  models/            SQLAlchemy models (schema of record)
  api/               FastAPI app
  ingestion/         source adapters + provenance helpers
  cli.py             ingestion CLI (seed-figures, ...)
alembic/             migrations
seeds/               manually verified executive/judicial figure data
```

## Data sources & rate limits (Phase 1)

| Source | Auth | Limit |
|---|---|---|
| Congress.gov API | api.data.gov key | 5,000 req/hr |
| GovInfo API | api.data.gov key | 1,000 req/hr |
| FEC API | api.data.gov key | 1,000 req/hr (7,200 on request) |
| CourtListener | token | 5/min, 50/hr, 125/day — bulk data for backfills |
| Federal Register | none | 2,000-result pagination cap per query |
| congress-legislators YAML | none | — |

Legal: FEC individual-contributor data must never be used for solicitation or
commercial purposes (52 U.S.C. 30111(a)(4)). Phase 1 stores committee-level
summaries only.

## Security note for shared deployments

The editing surface (promises, accountability records) supports single-editor
password auth: set `EDITOR_PASSWORD` (and ideally `SECRET_KEY`) in `.env` and
editing requires login at `/login`. With no password set, editing is open —
acceptable only for local development. `ENABLE_EDITING=false` turns the
editing surface off entirely. Always deploy behind TLS before logging in over
an untrusted network.

## Contributing

Bug reports and especially **data-accuracy reports** (anything displayed that
does not match its linked primary source) are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[AGPL-3.0](LICENSE). If you run a modified public instance, you must make
your modified source available to its users — for an accountability tool,
that transparency requirement is the point.
