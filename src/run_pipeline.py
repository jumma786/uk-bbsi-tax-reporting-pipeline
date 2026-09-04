"""Run the full BBSI cycle end to end and write the three deliverables.

    python src/run_pipeline.py --tax-year 2025

Outputs into output/:
    bbsi_return_<year>.csv        the submission extract
    exception_queue_<year>.csv    every failed check, per holder
    exception_summary_<year>.csv  the operations view
    reconciliation_<year>.csv     the waterfall
    run_summary_<year>.txt        what a reviewer reads first
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import build_return
import reconcile as reconcile_mod
import validate as validate_mod
from generate_data import generate
from tax_year import TaxYear, days_remaining_to_deadline

OUT = Path(__file__).resolve().parent.parent / "output"


def main() -> int:
    ap = argparse.ArgumentParser(description="UK BBSI return pipeline")
    ap.add_argument("--tax-year", type=int, default=2025, help="year the tax year starts in")
    ap.add_argument("--holders", type=int, default=40_000)
    ap.add_argument("--as-at", type=str, default=date.today().isoformat())
    args = ap.parse_args()

    ty = TaxYear(args.tax_year)
    as_at = date.fromisoformat(args.as_at)
    OUT.mkdir(exist_ok=True)

    print(f"BBSI return {ty.label}  ({ty.start} to {ty.end})")
    print(f"Filing deadline {ty.filing_deadline}\n")

    # 1. extract -------------------------------------------------------
    holders, accounts, postings = generate(ty, n_holders=args.holders)
    print(f"  extract       {len(postings):>9,} postings  {len(accounts):>8,} account-holder links")

    in_year, out_of_year = build_return.postings_in_year(postings, ty)
    print(f"                {len(in_year):>9,} in year   {len(out_of_year):>8,} excluded by date")

    # 2. completeness --------------------------------------------------
    exceptions = validate_mod.check_holders(holders, as_at)
    blocked = validate_mod.blocked_holder_ids(exceptions)
    summary = validate_mod.summarise(exceptions, len(holders))
    print(f"  validate      {len(exceptions):>9,} exceptions {len(blocked):>8,} holders blocked")

    # 3 & 4. allocate and build ----------------------------------------
    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)
    submission, held_back = build_return.build(allocated, holders, blocked)
    print(f"  build         {len(submission):>9,} reportable persons")

    # 5. reconcile -----------------------------------------------------
    rec = reconcile_mod.reconcile(
        postings, out_of_year, held_back, submission,
        rounding_events=len(allocated) + len(submission),
    )
    print(f"  reconcile     residual £{rec.residual:,.2f}  "
          f"{'BALANCED' if rec.balanced else 'BREAK'}\n")

    # ---- write -------------------------------------------------------
    submission.to_csv(OUT / f"bbsi_return_{ty.label}.csv", index=False)
    exceptions.to_csv(OUT / f"exception_queue_{ty.label}.csv", index=False)
    summary.to_csv(OUT / f"exception_summary_{ty.label}.csv", index=False)
    rec.to_frame().to_csv(OUT / f"reconciliation_{ty.label}.csv", index=False)

    lines = [
        f"BBSI return {ty.label}",
        f"Tax year            {ty.start} to {ty.end}",
        f"Filing deadline     {ty.filing_deadline} "
        f"({days_remaining_to_deadline(ty, as_at):+d} days from {as_at})",
        "",
        f"Postings received   {len(postings):,}",
        f"  in tax year       {len(in_year):,}",
        f"  excluded by date  {len(out_of_year):,} "
        f"(£{out_of_year['gross_interest'].sum():,.2f})",
        "",
        f"Holders             {len(holders):,}",
        f"  with exceptions   {exceptions['holder_id'].nunique() if not exceptions.empty else 0:,}",
        f"  blocked           {len(blocked):,}",
        f"Reportable persons  {len(submission):,}",
        f"Gross interest      £{submission['gross_interest_reported'].sum():,.2f}",
        "",
        "Exception summary",
        summary.to_string(index=False) if not summary.empty else "  none",
        "",
        "Reconciliation",
        rec.to_frame().to_string(index=False),
        "",
        reconcile_mod.break_analysis(rec),
    ]
    text = "\n".join(lines)
    (OUT / f"run_summary_{ty.label}.txt").write_text(text, encoding="utf-8")
    print(text.split("Exception summary")[1].strip()[:0] or "", end="")
    print(f"Written to {OUT}")

    return 0 if rec.balanced else 1


if __name__ == "__main__":
    raise SystemExit(main())
