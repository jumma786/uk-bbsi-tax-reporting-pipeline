"""UK tax year boundaries and the BBSI filing calendar.

The UK tax year runs 6 April to 5 April. Everything downstream of this module
depends on getting that boundary right: interest posted on 5 April belongs to
the closing year, interest posted on 6 April belongs to the opening one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TaxYear:
    """A UK tax year, identified by the calendar year it starts in.

    TaxYear(2025) is the 2025-26 tax year: 6 Apr 2025 to 5 Apr 2026.
    """

    start_year: int

    @property
    def start(self) -> date:
        return date(self.start_year, 4, 6)

    @property
    def end(self) -> date:
        return date(self.start_year + 1, 4, 5)

    @property
    def label(self) -> str:
        return f"{self.start_year}-{str(self.start_year + 1)[2:]}"

    @property
    def filing_deadline(self) -> date:
        """Statutory deadline for the return of interest.

        HMRC's deadline for the Bank and Building Society Interest return is
        30 June following the end of the tax year. See the README's
        "Verify against current HMRC guidance" note before relying on this.
        """
        return date(self.start_year + 1, 6, 30)

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end

    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.label


def tax_year_for(when: date) -> TaxYear:
    """Return the tax year a given date falls in."""
    if (when.month, when.day) >= (4, 6):
        return TaxYear(when.year)
    return TaxYear(when.year - 1)


def days_remaining_to_deadline(ty: TaxYear, today: date) -> int:
    """Negative once the deadline has passed. Used by the run summary."""
    return (ty.filing_deadline - today).days
