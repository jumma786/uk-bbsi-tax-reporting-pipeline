"""Reconciliation: prove the return ties back to the ledger before filing.

The rule this module exists to enforce: **never plug a difference.** A total
that has been forced to agree is worse than one that visibly does not, because
it looks correct and nobody looks again.

The reconciliation is a waterfall. Every line is a stated reason the return
differs from the source ledger, and the residual after all of them must be
zero to the penny. A non-zero residual is a break, and a break stops the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Rounding to pence is lossy, and the loss scales with how many times you do
# it. Each rounding event can move the total by at most half a penny, so the
# tolerance is derived from the number of events rather than picked as a
# round number. A flat cash tolerance silently becomes too tight as volumes
# grow, which is how a reconciliation control starts failing for no reason
# and gets widened until it stops catching anything.
MAX_ERROR_PER_ROUNDING = 0.005
FLOOR_GBP = 0.05


def tolerance_for(rounding_events: int) -> float:
    """Worst-case rounding drift for a given number of rounded amounts."""
    return max(FLOOR_GBP, MAX_ERROR_PER_ROUNDING * rounding_events)


@dataclass
class Reconciliation:
    ledger_total: float
    lines: list[tuple[str, float]] = field(default_factory=list)
    reported_total: float = 0.0
    rounding_events: int = 0

    def add(self, label: str, amount: float) -> None:
        self.lines.append((label, round(amount, 2)))

    @property
    def explained(self) -> float:
        return round(sum(amount for _, amount in self.lines), 2)

    @property
    def residual(self) -> float:
        return round(self.ledger_total - self.explained - self.reported_total, 2)

    @property
    def tolerance(self) -> float:
        return tolerance_for(self.rounding_events)

    @property
    def balanced(self) -> bool:
        return abs(self.residual) <= self.tolerance

    def to_frame(self) -> pd.DataFrame:
        rows = [("Ledger gross interest (all postings)", round(self.ledger_total, 2))]
        rows += [(label, -amount) for label, amount in self.lines]
        rows.append(("Reported on return", -round(self.reported_total, 2)))
        rows.append(("Unexplained residual", self.residual))
        rows.append(("Rounding tolerance", round(self.tolerance, 2)))
        return pd.DataFrame(rows, columns=["line", "amount_gbp"])


def reconcile(
    postings: pd.DataFrame,
    out_of_year: pd.DataFrame,
    held_back: pd.DataFrame,
    submission: pd.DataFrame,
    rounding_events: int = 0,
) -> Reconciliation:
    rec = Reconciliation(
        ledger_total=float(postings["gross_interest"].sum()),
        rounding_events=rounding_events,
    )

    rec.add("Postings outside the tax year", float(out_of_year["gross_interest"].sum()))

    if not held_back.empty:
        for reason, group in held_back.groupby("hold_reason"):
            label = {
                "isa_exempt": "ISA interest (exempt, not reportable)",
                "blocking_exception": "Held back: blocking data exception",
            }.get(str(reason), str(reason))
            rec.add(label, float(group["allocated_interest"].sum()))

    rec.reported_total = float(submission["gross_interest_reported"].sum())
    return rec


def break_analysis(rec: Reconciliation) -> str:
    """What to say when it does not balance. Diagnosis, not a plug."""
    if rec.balanced:
        return "Balanced within tolerance. Return is safe to file."
    direction = "more" if rec.residual > 0 else "less"
    return (
        f"BREAK: £{abs(rec.residual):,.2f} {direction} in the ledger than the return "
        f"and its exclusions explain, against a rounding tolerance of "
        f"£{rec.tolerance:,.2f}. "
        f"Do not adjust the return. Check, in order: the tax-year boundary filter, the joint-account split "
        f"(parts must sum to the account total), and duplicate postings."
    )
