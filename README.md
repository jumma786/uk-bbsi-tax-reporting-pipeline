# UK BBSI Tax Reporting Pipeline

A production-shaped pipeline for the **Bank and Building Society Interest (BBSI)** return
to HMRC: extract account-level interest for the tax year, check reportable-person
completeness, queue the exceptions, and **reconcile the return back to the ledger before
anyone files it**.

The point of this repo is the fourth step. Building an extract is easy. Proving the extract
is right, and refusing to file it when it is not, is the part that carries a statutory
penalty if you get it wrong.

```bash
pip install -r requirements.txt
python src/run_pipeline.py --tax-year 2025 --holders 40000
python -m pytest tests/ -q
```

---

## Results from the current run

40,000 holders, 84,943 account-holder links, 320,548 interest postings across the 2025-26
tax year.

| | |
|---|---|
| Postings received | 320,548 |
| In tax year (6 Apr 2025 – 5 Apr 2026) | 307,874 |
| **Excluded by date** | **12,674** (£3,493,162.98) |
| Holders with exceptions | 3,302 |
| **Blocked from the return** | **1,964** |
| Reportable persons | 35,818 |
| Gross interest reported | £75,798,488.21 |
| **Unexplained residual** | **£0.03** on an £88,391,073.13 ledger |

### The reconciliation

Every line is a stated reason the return differs from the ledger. The residual after all of
them is what decides whether the file goes.

| Line | £ |
|---|---:|
| Ledger gross interest (all postings) | 88,391,073.13 |
| Postings outside the tax year | −3,493,162.98 |
| Held back: blocking data exception | −3,742,181.26 |
| ISA interest (exempt, not reportable) | −5,357,240.71 |
| Reported on return | −75,798,488.21 |
| **Unexplained residual** | **−0.03** |

### The exception queue

| Check | Severity | Holders | % of population |
|---|---|---:|---:|
| NINO missing | blocking | 1,227 | 3.068% |
| NINO malformed | blocking | 737 | 1.842% |
| Address incomplete | advisory | 966 | 2.415% |
| Date of birth missing | advisory | 465 | 1.162% |

Defects are injected at known rates and the pipeline recovers them to within 0.03
percentage points — that is what makes the exception report testable rather than merely
plausible.

---

## The four decisions that carry the pipeline

**1. The tax year boundary is 6 April to 5 April, and getting it wrong is silent.**
Interest posted on 5 April belongs to the closing year; 6 April opens the next one.
`tax_year.py` owns that boundary and nothing else re-implements it. Postings outside the
year are **excluded and counted**, never dropped — "how much did we exclude" is the first
question a reviewer asks, and a pipeline that cannot answer it has already failed.

**2. NINO validation is structural, and structure is all it can prove.**
`nino.py` implements the published HMRC/DWP rules: two prefix letters, six digits, one
suffix letter; first letter never D/F/I/Q/U/V; second never D/F/I/O/Q/U/V; prefixes BG, GB,
KN, NK, NT, TN, ZZ never issued; suffix A–D. A structurally valid NINO is not necessarily a
*correct* one. The module tells you a value cannot possibly be right; it cannot tell you it
is, and the docstring says so.

**3. Severity decides who picks the work up.**
A malformed NINO is **blocking** — HMRC cannot match the record, so it does not go on the
return. A missing address is **advisory** — it goes on the remediation list but does not
stop the filing. Splitting the queue this way is what makes it workable by an operations
team rather than a single undifferentiated list of 3,395 problems.

**4. Joint accounts split equally, and the split has to sum back.**
Interest on a jointly held account is divided between holders. Allocation is where a return
most easily stops tying to the ledger, so it happens once, in one function, and a test
proves the parts still equal the whole.

---

## The rounding tolerance is derived, not picked

The first version used a flat £0.05 tolerance. It failed at 4,000 holders with a £0.09
residual, and the fix was not to widen the number.

Rounding to pence is lossy and the loss scales with how many times you do it. Each rounding
event can move a total by at most half a penny, so:

```python
tolerance = max(0.05, 0.005 * rounding_events)
```

At 40,000 holders that is £603.81 against an £88.4M ledger — 0.0007%. The actual residual is
£0.03.

This matters beyond the arithmetic. **A flat cash tolerance silently becomes too tight as
volumes grow, which is how a reconciliation control starts failing for no reason and gets
widened until it stops catching anything.** A derived tolerance grows with the thing that
actually causes drift and stays tight against everything else. `test_a_material_difference_is_still_a_break_at_scale`
proves a £10,000 break is still caught at the larger tolerance.

---

## Never plug a difference

`reconcile.py` will not adjust the return to make it agree. When the residual exceeds
tolerance it emits a break with a diagnostic order:

```
BREAK: £10,000.00 less in the ledger than the return and its exclusions explain,
against a rounding tolerance of £160.00. Do not adjust the return. Check, in order:
the tax-year boundary filter, the joint-account split (parts must sum to the account
total), and duplicate postings.
```

A reconciled-by-plug figure is worse than an unreconciled one, because it looks correct and
nobody looks again.

---

## Layout

```
src/tax_year.py       UK tax year boundaries and the filing calendar
src/nino.py           National Insurance number validation (HMRC structural rules)
src/generate_data.py  deterministic test fixture for pipeline rules
src/public_data.py   HMRC public statistics loader
src/run_public_report.py  reproducible public-data report runner
src/validate.py       completeness checks, severity, the exception queue
src/build_return.py   tax-year filter, joint-account allocation, submission extract
src/reconcile.py      the waterfall, the derived tolerance, break analysis
src/run_pipeline.py   end-to-end run and the five output files
tests/                34 tests
```

Outputs land in `output/`: the submission extract, the exception queue, the operations
summary, the reconciliation waterfall, and a run summary.

---

## Honesty note

Read this before citing anything above.

- **The pipeline test fixture is synthetic and seeded.** `generate_data.py` builds the holders, accounts and
  postings; there is no real customer data here and there could not be. What is genuine is
  the **process**: the tax-year boundary, the NINO rules, the severity model, the joint
  allocation, and the reconciliation discipline.
- **The public report uses official HMRC statistics.** Run `python src/run_public_report.py`
  to download and normalize HMRC Table 3.7. HMRC does not publish real account-level BBSI
  records, names, NINOs, or postings, so the public runner does not invent those fields.
- **Defect rates are injected deliberately** (`DEFECT_RATES`) so the validation stage has
  something known to find. The tests assert the pipeline recovers them. That makes the
  exception percentages a test of the pipeline, **not** an estimate of real-world data
  quality in any bank.
- **The 30 June filing deadline should be verified against current HMRC guidance** before
  anyone relies on it. It is encoded in `TaxYear.filing_deadline` with a docstring saying
  the same thing. Deadlines change; this repo is not a tax reference.
- **This is a data pipeline, not tax advice.** It models the data production and assurance
  side of a return — extraction, completeness, remediation, reconciliation. It does not make
  tax technical judgements, and the ISA exemption is the only treatment rule it encodes.
- **No live HMRC submission.** Nothing here files anything. The output is an extract in the
  shape a submission would consume.
- All 34 tests pass and every figure in this README came from an actual run
  (`output/run_summary_2025-26.txt`), not from an estimate.
