"""Reportable-person completeness checks and the exception queue.

HMRC matches a reported interest record to a taxpayer using name, NINO, date
of birth and address. A record missing any of those is not automatically
wrong, but it is a record HMRC may not be able to match, and the bank is the
party that has to fix it before the deadline.

Every check returns a category, because the category is what determines who
picks the work up: a malformed NINO goes to data remediation, a missing
address goes to the servicing team who can write to the customer.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from nino import NinoStatus, is_reportable, validate as validate_nino

# Severity decides whether a record can go on the return at all.
BLOCKING = "blocking"      # cannot be reported until fixed
ADVISORY = "advisory"      # reportable, but flag for remediation

CHECKS = {
    "nino_missing": BLOCKING,
    "nino_malformed": BLOCKING,
    "dob_missing": ADVISORY,
    "dob_implausible": ADVISORY,
    "address_incomplete": ADVISORY,
    "name_missing": BLOCKING,
}

MIN_PLAUSIBLE_DOB = date(1900, 1, 1)


def _dob_status(raw: str, as_at: date) -> str | None:
    if not raw:
        return "dob_missing"
    try:
        parsed = date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return "dob_implausible"
    if parsed < MIN_PLAUSIBLE_DOB or parsed >= as_at:
        return "dob_implausible"
    return None


def check_holders(holders: pd.DataFrame, as_at: date) -> pd.DataFrame:
    """One row per (holder, failed check). Holders that pass produce no rows."""
    exceptions = []

    for row in holders.itertuples(index=False):
        nino_status = validate_nino(row.nino)
        if nino_status is NinoStatus.MISSING:
            exceptions.append((row.holder_id, "nino_missing", "NINO absent"))
        elif nino_status is not NinoStatus.VALID:
            exceptions.append(
                (row.holder_id, "nino_malformed", f"NINO fails structure: {nino_status.value}")
            )

        dob_issue = _dob_status(row.date_of_birth, as_at)
        if dob_issue:
            exceptions.append((row.holder_id, dob_issue, f"date_of_birth={row.date_of_birth!r}"))

        if not str(row.address_line_1).strip() or not str(row.postcode).strip():
            missing = []
            if not str(row.address_line_1).strip():
                missing.append("address_line_1")
            if not str(row.postcode).strip():
                missing.append("postcode")
            exceptions.append(
                (row.holder_id, "address_incomplete", "missing " + ", ".join(missing))
            )

        if not str(row.last_name).strip():
            exceptions.append((row.holder_id, "name_missing", "last_name absent"))

    frame = pd.DataFrame(exceptions, columns=["holder_id", "check", "detail"])
    if frame.empty:
        frame["severity"] = pd.Series(dtype="object")
        return frame
    frame["severity"] = frame["check"].map(CHECKS)
    return frame


def blocked_holder_ids(exceptions: pd.DataFrame) -> set[str]:
    if exceptions.empty:
        return set()
    return set(exceptions.loc[exceptions["severity"] == BLOCKING, "holder_id"])


def summarise(exceptions: pd.DataFrame, total_holders: int) -> pd.DataFrame:
    """The exception report an operations team would actually work from."""
    if exceptions.empty:
        return pd.DataFrame(columns=["check", "severity", "holders", "pct_of_population"])
    out = (
        exceptions.groupby(["check", "severity"])["holder_id"]
        .nunique()
        .reset_index(name="holders")
        .sort_values(["severity", "holders"], ascending=[True, False])
    )
    out["pct_of_population"] = (out["holders"] / total_holders * 100).round(3)
    return out.reset_index(drop=True)


def reportable_mask(holders: pd.DataFrame) -> pd.Series:
    """True where the NINO is good enough for HMRC to match on."""
    return holders["nino"].map(is_reportable)
