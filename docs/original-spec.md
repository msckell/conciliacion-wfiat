# Original build specification · wFIAT Quarter Close

> Kept for reference. This is the specification the project was built from, phase by phase, with checkpoints reviewed by the owner. It is superseded by [CLAUDE.md](../CLAUDE.md) and the code: file names, steps and some decisions differ in the final repo.

---

## 0. How we work

- Code, comments, commit messages and README: **English**.
- Everything a Ripio employee would read (web page, Slack messages, Excel labels, closing memo): **Spanish with voseo** ("revisá", "incluila"), the same register Ripio uses in its own public writing.
- That prose uses **no dashes and no semicolons**. Write "onchain", "end to end".
- Every phase ends in a **CHECKPOINT** that the owner verifies on a real output (a table, a file, a page). **Stop at every checkpoint and wait for approval.**
- One phase at a time. Commit at the end of each phase.
- At the end of each phase, rewrite `STATUS.md` instead of appending to it. It says what is done, what is next and what questions are open, and it must fit on one screen.
- Write that summary as soon as the phase work is done, **before** the checkpoint is approved. Keep it short, but spell out the details that matter (decisions taken, sources that failed, traps for the next session). A new session must be able to continue from the startup doc + `STATUS.md` alone.

---

## 1. Context: the problem we solve

The goal is to automate a manual process of a staff area with LLM agents and bots that connect to Slack and third party services, ship with CI/CD and monitoring, and must provably not make mistakes.

Ripio is an Argentine crypto company that sells B2B infrastructure in 7 LATAM markets. It issues local currency stablecoins called **wFIAT**: wARS, wBRL, wMXN, wCOP, wCLP and wPEN. A wUYU is listed on one Ripio page but is unconfirmed. Each token is backed 1:1 by fiat held in bank accounts.

After each quarter end, a public accountant certifies that the tokens outstanding at the cutoff are fully backed. Published so far:
- cutoff 2026-03-31: wARS, wBRL, wCOP, wMXN (signed 2026-04-22)
- cutoff 2026-06-30: wARS, wBRL, wMXN, wCOP, wCLP (signed 2026-07-28)

The certificates say that management prepared the information. The accountant then reconciled the token count against the onchain issuance and burn reports, the transaction ID listing of each issuance and burn, and the bank statements.

So every quarter someone in Ripio's Finance team (a staff area) has to prepare three things for each token and each network:
1. the supply at the cutoff
2. the list of every issuance (mint) and burn, with its transaction ID
3. the reconciliation: opening supply + mints − burns = closing supply

This work grows fast:
- wARS outstanding went from 973,000,960.19 (2026-03-31, 6 networks) to 7,619,996,077 (2026-06-30, 8 networks).
- Arc mainnet launched on 2026-09-16 with six wFIAT tokens, so the 2026-09-30 close (Q3) is the first one that includes Arc.

Doing this by hand carries four typical risks:
- missing a new network
- counting a bridge transfer (burn on one chain, mint on another) as new issuance
- using the wrong cutoff time
- float rounding errors

**What this repo builds:**
- An agent that prepares the onchain side of the quarterly closing package by itself.
- It proves its method by reproducing the token counts in the published certificates.
- An exception agent investigates the movements the engine cannot classify. It proves what it can with evidence and turns the rest into tasks for a person.
- It notifies Finance on Slack, with a link to a deliverable that is already published.
- A daily monitor watches a written list of networks for new deployments and reports failed runs.

---

## 2. Who reads the result and what success looks like

- **Reader 1: non technical, 30 seconds.** Must understand what it is, see that it is about Ripio and see that it works. The first view is in plain Spanish with no jargon.
- **Reader 2: engineering lead.** Checks the correctness method, CI/CD, monitoring, code quality and security hygiene.

Success means the page opens instantly, with no login and no API key, and shows:
1. What the agent did by itself in its last run, as a timeline.
2. "Coincide con la cantidad de tokens certificada por el contador en N de las 9 certificaciones publicadas", with the true N (ideally 9). Any certificate that does not match shows its status and explanation.
3. The 2026-09-30 close ready to review, with the Excel package to download.
4. The Slack message Finance would receive, with the tasks it has to review.
5. A "Cómo funciona" view with the verification table, the methodology, the exception agent log, the AI verifier log, CI status and run history.

---

## 3. Decisions (do not re-litigate)

1. **Scope is the onchain side only.** Never compute or display a collateral coverage ratio for a cutoff that has no published certificate. Collateral appears only as stated in published certificates.
2. **Numbers come only from the deterministic engine, and the LLM never types them.**
   - Templates render every figure, every status ("conciliado", "red nueva") and every verification conclusion.
   - The LLM only writes explanations. It works on one token at a time, sees only that token's facts and cites them by fact ID.
   - A verifier rejects any raw figure, any fact ID outside that token's scope and any banned claim (Phase 2).
3. **Integer base units (wei) and Decimal.** Never float. Round only when displaying.
4. **The published certified figures are the golden tests, but matching them is not enough.**
   - A single rule (cutoff time convention, networks, exclusions) must reproduce all of them. A rule tuned per token is overfitting and is not accepted.
   - Every methodology decision is labeled **documented** (with its source), **inferred** (its only evidence is that it matches N of 9) or **hypothesis**. Trying both time conventions is fine as an investigation, and the result is reported as inferred.
   - Exclusions (treasury, bridge escrow) need external evidence. A better match is never enough.
   - Always keep three layers apart: the raw sum of contract `totalSupply()`, each documented adjustment, and the resulting figure next to the published one.
5. **The machine proposes, the human decides.**
   - In Phase 0, before anything is compared against them, the figures are extracted from the PDFs and the owner confirms them.
   - Each reference keeps document URL, page, quoted text, concept (for example "tokens outstanding"), cutoff date and printed precision.
   - Once confirmed, they are frozen in `data/golden/certifications.json`.
6. **Static site plus JSON data committed by CI.** No backend server, no database and no live LLM on the public page.
7. **Stack.**
   - Pipeline: Python 3.12 with uv.
   - Site: Vite + React + TypeScript + Tailwind.
   - Hosting and automation: Vercel for the site, GitHub Actions for CI and schedules.
   - LLM: Claude Code in headless mode (`claude -p`, JSON output), called from Python, so it runs on a Claude subscription instead of API billing. Model from env `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). Locally it uses the `claude` login. In GitHub Actions it uses `CLAUDE_CODE_OAUTH_TOKEN`, created with `claude setup-token`.
   - Dependencies: keep them minimal (httpx, pyyaml, pypdf, openpyxl, pytest, ruff).
   - Chain calls: raw JSON-RPC is enough (totalSupply selector `0x18160ddd`, ERC20 Transfer topic). Use web3.py only if it truly saves time.
8. **Free data access only.** Use public RPCs or free tiers (Etherscan API V2, Alchemy, dRPC, Blockscout). No paid plans. Secrets live only in `.env` (gitignored) and in GitHub Actions secrets. Commit a `.env.example`.
9. **Not official, not branded.**
   - Title "Cierre trimestral wFIAT", subtitle "Demo independiente para Ripio, por Máximo Sckell".
   - A visible disclaimer.
   - No Ripio logo, colors or fonts on the page or in Slack. Exception: the Excel package carries light Ripio branding (logo and purple), and keeps the "No es una herramienta oficial" disclaimer.
   - Exception: the page shows the wFIAT token icons, as CoinGecko and the explorers do. Still no Ripio logo, no Geist font and no Ripio purple (#7908ff) on the page.
   - Style: a fintech look in the same family as Ripio (deep navy, one indigo accent, Inter), so the demo is not out of line with them, without copying their identity.
   - The domain must not contain "ripio".
10. **Sensitivity.**
    - Add a `noindex,nofollow` meta tag and a `robots.txt` that disallows everything.
    - Do not show personal names from the certificates. Say "contador público".
    - No "findings" and no accusations.
    - **If a result suggests an error in a published certificate, publish nothing. Stop and tell the owner.**
11. **The repo stays private while building.** It becomes public at the end, after a secret scan, and only if the owner decides so.
12. **Publication gate and order.**
    - 8 of 9 is enough to keep working, not to publish. Every certified figure that does not match needs a status and an explanation before anything is public, and the owner decides.
    - Every run follows this order: compute → verify → save the package → confirm the published file is reachable → notify Slack with a link to it. If any step fails, Slack gets an alert with no link.

---

## 4. Known facts (checked 2026-10-01, verify again onchain)

**Token contracts**
- wARS: `0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D` (Base, verified ERC1967Proxy, 18 decimals). Expected at the same address on the other EVM chains: verify.
- wBRL: `0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0` (Ethereum per the wFIAT whitepaper, also seen on Base and World Chain).
- For the rest, try these sources:
  - the CoinGecko API: `/coins/{id}` returns `platforms`, and the wARS id is `argentine-peso`
  - the explorers
  - Ripio's public Dune dashboards (`dune.com/ripio_team/wars`, `/wbrl`, `/wcop`, `/wmxn`, `/wpen`)
  - the bridge front end (bridge.ripio.com)
- Confirm every address onchain with `name()`, `symbol()` and `decimals()`.

**Networks**
- The March certificates name Ethereum, World Chain, Base, Polygon, Gnosis and BNB Smart Chain.
- The June certificates add HyperEVM and Celo.
- Arc has been on mainnet since 2026-09-16.

**Contract design reference:** `github.com/ripio/latam-stables`.
- LatamStable: MINTER_ROLE.
- LimitedMinterBridge: daily mint caps per token.
- BridgeDeposit: burn and mint. `depositForBridge()` burns on the source chain, then an operator calls `fulfillBridgeMint()` on the destination chain.
- The deployed versions may differ, so read the verified source on the explorers.

**Certificates (inputs for the golden tests)**
- Index: https://action.ripio.com/en/wfiat-attestations and https://www.ripio.com/en/cryptos/local-stablecoins
- 2026-03-31:
  - https://action.ripio.com/hubfs/2026/wFIAT/ATTESTATION/20260331_wARS_Certification.pdf
  - https://action.ripio.com/hubfs/2026/wFIAT/ATTESTATION/20260331__wBRL__Token-Certification.pdf
  - https://action.ripio.com/hubfs/2026/wFIAT/ATTESTATION/20260331__wCOP__Token-Certification.pdf
  - https://action.ripio.com/hubfs/2026/wFIAT/ATTESTATION/20260331__wMXN__Token-Certification.pdf
- 2026-06-30 (replace TOKEN with wARS, wBRL, wMXN, wCOP and wCLP):
  - https://action.ripio.com/hubfs/2026/wFIAT/ATTESTATION/Aug%202026/Token%20TOKEN%2006.30.2026%20Certification%20vf.pdf
- Figures seen so far. **Confirm them from the PDFs, do not trust them:**

  | Token | Cutoff | Figure |
  |---|---|---|
  | wARS | 2026-03-31 | 973,000,960.19 |
  | wARS | 2026-06-30 | 7,619,996,077 |
  | wBRL | 2026-03-31 | 719,448 |
  | wCOP | 2026-03-31 | 104,898,654 |
  | wMXN | 2026-03-31 | 518,689 |

**Unknowns that Phase 0 must resolve**
- Cutoff time convention: 23:59:59 America/Argentina/Buenos_Aires or 23:59:59 UTC. Test both and report the result as inferred. If no movement falls between the two times, say that the convention makes no difference for that cutoff.
- Whether "outstanding" is the sum of `totalSupply()` across networks, or excludes wallets the issuer controls (treasury, bridge escrow). Any exclusion must be the same for every token and backed by external evidence.
- Precision: compare at the precision printed in the certificate (2 decimals or whole units). Choose rounding or truncation once, for all tokens.
- Whether the free sources return the **complete** mint and burn history on every network. Old log ranges are the usual weak point of free RPCs, and a readable balance does not prove we can build the package.
- The contract creation block on each network. An opening balance of zero is valid only when the contract was created after the previous cutoff, and it must never come from a failed query.

---

## 5. Architecture

```
cierre-wfiat-demo/
├── CLAUDE.md  STATUS.md  README.md  LICENSE (MIT)  .env.example
├── config/
│   ├── chains.yaml    name, chain_id, rpc_urls[], explorer tx/block/address URL templates
│   └── tokens.yaml    symbol, name, decimals, address and creation block per chain, coingecko_id
├── pipeline/cierre/
│   ├── rpc.py         JSON-RPC client: several endpoints per chain, retries, rate limit, disk cache
│   ├── blocks.py      block at timestamp (binary search, cached)
│   ├── supply.py      totalSupply at block
│   ├── ledger.py      mints and burns from Transfer logs (from/to zero address), chunked and classified
│   ├── reconcile.py   per network and total: opening + mints − burns == closing, exact
│   ├── discover.py    probes the written candidate list, records creation blocks and networks checked
│   ├── package.py     package.json for the site + Excel for the accountant
│   ├── golden.py      computed vs certified, three layers, aware of the printed precision
│   ├── agent/
│   │   ├── extract.py     certificate PDF → structured figures, scored against the confirmed golden table
│   │   ├── memo.py        templates for figures and statuses, LLM explanations by fact ID
│   │   ├── verifier.py    rejects raw figures, out of scope fact IDs and banned claims, retries, fallback
│   │   └── exceptions.py  investigates unclassified movements with tools, proposes with evidence
│   ├── tasks.py       GitHub issues for what needs a person
│   ├── slack.py       Block Kit via Incoming Webhook (dry run writes the payload to a file)
│   └── cli.py         discover · golden · close --cutoff YYYY-MM-DD · monitor · publish
├── data/              committed by CI, read by the site
│   ├── golden/certifications.json
│   ├── closes/2026-09-30/  package.json · memo.json · attempts.jsonl · paquete_cierre_2026-09-30.xlsx
│   ├── monitor/latest.json · history.jsonl
│   └── runs.jsonl
├── site/              Vite + React + TS + Tailwind, reads static JSON
├── tests/             pytest, offline, with recorded RPC fixtures
└── .github/workflows/ ci.yml · daily.yml · close.yml
```

- Compute supply by two independent paths. One is `totalSupply()` at the cutoff block. The other is the opening supply plus every mint minus every burn. If they disagree, stop and report.
- Historical blocks never change, so cache their responses on disk forever.

---

## 6. Phases and checkpoints

Phase 0 decides how much the project can promise, so do not rush it.

### Phase 0 · Go or no go

Phase 0 must prove three things: **enough data access, confirmed references and a reproducible method.**

**Before writing any code, explain in 5 lines how you will get the data for each network, for both balances and the full movement history.**

1. Repo skeleton, uv, `.env.example`, config files. `git init` and a first commit.
2. Find the address of every token on every candidate network. Confirm each one onchain and record its contract creation block.
3. **References first.**
   - Download the 9 certificates and extract their figures (pypdf plus regex is enough here).
   - For each one, record document URL, page, quoted text, concept, cutoff date and printed precision.
   - Show the owner the table. Once confirmed, freeze it in `data/golden/certifications.json`.
4. A minimal `blocks.py` and `supply.py`. Compute the outstanding supply at each cutoff with both time conventions. Keep the raw `totalSupply()` sum, any documented adjustment and the result in separate columns.
5. **Movement history test.** This proves the free sources can build the package, and it is also the two path check.
   - Use wARS on every network where it exists, and add one other token for any network wARS does not cover.
   - Fetch the complete list of mints and burns between the 2026-03-31 and 2026-06-30 cutoff blocks.
   - Check that opening + mints − burns == closing, exactly, per network. For networks that did not exist yet at the opening cutoff, start from the creation block.
   - Arc did not exist in that quarter, so test it from its creation block to the 2026-09-30 cutoff.
6. Write in `STATUS.md`:
   - the result table: token · cutoff · certified · raw sum · adjustments · computed · difference · match · networks included
   - the movement test per network: passed or failed, and which source was used
   - the methodology list, each item labeled documented, inferred or hypothesis

**CHECKPOINT 0, stop here.** Show the owner the three results.
- **Go** needs two things: the movement test passes on every network in scope, and at least 8 of the 9 figures match under one single rule.
- A figure that does not match needs a status and an explanation before anything is published (decision 12).
- If the go conditions are not met, diagnose briefly (a missing network, excluded wallets, the time convention, decimals, log ranges), then report the options.
- Do not continue without approval.

### Phase 1 · The 2026-09-30 close

1. `ledger.py`:
   - Record every mint and burn with chain, block, timestamp, tx hash, amount and explorer link.
   - Classify each one as primary issuance, bridge or unclassified, using the minter address or the transaction target.
   - Never guess. If it cannot be determined, mark it unclassified.
2. `reconcile.py`: opening supply (2026-06-30 cutoff block) + mints − burns == closing supply (2026-09-30 cutoff block). Per network, exact in base units.
   - **Every movement that changes supply goes in, bridge movements included.**
   - The classification only adds context, for example net primary issuance.
3. `discover.py`:
   - Find the networks where each token is deployed. Check them against a written list of candidate networks, and cross check with CoinGecko `platforms` and the networks named in the certificates.
   - Flag networks that are new since the previous close (Arc is expected). Their opening balance is zero only if the contract creation block is after the previous cutoff, and the package shows that creation block as evidence.
   - Record every network checked. The package and the page say "redes revisadas", never "todas las redes".
4. Bridge transfers in flight at the cutoff (burned on one chain, not yet minted on the other):
   - Match them through the bridge events if they allow it.
   - Otherwise, list the mints and burns near the cutoff as "revisar", using a window based on the bridge delays observed in past matched pairs.
   - In that second case, the package must say this is a preventive review, not proof that every pending transfer was found.
5. `package.py`:
   - `package.json` plus an Excel file with the sheets **Resumen · Por red · Emisiones y quemas · Conciliación · Metodología**.
   - Spanish labels, block numbers, timestamps and explorer links.
   - The Metodología sheet lists each decision with its label (documented, inferred or hypothesis).

**CHECKPOINT 1, stop here.** The owner opens the Excel and checks three things:
- the totals per token
- that Arc is flagged as new, with its creation block
- that every reconciliation difference is zero

### Phase 2 · Agents and verifier

1. `extract.py`:
   - The LLM extracts token, cutoff, tokens outstanding, collateral, certification date and networks listed from each PDF, with structured output.
   - Every extracted number must appear verbatim in the PDF text.
   - The result is scored against the golden table confirmed in Phase 0. That makes this step an eval, not the source of truth.
2. `memo.py`:
   - Templates render every figure, every status and every verification conclusion.
   - The LLM writes only the explanations, in Spanish with voseo, one token at a time. It sees only that token's facts and cites them by fact ID, for example `{wARS.outstanding.2026-09-30}`. The code replaces each ID with the formatted figure.
3. `verifier.py` rejects three kinds of problems:
   - any figure typed directly in the LLM text
   - any fact ID that does not exist or belongs to another token or period
   - any banned claim: "todas las redes", "auditado", "respaldo", "cobertura", or anything that states a conclusion the templates did not produce

   On failure it retries with feedback that names the problem, at most 2 times, then falls back to the template only version. Every attempt goes to `attempts.jsonl`.
4. `tests/test_verifier.py` covers at least 8 cases:
   - must fail: a raw figure, a fact ID from another token (the swapped token case), a fact ID from another period, an invented fact ID, "todas las redes verificadas", a coverage claim
   - must pass: a correct explanation with valid IDs, a paraphrase with no figures
5. **Exception agent** (`agent/exceptions.py`):
   - It handles every movement the engine left unclassified and every item marked "revisar".
   - It investigates with tools. It reads the transaction and its logs, then searches the other networks for the matching burn or mint within the observed bridge delay.
   - It proposes a classification with evidence (tx hashes on both sides). Code checks that amounts, hashes and timing match before accepting it. Anything it cannot prove goes to a person.
   - If the real close has no exceptions, show it working on real cases from past quarters, never invented ones.
   - Everything it does goes to the run log.
6. The Slack text: title, one line per token, alerts, "Qué te toca revisar" with the open tasks, verification result and a link to the Excel.
   Order: first what needs a person (🔍 "Se requiere tu revisión en N movimientos" with the tasks), then ✅ "Todo lo demás está conciliado y verificado" with the token lines, the new network note and the verification line. The ✅ heading only appears when every reconciliation is zero and the method matches every certificate.

**CHECKPOINT 2, stop here.** The owner reads the memo, the exception agent log and the Slack text, and approves the tone.

### Phase 3 · The page

Two views, designed for mobile first, clean and neutral, light theme, no Ripio branding.

**View "Resumen" (reader 1).** This copy is a draft, and the owner approves the final wording.
Order (replaces the order below): hero with key figures · "Cierre al ..., listo para revisión" · trust banner · "Lo que hizo el agente" · "Cómo lo hace" · Slack message · by hand vs agent · other areas · disclaimer. The hero's navy style is reused on the Excel card, on "Con el agente" and on the "Cómo funciona" header.
- Title: "Cierre trimestral wFIAT". Subtitle: "Demo independiente para Ripio, por Máximo Sckell".
- Hero: "Cada trimestre, un contador certifica que cada wARS, wBRL y demás stablecoins de Ripio están respaldadas. Para eso, alguien de Finanzas junta los datos de todas las redes. Este agente arma esa parte solo, al día siguiente del cierre."
- **"Lo que hizo el agente"**, right below the hero:
  - The timeline of the last close run, read from the run log.
  - Example of the steps it shows: it detected the close, read N networks, found X movements to review, resolved Y with evidence, left Z as tasks and notified Finance.
  - The reader must see a worker, not a dashboard.
- Trust banner, computed: "Coincide con la cantidad de tokens certificada por el contador en N de las 9 certificaciones publicadas."
- Three steps:
  1. "Cuenta los tokens de cada red a la fecha y hora del corte, con el bloque usado en cada red como prueba."
  2. "Lista cada emisión y cada quema con su comprobante."
  3. "Avisa a Finanzas por Slack qué cambió y qué le toca revisar."
- A section titled "Cierre al 30/09/2026, listo para revisión":
  - one card per token (outstanding, change vs 30/06, networks)
  - a bar split by network
  - an alert card: "Red nueva desde el último cierre: Arc", with its creation block as evidence
  - the list of networks checked ("redes revisadas")
  - a download button for the Excel
- The Slack message: a screenshot of the real one plus a faithful render.
- Two lines comparing the work by hand with the agent, where every number comes from the data and none is estimated:
  - "Hecho a mano: buscar en cada explorador, una planilla por red y el riesgo de olvidarse una red nueva."
  - "Con el agente: este cierre revisó X transacciones en Y redes para Z monedas, en N minutos, con el link de cada transacción."
- "El mismo método sirve en otras áreas":
  - "En People, para armar el legajo de cada ingreso."
  - "En Legales, para seguir las normas nuevas de cada país donde opera Ripio."
- Disclaimer: "No es una herramienta oficial de Ripio. Usa solo datos públicos: las blockchains y las certificaciones que Ripio publica. No calcula el respaldo bancario de cierres sin certificar."
- Footer: Máximo Sckell · linkedin.com/in/msckell · github.com/msckell

**View "Cómo funciona" (reader 2):**
- A simple diagram: sources → deterministic engine → agents → verifier → outputs (Slack, tasks, page, Excel).
- Verification against the accountant: the 9 rows with raw sum, adjustments, computed figure, certified figure, difference and status.
- The methodology, with each decision labeled documented, inferred or hypothesis.
- The exception agent: what it investigated, what it proved with evidence and what it left as a task.
- The AI verifier: the attempts log for this close and the table of errors it rejects (taken from the tests).
- In production: CI badge, last runs (date, duration, result), schedule, alerting and networks watched.
- The design decisions in 5 bullets, and a link to the repo.

**Rules for the page**
- Every number on the page is read from `data/`. None is hardcoded.
- Keep two dates apart:
  - The close is a fixed snapshot. "Cierre al 30/09/2026, generado el ..." never goes stale.
  - The daily monitor shows "Última corrida hace X". Only the monitor gets a stale warning, when its last successful run is older than 36 hours.
- Deploy to Vercel (project name, for example, `cierre-wfiat-demo`).

**CHECKPOINT 3, stop here.** The owner opens the page on a phone and on desktop.

### Phase 4 · Production

- `ci.yml`, on push and PR:
  - ruff
  - offline pytest, including the golden tests against recorded fixtures
  - site build
  - ignore commits that only touch data
- `daily.yml`, at 12:00 UTC (09:00 Buenos Aires):
  - Run the monitor: supply per network and new deployments on the networks in the candidate list.
  - Write the data, append to `runs.jsonl` and commit if anything changed.
  - Post to Slack only when something changes or a run fails.
  - Run a live golden check that recomputes the certified cutoffs from the network. A failure triggers a Slack alert.
- `close.yml`:
  - Triggered manually with a cutoff input, plus a scheduled run the day after each quarter end.
  - Order (decision 12): compute → verify → commit the package → wait for the Vercel deploy → check that the Excel URL answers → open the tasks → post to Slack with the link.
  - If the check fails, post an alert with no link.
- **Tasks:** every item the exception agent could not prove becomes a GitHub issue, with its evidence links and a label for the close. The Slack message lists them under "Qué te toca revisar".
- Slack: a real Incoming Webhook in a demo workspace the owner creates. Until it exists, use dry run mode.

**CHECKPOINT 4, stop here.** Done means four things:
- CI is green.
- One scheduled run has executed.
- The tasks were opened.
- The Slack message arrived with a working link.

### Phase 5 · Polish and QA

- README in English: what and why, how it works, verification results, methodology labels, how to run, design decisions, limitations.
- QA with fresh eyes:
  - trace every number on the page back to `data/`
  - check every link
  - confirm the noindex
  - confirm no wording promises more than Phase 0 proved
  - run a secret scan over the whole git history
  - run a quick Lighthouse check on mobile

**FINAL CHECKPOINT.** The owner approves. Making the repo public is the owner's call.

---

## 7. Rules

- Never invent data. If a source fails, say so and stop. Never present an estimate as data.
- A failed query is never a zero.
- Never commit secrets.
- Respect the rate limits of public RPCs.
- No destructive git operations (force push, history rewrite) without explicit approval.
- If something blocks you, stop and explain what you tried, in plain words.

---

## 8. Out of scope

- Bank collateral and coverage ratios.
- Anything that needs Ripio's internal data.
- Networks that are not EVM, and networks outside the written candidate list (the page says which networks were checked).
- Login, database, backend server, live LLM on the page.

---

## 9. Environment variables

- Required: `ANTHROPIC_MODEL` · `SLACK_WEBHOOK_URL` · `CLAUDE_CODE_OAUTH_TOKEN` (CI only, from `claude setup-token`)
- Optional:
  - `ETHERSCAN_API_KEY` (API V2, free, multichain)
  - `ALCHEMY_API_KEY` or `DRPC_API_KEY`
  - `COINGECKO_API_KEY` (free demo key)
  - `RPC_URL_<CHAIN>` overrides
- GitHub issues use the Actions `GITHUB_TOKEN` with `issues: write`. No extra key.

---

## 10. Manual steps for the owner (say when each one is due)

1. Phase 0:
   - Confirm the table of certified figures.
   - Only if the public sources fail on old logs: create a free key (Etherscan or Alchemy).
2. Phase 4: run `claude setup-token` and save the token as the GitHub secret `CLAUDE_CODE_OAUTH_TOKEN`. Nothing needed in Phase 2: local runs use the `claude` login.
3. Phase 3: link the repo to Vercel.
4. Phase 4:
   - Create a free Slack workspace with a `#finanzas-cierre` channel and an Incoming Webhook.
   - Add `SLACK_WEBHOOK_URL` to `.env` and to the GitHub secrets.
