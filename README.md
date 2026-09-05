# UK BBSI Tax Reporting Pipeline

A production-shaped assurance pipeline for UK Bank and Building Society Interest (BBSI) reporting.

The project models the controls that matter before a return is filed: tax-year filtering, reportable-person validation, joint-account allocation, exception management, and reconciliation back to the source ledger.

## The Problem

Extracting interest is straightforward. Proving that the extract is complete, correctly dated, correctly allocated, and tied back to the ledger is the difficult part.

This pipeline is designed around one operational question:

> Can the return be explained back to the ledger without plugging the difference?

## Data Modes

### Public report

`src/run_public_report.py` downloads and normalizes HMRC's published Table 3.7 statistics for the 2017 to 2018 tax year. The source contains aggregate taxpayer counts and interest amounts by income band.

The public report is the appropriate real-data demonstration because HMRC does not publish personal names, NINOs, bank account identifiers, or account-level postings.

### Test fixture

`src/generate_data.py` creates deterministic synthetic holders, accounts, and postings solely to test the pipeline controls. The generated identities are not real people and must not be described as production data.

## Quickstart

```bash
pip install -r requirements.txt
python src/run_public_report.py
python src/run_pipeline.py --tax-year 2025 --holders 40000
python -m pytest tests/ -q
```

The public runner writes `output/public_hmrc_interest_by_income_band.csv` and `output/public_report.txt`.

## Control Flow

```text
Source extract -> Tax-year filter -> Completeness checks
    -> Exception queues -> Joint-account allocation -> Return build
    -> Ledger reconciliation -> File, hold, or investigate
```

## Key Controls

### Tax-year boundary

The UK tax year runs from 6 April to 5 April. Postings outside the year are excluded and reported, never silently dropped.

### Structural NINO validation

The validator checks whether a NINO is structurally possible. It does not claim that a structurally valid value belongs to a particular person.

### Exception severity

Missing or malformed NINOs are blocking because HMRC cannot reliably match the record. Address and date-of-birth issues are advisory and remain visible for remediation.

### Joint-account allocation

Interest is allocated across account holders once, in one step, and the allocation must sum back to the account total.

### Derived tolerance

The reconciliation tolerance scales with the number of rounding events rather than relying on a fixed cash amount. A material break remains a break and is never plugged.

## Repository Layout

```text
src/tax_year.py          Tax-year boundaries and filing calendar
src/nino.py              Structural NINO validation
src/public_data.py       HMRC public-statistics loader
src/run_public_report.py Public-data report runner
src/generate_data.py     Deterministic test fixture
src/validate.py          Completeness checks and exception severity
src/build_return.py      Filtering, allocation, and return construction
src/reconcile.py         Reconciliation waterfall and break diagnostics
src/run_pipeline.py      End-to-end control-pipeline runner
sql/bbsi_return.sql      Equivalent SQL implementation
tests/                   Pipeline and control tests
output/                  Generated reports and normalized data
```

## Public Sources

- [HMRC Table 3.7: Property, interest, dividend and other income](https://www.gov.uk/government/statistics/investment-income-2010-to-2011)
- [HMRC BBSI return guidance](https://www.gov.uk/guidance/bank-and-building-society-interest-returns)

## Verification

The test suite covers tax-year boundaries, NINO rules, missing-data severity, date filtering, joint-account allocation, ISA exclusion, and ledger reconciliation.

```text
34 tests passed
```

## Important Limitation

This repository is an engineering demonstration, not tax advice and not a live HMRC submission system. The public report uses official aggregate statistics; the granular control tests use synthetic fixtures because real account-level BBSI data is confidential.
