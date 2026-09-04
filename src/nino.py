"""National Insurance number validation.

HMRC cannot match a reported interest record to a taxpayer without a valid
NINO, so this is the single highest-value completeness check in the pipeline.
The structural rules below are published by HMRC and DWP:

  * Format is two prefix letters, six digits, one suffix letter.
  * The first prefix letter cannot be D, F, I, Q, U or V.
  * The second prefix letter cannot be D, F, I, O, Q, U or V.
  * The prefixes BG, GB, KN, NK, NT, TN and ZZ are never issued.
  * The suffix is A, B, C or D. (A space is accepted in some legacy feeds
    and is treated here as a suffix that needs remediation, not a hard fail.)

A structurally valid NINO is not necessarily a *correct* one. This module
tells you a value cannot possibly be right; it cannot tell you it is.
"""

from __future__ import annotations

import re
from enum import Enum

FIRST_LETTER_DISALLOWED = set("DFIQUV")
SECOND_LETTER_DISALLOWED = set("DFIOQUV")
DISALLOWED_PREFIXES = {"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"}
VALID_SUFFIXES = set("ABCD")

_SHAPE = re.compile(r"^([A-Z]{2})(\d{6})([A-Z ])$")


class NinoStatus(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    BAD_FORMAT = "bad_format"
    DISALLOWED_PREFIX = "disallowed_prefix"
    DISALLOWED_LETTER = "disallowed_letter"
    SUFFIX_BLANK = "suffix_blank"
    BAD_SUFFIX = "bad_suffix"


def normalise(raw: object) -> str:
    """Strip spaces and upper-case. Real feeds arrive as 'ab 12 34 56 c'."""
    if raw is None:
        return ""
    return re.sub(r"\s+", "", str(raw)).upper()


def validate(raw: object) -> NinoStatus:
    """Classify a NINO. The status is the remediation category."""
    value = normalise(raw)
    if not value:
        return NinoStatus.MISSING

    # A trailing space is meaningful, so re-read the suffix from the raw form
    # before normalisation collapsed it.
    padded = str(raw).upper().replace(" ", "") if raw is not None else ""
    if len(padded) == 8:
        padded = padded + " "

    match = _SHAPE.match(padded if len(padded) == 9 else value)
    if not match:
        return NinoStatus.BAD_FORMAT

    prefix, _digits, suffix = match.groups()

    if prefix in DISALLOWED_PREFIXES:
        return NinoStatus.DISALLOWED_PREFIX
    if prefix[0] in FIRST_LETTER_DISALLOWED or prefix[1] in SECOND_LETTER_DISALLOWED:
        return NinoStatus.DISALLOWED_LETTER
    if suffix == " ":
        return NinoStatus.SUFFIX_BLANK
    if suffix not in VALID_SUFFIXES:
        return NinoStatus.BAD_SUFFIX

    return NinoStatus.VALID


def is_valid(raw: object) -> bool:
    return validate(raw) is NinoStatus.VALID


def is_reportable(raw: object) -> bool:
    """A blank suffix still matches a taxpayer in practice; other faults do not.

    This is the distinction that decides whether a record blocks the return or
    merely goes on the remediation list.
    """
    return validate(raw) in (NinoStatus.VALID, NinoStatus.SUFFIX_BLANK)
