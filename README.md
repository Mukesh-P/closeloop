# CloseLoop

**Month-end close, cross-system exposure and explainable credit decisions in seconds, not days. Built on Exasol.**

CloseLoop reconciles millions of transactions inside Exasol, turns every difference into a classified break with a proposed correcting journal, finds customers whose true exposure is hidden across systems, and explains every finding (and every declined application) in plain English.

## The problem

Banks run on systems that don't agree. Core banking, the general ledger, the loan book, treasury and the credit-risk system each hold part of the truth, with no shared keys and different spellings of the same customer.

| Who | Today | Why it matters |
|---|---|---|
| **Finance operations** | Month-end close consumes a full week of matching core banking to the GL line by line | Reporting is late; skilled people do clerical work |
| **Financial controllers** | Breaks that can't be explained roll forward, so reconciliation never fully closes | Misstatement risk, audit findings |
| **Credit risk** | Exposure is seen one system at a time | A customer can be over limit while every system looks fine, and new credit is approved on the wrong number |
| **Customers** | A declined application comes with little or no explanation | Poor experience, complaints, conduct risk |

CloseLoop addresses all four challenges in the track: **reconciliation cycles that never fully close, exposure spread across systems that disagree, a month-end close consuming a full week, and explaining a declined application to a customer.**

## What CloseLoop does

1. **Reconciles inside Exasol** with seven SQL passes, each only looking at what earlier passes left: exact → timing → FX/rounding (with or without timing) → sign flip → **split postings** (many GL lines summing to one payment) → fuzzy reference (`EDIT_DISTANCE`) → duplicates, missing and orphan postings. Every match carries a confidence score.
2. **Builds a break register** with severity, status and a suggested action. Self-explaining breaks (same-month timing, fuzzy reference matches, FX differences ≤ 100) are **auto-cleared**, so people only see what needs judgement.
3. **Generates correcting journals**: one proposed entry per break (contra account, amount, reversal flag, evidence flag), exportable as CSV for GL upload once approved.
4. **Supports the review workflow**: approve, reject or escalate breaks individually or in bulk. Decisions are stored in Exasol as an audit trail, and a **close-readiness gauge** blocks sign-off while high-severity breaks are unreviewed. For unmatched items it suggests **likely matches** on the same account, ranked by reference similarity, date and amount.
5. **Resolves customers across three systems** with a Python UDF (`NORMALIZE_NAME`) running inside Exasol, computes true exposure and surfaces **hidden limit breaches**.
6. **Decides credit applications on true exposure**, shows which ones the risk system alone would have **wrongly approved**, and drafts a **customer letter** for each decline. The letter gives the reason, a counter-offer within the limit, and the steps that would change the outcome, with no internal system details.
7. **Answers questions in plain English**: Claude writes the SQL, CloseLoop checks it is read-only, and Exasol runs it. Preset questions work offline.
8. **Exports a close pack**: a Markdown summary of status, breaks, journals and exposure for the close file.
9. **Measures its own accuracy** against a hidden answer key of planted problems, including cases deliberately designed so that no rule should auto-match them.

Claude (`claude-opus-5-5`) writes the explanations, letters and SQL when `ANTHROPIC_API_KEY` is set. Without a key, every feature except free-form questions falls back to deterministic templates, so the demo runs fully offline.

## Results

Measured on Exasol Personal running locally (MacBook, database VM limited to 2 vCPU and 8 GB RAM):

| | |
|---|---|
| Data | 2,000,000 core banking transactions vs 2,006,037 GL postings; 5,000 counterparties; 1,250 new applications |
| **Full close** (7 matching passes, break register, journals, exposure, credit decisions) | **12.3 s** |
| Matched automatically (exact + split postings) | 97.6% of transactions |
| Breaks auto-cleared without human review | 26,892 of 60,034 (45%) |
| Planted problems flagged for action | 66,977 / 66,977 (100%) |
| Planted problems classified to the exact type | 65,865 / 66,977 (98.3%). The remainder is the 1,073 deliberately unresolvable cases, which go to a human as missing/orphan with the true match suggested |
| Proposed correcting journals | 35,517 |
| Hidden limit breaches | 443 counterparties, ₹4,708 Cr unreported exposure |
| Bad approvals prevented | 188 applications (₹1,221 Cr) that the risk system alone would have approved |

The data is synthetic (see `closeloop/generate.py`): break types are planted at realistic rates with a hidden answer key so accuracy can be measured. Real data is messier; the confidence scores, candidate matches and review workflow exist for exactly that.

## Architecture

```
 Source systems (synthetic)       Exasol Personal (in-memory MPP)                    Streamlit UI
┌───────────────────────┐ bulk  ┌───────────────────────────────────────────┐     ┌────────────────────┐
│ CORE_TXN    (2M rows) │ load  │ GL_RANKED ─► 7 SQL matching passes        │     │ 🏁 Close cockpit    │
│ GL_ENTRY    (2M rows) │ ────► │   ─► RECON_MATCH ─► BREAKS ─► JOURNALS    │ ──► │ 🧾 Breaks + review  │
│ LOAN_BOOK             │       │ NORMALIZE_NAME (Python3 UDF, in-database) │     │ 📒 Journals         │
│ TREASURY              │       │   ─► ENTITY_EXPOSURE ─► CREDIT_DECISIONS  │     │ 🏦 Exposure         │
│ RISK_LIMITS           │       │ BREAK_REVIEW (audit trail)                │     │ 📨 Credit decisions │
│ APPLICATIONS          │       └───────────────────────────────────────────┘     │ 💬 Ask              │
└───────────────────────┘                                                          └─────────┬──────────┘
                                                              Claude: explanations, letters, text-to-SQL
```

All heavy lifting happens in Exasol: Python only sends SQL and renders results.

| File | Purpose |
|---|---|
| `closeloop/generate.py` | Synthetic bank data with planted breaks, applications and an answer key |
| `closeloop/load.py` | Creates schema + UDF, bulk-loads data into Exasol |
| `closeloop/recon.py` | Matching passes, break register, journals, accuracy scoring, candidate matches |
| `closeloop/exposure.py` | Cross-system entity resolution and exposure |
| `closeloop/credit.py` | Credit decisions on true exposure, reason codes and counter-offers |
| `closeloop/review.py` | Analyst decisions, close readiness, close pack |
| `closeloop/explain.py` | Claude explanations, decision letters and text-to-SQL, with offline fallbacks |
| `closeloop/pipeline.py` | Runs the whole close; also a CLI |
| `sql/` | Table DDL and the `NORMALIZE_NAME` Python UDF |
| `app.py` | Streamlit dashboard |
| `tests/` | End-to-end tests against Exasol |

## Setup

Requirements: macOS, Linux or Windows; Python 3.10+; ~8 GB RAM and ~10 GB free disk for the default 2M-row dataset (more for larger `ROWS`).

```bash
# 1. Install the Exasol launcher
curl https://downloads.exasol.com/exasol-personal/installer.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 2. Install and start Exasol Personal locally, with the Python UDF runtime
make db                # = exasol install local && exasol slc install PYTHON3
exasol info            # the password is in ~/.exasol/personal/deployments/default/secrets.json

# 3. Install CloseLoop
git clone https://github.com/Mukesh-P/closeloop.git && cd closeloop
make setup             # creates .venv and .env
# edit .env: set EXASOL_DSN / EXASOL_USER / EXASOL_PASSWORD
# optional: set ANTHROPIC_API_KEY for Claude explanations, letters and free-form questions
```

## Usage

```bash
make data              # generate + load 2M transactions (ROWS=500000 make data for a smaller machine)
make run               # dashboard at http://localhost:8501
make close             # or: run the close from the command line and print accuracy
make test              # 26 end-to-end tests in an isolated CLOSELOOP_TEST schema (~10 s)
```

In the dashboard:

1. **▶ Run month-end close** (sidebar) runs every step inside Exasol and shows timings.
2. **Close cockpit** shows headline numbers, close readiness, where the time went, engine accuracy and the close pack download.
3. **Breaks**: use the bulk actions to approve routine fixes, filter what's left, select rows to approve, reject or escalate. Select one break to see its explanation, proposed journal and possible matches.
4. **Journals**: download approved journals as CSV for GL upload.
5. **Exposure**: compare reported vs true utilisation, and select a hidden breach to see what each system holds.
6. **Credit decisions**: see the applications the risk system alone would have got wrong, select one, and draft the customer letter.
7. **Ask**: pick a preset question or type your own.

## Deployment

CloseLoop is two processes: an Exasol database and a stateless Streamlit app.

- **Local:** follow Setup above.
- **Cloud database:** `exasol install aws` (or `azure`, `exoscale`, `stackit`) provisions Exasol Personal in your own account. Run `exasol slc install PYTHON3` and put the DSN it prints into `.env`.
- **App hosting:** any host that runs Python can serve `streamlit run app.py --server.port 8501 --server.address 0.0.0.0`, as long as it can reach the database port. Set the same environment variables as `.env`.

## Security notes

- The Ask tab only runs a single `SELECT`/`WITH` statement; anything else is rejected before it reaches the database. For production, also run it under a read-only database user.
- Customer letters are generated from a restricted set of fields so internal identifiers never reach the model or the letter.
- Credentials live in `.env`, which is git-ignored.

## License

MIT, see [LICENSE](LICENSE).
