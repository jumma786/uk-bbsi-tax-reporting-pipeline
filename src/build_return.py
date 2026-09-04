"""Aggregate interest to the reportable-person grain and build the return extract.

Two decisions in here carry the whole thing:

1. **The tax year boundary.** Only postings dated 6 April to 5 April inclusive
   belong to the year. Postings outside it are excluded and counted, never
   silently dropped, because "how much did we exclude" is the first question
   anyone reviewing the return will ask.

2. **Joint accounts.** Interest on a jointly held account is split equally
   between holders. Splitting is where a return most easily stops tying back
   to the ledger, so the split is done once, here, and the reconciliation in
   `reconcile.py` proves the parts still sum to the whole.
"""

from __future__ import annotations

import pandas as pd

from tax_year import TaxYear


def postings_in_year(postings: pd.DataFrame, ty: TaxYear) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split postings into in-year and out-of-year. Both are returned."""
    posted = pd.to_datetime(postings["posted_on"]).dt.date
    in_year = postings[(posted >= ty.start) & (posted <= ty.end)].copy()
    out_of_year = postings[(posted < ty.start) | (posted > ty.end)].copy()
    return in_year, out_of_year


def interest_by_account(in_year: pd.DataFrame) -> pd.DataFrame:
    return (
        in_year.groupby("account_id", as_index=False)["gross_interest"]
        .sum()
        .rename(columns={"gross_interest": "account_gross_interest"})
    )


def allocate_to_holders(account_interest: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """Split each account's interest equally across its holders."""
    holders_per_account = (
        accounts.groupby("account_id", as_index=False)["holder_id"]
        .nunique()
        .rename(columns={"holder_id": "n_holders"})
    )
    links = accounts[["account_id", "holder_id", "product", "is_isa"]].drop_duplicates()
    allocated = (
        links.merge(account_interest, on="account_id", how="inner")
        .merge(holders_per_account, on="account_id", how="left")
    )
    allocated["allocated_interest"] = (
        allocated["account_gross_interest"] / allocated["n_holders"]
    ).round(2)
    return allocated


def build(
    allocated: pd.DataFrame,
    holders: pd.DataFrame,
    blocked: set[str],
    exclude_isa: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (submission_rows, held_back_rows).

    ISA interest is exempt from income tax and is excluded by default. It is
    returned in the held-back frame rather than dropped, so the reconciliation
    can still account for every penny.
    """
    work = allocated.copy()

    if exclude_isa:
        isa_rows = work[work["is_isa"]].copy()
        isa_rows["hold_reason"] = "isa_exempt"
        work = work[~work["is_isa"]]
    else:
        isa_rows = work.iloc[0:0].copy()
        isa_rows["hold_reason"] = pd.Series(dtype="object")

    blocked_rows = work[work["holder_id"].isin(blocked)].copy()
    blocked_rows["hold_reason"] = "blocking_exception"
    work = work[~work["holder_id"].isin(blocked)]

    submission = (
        work.groupby("holder_id", as_index=False)["allocated_interest"]
        .sum()
        .rename(columns={"allocated_interest": "gross_interest_reported"})
        .merge(
            holders[
                [
                    "holder_id",
                    "first_name",
                    "last_name",
                    "nino",
                    "date_of_birth",
                    "address_line_1",
                    "town",
                    "postcode",
                ]
            ],
            on="holder_id",
            how="left",
        )
    )
    submission["gross_interest_reported"] = submission["gross_interest_reported"].round(2)
    submission = submission[submission["gross_interest_reported"] > 0].reset_index(drop=True)

    held_back = pd.concat([isa_rows, blocked_rows], ignore_index=True)
    return submission, held_back
