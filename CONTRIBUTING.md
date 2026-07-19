# Contributing

Thanks for your interest. This project has one governing principle that shapes
every contribution: **the tool presents records, never conclusions.** Every
displayed fact links to a primary source, absence of evidence is reported
honestly, and nothing is ever generated or editorialized by the software.

## Running it locally

See the Quickstart in the README. Short version: Docker Compose for Postgres,
a Python 3.12 venv, `alembic upgrade head`, `seed-figures`, then the ingestion
commands. You will need a free api.data.gov key. All adapters are idempotent —
re-running them is always safe.

## Reporting issues

Two kinds of reports are especially valuable:

- **Data accuracy** — the tool displays something that does not match its
  linked primary source (misattributed statement, wrong vote position, bad
  date, broken source link). Use the "Data accuracy" issue template and
  include the page URL and the primary-source URL. These are treated as the
  most serious class of bug.
- **Bugs** — crashes, ingestion failures, search problems. The `ingestion_run`
  ledger (surfaced at `/healthz`) usually has the error message.

## Contributing code

- Match the existing style; `ruff check src scripts tests` and
  `pytest tests` must pass.
- New source adapters must follow the rules in `ingestion/base.py`: store the
  raw payload, log every run to the ledger, upsert idempotently on
  source-native IDs, and fail loudly.
- Displayed text must be a verbatim extract of a stored payload. Parsers that
  extract statements must enforce this mechanically (see
  `crec_parser.extract_turns` for the pattern) and ship a fixture test.
- Anything interpretive (topic tags, similarity scores) must carry its method
  and confidence and be labeled as machine-computed in the UI.
- Strict neutrality in copy and presentation. Identical treatment of all
  figures and parties. No partisan color coding.

## Legal constraints to respect

- FEC individual-contributor data must never be used for solicitation or
  commercial purposes (52 U.S.C. 30111(a)(4)). This project stores only
  FEC-computed aggregates; keep it that way.
- Non-government sources: store links and metadata, not full text.
