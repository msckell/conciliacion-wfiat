# CLAUDE.md · wFIAT Quarter Close

Instructions for AI coding agents working in this repo. Read this first, then `STATUS.md` for the current state. `README.md` explains the project for humans.

## What this repo is

An agent that prepares the onchain side of the quarterly closing package for Ripio's wFIAT stablecoins (wARS, wBRL, wMXN, wCOP, wCLP, wPEN). For every token and EVM network it computes the supply at the cutoff, lists every mint and burn with its transaction, reconciles opening + mints − burns = closing exactly, and notifies Finance on Slack with a link to the published package.

Independent demo, not an official Ripio tool. Public data only: the blockchains and the certificates Ripio publishes.

## Commands

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                                 # offline, recorded fixtures, golden tests included
uv run cierre golden                          # computed vs certified, every rule
uv run cierre close --cutoff 2026-09-30       # engine + package for a quarter
uv run cierre close --opening 2026-07-31 --cutoff 2026-08-31   # any interval
uv run cierre run-close --cutoff 2026-09-30   # the whole close, logged step by step
uv run cierre monitor                         # daily monitor
uv run cierre site                            # JSON for the page
cd site && npm ci && npm run lint && npm run build
```

`uv run cierre --help` lists every subcommand. CI (`.github/workflows/ci.yml`) runs ruff, pytest, and the site lint and build. Run the same before you commit.

## Layout

- `config/`: networks (`chains.yaml`), tokens (`tokens.yaml`), certificates, contracts, and the methodology with its labels (`methodology.yaml`).
- `pipeline/cierre/`: the deterministic engine.
  - `rpc.py`, `explorer.py`, `cache.py`, `blocks.py`, `cutoffs.py`, `supply.py`: data access.
  - `scope.py`, `ledger.py`, `classify.py`, `bridge.py`, `close.py`: expected scope, mints and burns, classification, bridge pairing, reconciliation.
  - `golden.py`, `references.py`, `refresh.py`: comparison against the published certificates.
  - `package.py`, `slack.py`, `tasks.py`, `site.py`, `monitor.py`, `close_run.py`, `runlog.py`: outputs and orchestration.
  - `agent/`: the LLM steps (`memo.py`, `exceptions.py`, `extract.py`), the verifier (`verifier.py`), read only tools (`tools.py`) and the single entry point to Claude (`llm.py`). Prompts live in `agent/prompts/`.
- `data/`: committed outputs read by the site. `data/golden/certifications.json` is the confirmed reference table.
- `site/`: static Vite + React + TypeScript + Tailwind page. It reads only from `data/`.
- `tests/`: pytest, offline.

## Rules that must not be broken

**Numbers**
- Every figure, status and verification conclusion comes from the deterministic engine and is rendered by templates. The LLM only writes explanations, one token at a time, citing facts by ID (`{wARS.outstanding.2026-09-30}`). Never let the LLM type a number.
- Amounts are integer base units and `Decimal`. Never `float`. Round only when displaying.
- A failed query is never a zero. If a source fails, say so and stop. Never present an estimate as data.
- Every pair is computed by two independent paths: `totalSupply()` at the cutoff block, and opening + mints − burns. If they disagree, stop and report.
- An opening balance of zero is valid only when the contract was created after the previous cutoff.

**Method**
- One rule must reproduce every published certificate. A rule tuned per token or per period is overfitting and is rejected.
- Every methodology decision carries a label: documented (with its source), inferred, or hypothesis. Keep them in `config/methodology.yaml`.
- Exclusions (treasury, bridge escrow) need external evidence. A better match is never enough.
- Classify a movement only with evidence. If it cannot be proven, it stays unclassified and becomes a task for a person.
- Keep raw sum, documented adjustments and the resulting figure in separate fields.

**Verifier**
- `agent/verifier.py` rejects any raw figure, any fact ID outside the token and period in scope, and banned claims ("todas las redes", "auditado", "respaldo", "cobertura"). Add a case to `verifier_cases.py` and a test whenever you change what it accepts.

**Publishing**
- Order: compute → verify → save the package → confirm the published file is reachable → notify Slack with the link. If any step fails, Slack gets an alert with no link.
- Never compute or show a collateral coverage ratio. Collateral appears only as stated in published certificates.
- No personal names from the certificates ("contador público"). No findings and no accusations.
- If a result suggests an error in a published certificate, publish nothing and tell the maintainer.
- The page keeps `noindex,nofollow` and a `robots.txt` that disallows everything. No Ripio logo, Ripio purple or Geist font on the page. The domain must not contain "ripio".

**Repo**
- Never commit secrets. Keys live in `.env` (gitignored) and in GitHub Actions secrets. Update `.env.example` when you add a variable.
- No destructive git operations (force push, history rewrite) without explicit approval.
- Respect the rate limits of public RPCs. Historical blocks never change, so their responses are cached on disk for good.

## Conventions

- Code, comments, commit messages and README: English.
- Everything Finance reads (page, Slack, Excel labels, memo): Spanish with voseo ("revisá", "incluila").
- That prose uses no dashes and no semicolons. Write "onchain" and "end to end".
- Wording: "redes revisadas", never "todas las redes". Say what was checked, not more than was proven.
- Every number on the page is read from `data/`. None is hardcoded.

## Known pitfalls

- The code does not load `.env` by itself. Load it with `set -a; . ./.env; set +a`.
- The daily monitor commits to `main` at 12:00 UTC. Pull before you push.
- `data/closes/<cutoff>/slack_payload.json` is the record of what was sent. Do not regenerate it.
- `cierre slack --excel-url` must be the published Excel URL, or the payload changes.
- Blockscout returns incomplete logs on Base, and the Gnosis Blockscout API redirects to gnosisscan.io. Public BNB endpoints refuse wide log ranges, so BNB uses NodeReal when `NODEREAL_API_KEY` is set. The free Etherscan plan does not cover Base, BNB or Gnosis.
- HyperEVM returns a different `log_index` for the same log depending on the node.
- Orchestration tests must stub `close_run.write_scope`.

## When you finish a task

Rewrite `STATUS.md` (do not append to it): what is done, what is next, open questions. It must fit on one screen.

`docs/original-spec.md` is the original build specification, kept for reference only. This file and the code take precedence over it.
