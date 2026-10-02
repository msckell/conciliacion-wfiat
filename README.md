# Cierre trimestral wFIAT

Independent demo for Ripio, by Máximo Sckell. **Not an official Ripio tool.** It uses only public data: the blockchains and the certificates Ripio publishes.

Live page: https://conciliacion-wfiat.vercel.app (Spanish, no login)

## What and why

Ripio issues local currency stablecoins (wFIAT: wARS, wBRL, wMXN, wCOP, wCLP, wPEN) on several EVM networks. After each quarter end a public accountant certifies that the tokens outstanding at the cutoff are fully backed. Before that, someone in Finance has to prepare the onchain side for every token and every network:

1. the supply at the cutoff
2. every issuance and burn, with its transaction ID
3. the reconciliation: opening supply + mints − burns = closing supply

By hand this means one explorer and one spreadsheet per network, with four typical risks: missing a new network, counting a bridge transfer as new issuance, using the wrong cutoff time and float rounding.

This repo is an agent that prepares that package by itself the day after the close, proves its method against the published certificates, investigates what it cannot classify and tells Finance on Slack what changed and what needs a person.

## How it works

```
public sources ──► deterministic engine ──► agents (Claude) ──► verifier ──► outputs
RPC nodes,          cutoff block, supply,    exception agent,    rejects raw     Slack, GitHub
explorers,          mints and burns,         memo writer         figures, foreign issues, page,
certificates        exact reconciliation                         facts, claims   Excel package
```

* **The engine computes every figure.** Integer base units and `Decimal`, never float. Each network is computed by two independent paths: `totalSupply()` at the cutoff block, and the opening supply plus every mint minus every burn. If they disagree the close stops.
* **The LLM never types a number.** It writes explanations for one token at a time and cites facts by ID, for example `{wARS.outstanding.2026-09-30}`. The code replaces each ID with the formatted figure. A verifier rejects raw figures (digits or words), fact IDs from another token or period, invented IDs and banned claims, retries twice with feedback and then falls back to the template only text. Every attempt is logged.
* **The exception agent** takes each movement the engine left unclassified. It reads the transaction, checks minting permissions and searches the other networks for the other side of a bridge transfer. Code checks the evidence (amounts, hashes, roles, timing) before accepting a proposal. What it cannot prove becomes a GitHub issue for a person.
* **Publication order** on every run: compute, verify, commit the package, wait until the site serves that exact Excel file, open the issues, then notify Slack with the link. If any step fails, Slack gets an alert with no link.

## Verification against the published certificates

One single rule for every row: the sum of `totalSupply()` on the reviewed networks, no adjustments, at 23:59:59 Buenos Aires time, compared in whole tokens.

| Token | Cutoff | Sum of contracts | Certified | Difference |
|---|---|---:|---:|---:|
| wARS | 31/03/2026 | 973.000.960,20 | 973.000.960,19 | 0 |
| wBRL | 31/03/2026 | 719.447,50 | 719.448 | 0 |
| wCOP | 31/03/2026 | 104.898.653,80 | 104.898.654 | 0 |
| wMXN | 31/03/2026 | 518.688,69 | 518.689 | 0 |
| wARS | 30/06/2026 | 7.619.996.077,04 | 7.619.996.077 | 0 |
| wBRL | 30/06/2026 | 2.586.147,50 | 2.586.148 | 0 |
| wMXN | 30/06/2026 | 968.788,69 | 968.789 | 0 |
| wCOP | 30/06/2026 | 196.398.753,80 | 196.398.754 | 0 |
| wCLP | 30/06/2026 | 64.295.100,00 | 64.295.100 | 0 |

**9 of 9 match.** The certified figures were extracted from the PDFs, confirmed by hand and frozen in `data/golden/certifications.json` (document URL, page, quoted text, concept, cutoff and printed precision). The page keeps three layers apart: the raw sum, each adjustment (none) and the result next to the certified figure. The daily monitor recomputes all nine from the network every day.

## Methodology labels

Every decision is labeled **documented** (with its source), **inferred** (its only evidence is that it reproduces the certified figures) or **hypothesis**. The full list, with sources, is on the page and in the Excel package.

* Documented: token addresses confirmed onchain with `name()`, `symbol()` and `decimals()`. Mints and burns are ERC20 transfers from and to the zero address. The reconciliation includes every supply change, bridge movements too. Two independent paths per network. Zero opening balance only when the contract was created after the previous cutoff. Bridge pairs matched by source chain, source hash and deposit number. Primary issuance and redemption identified through the `LimitedMinter` contract and `MINTER_ROLE`. Anything else stays unclassified.
* Inferred: outstanding supply is the plain sum of `totalSupply()` with no wallet excluded. Comparison in whole tokens. Cutoff at 23:59:59 Buenos Aires time (for the certified cutoffs, Buenos Aires and UTC give the same result).
* Hypothesis: wUYU is left out. It exists on the same networks, but a public factory created it and there is no evidence that it belongs to Ripio.

## The 2026-09-30 close

54 of 54 token and network reconciliations close with zero difference, across 9 networks with contracts out of 24 reviewed. Arc is flagged as a new network with its creation block as evidence. The exception agent investigated 9 movements, proved 4 with evidence and left 5 as tasks. The package is `data/closes/2026-09-30/paquete_cierre_2026-09-30.xlsx` (sheets Resumen, Por red, Emisiones y quemas, Conciliación, Metodología).

## In production

* `ci.yml` on push and PR: ruff, offline pytest (the golden tests included), site lint and build. Commits that only touch `data/` are skipped.
* `daily.yml` at 12:00 UTC: supply per network, new deployments on the 24 candidate networks, live recomputation of the nine certificates and CI status. Slack only on change or failure. It also prefetches the running quarter's logs so the close only reads the last stretch.
* `close.yml` on the day after each quarter end, or by hand with a cutoff: the whole close in the order above. An `engine-check` mode runs only the engine and the package.
* The page is static (Vite, React, TypeScript, Tailwind) and reads the JSON that CI commits. Every number on it comes from `data/`.

## How to run

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and the [Claude Code](https://claude.com/claude-code) CLI logged in for the agent steps.

```sh
cp .env.example .env              # optional keys, all free tiers
uv sync
uv run pytest                     # offline, with recorded fixtures
uv run cierre golden              # computed vs certified, every rule
uv run cierre close --cutoff 2026-09-30
uv run cierre run-close --cutoff 2026-09-30   # the whole close, logged step by step
uv run cierre monitor
uv run cierre site                # JSON for the page
cd site && npm install && npm run dev
```

Without `SLACK_WEBHOOK_URL` the Slack message is written to `data/closes/<cutoff>/slack_payload.json` (dry run). The LLM runs through Claude Code in headless mode (`claude -p`), on a Claude subscription. In CI it uses `CLAUDE_CODE_OAUTH_TOKEN`.

## Design decisions

1. Scope is the onchain side only. Collateral appears only as stated in published certificates.
2. Numbers come only from the deterministic engine. The LLM writes explanations and a verifier checks every text.
3. Matching the certificates is not enough: one rule must reproduce all of them, exclusions need external evidence and every decision carries its label.
4. The machine proposes and a person decides. Unproven cases become tasks, never guesses.
5. Static site plus versioned JSON. No backend, no database, no live LLM on the page.

## Limitations

* EVM networks from a written candidate list only. The page says "redes revisadas", never "all networks".
* Free public sources only. Some nodes rate limit CI runners, so the engine retries across several endpoints and caches immutable responses.
* BNB Smart Chain has no free log API. The 2026-09-30 close uses a transaction list exported from BscScan. The next close needs another free source or an updated list.
* Gnosis reconciles over RPC, but its second comparison source (Blockscout) now asks for an API key.
* Bridge transfers in flight at the cutoff are listed for review using a window based on observed bridge delays. That is a preventive review, not proof that every pending transfer was found.
* No bank collateral and no coverage ratios for cutoffs without a published certificate.

## License

MIT. See `LICENSE`.
