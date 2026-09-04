"""Tests for the BBSI pipeline.

The reconciliation test is the one that matters: it proves the return plus its
stated exclusions equals the ledger to the penny.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import build_return  # noqa: E402
import reconcile as reconcile_mod  # noqa: E402
import validate as validate_mod  # noqa: E402
from generate_data import generate  # noqa: E402
from nino import NinoStatus, is_reportable, validate as validate_nino  # noqa: E402
from tax_year import TaxYear, tax_year_for  # noqa: E402


# --------------------------------------------------------------------- tax year

def test_tax_year_boundaries():
    ty = TaxYear(2025)
    assert ty.start == date(2025, 4, 6)
    assert ty.end == date(2026, 4, 5)
    assert ty.label == "2025-26"
    assert ty.days() == 365


def test_fifth_of_april_is_the_closing_year_not_the_opening_one():
    assert tax_year_for(date(2026, 4, 5)).start_year == 2025
    assert tax_year_for(date(2026, 4, 6)).start_year == 2026


def test_filing_deadline_follows_year_end():
    assert TaxYear(2025).filing_deadline == date(2026, 6, 30)


# ------------------------------------------------------------------------ nino

@pytest.mark.parametrize("value", ["AB123456C", "ab 12 34 56 c", "JG452919A"])
def test_valid_ninos(value):
    assert validate_nino(value) is NinoStatus.VALID


@pytest.mark.parametrize("prefix", ["BG", "GB", "KN", "NK", "NT", "TN", "ZZ"])
def test_never_issued_prefixes_rejected(prefix):
    assert validate_nino(f"{prefix}123456C") is NinoStatus.DISALLOWED_PREFIX


@pytest.mark.parametrize("value", ["DA123456C", "QB123456C", "AO123456C", "AD123456C"])
def test_disallowed_letters_rejected(value):
    assert validate_nino(value) is NinoStatus.DISALLOWED_LETTER


def test_missing_and_malformed():
    assert validate_nino("") is NinoStatus.MISSING
    assert validate_nino(None) is NinoStatus.MISSING
    assert validate_nino("AB12345") is NinoStatus.BAD_FORMAT
    assert validate_nino("AB1234567C") is NinoStatus.BAD_FORMAT
    assert validate_nino("AB12O456C") is NinoStatus.BAD_FORMAT


def test_bad_suffix_rejected():
    assert validate_nino("AB123456E") is NinoStatus.BAD_SUFFIX


def test_blank_suffix_is_reportable_but_not_clean():
    assert validate_nino("AB123456 ") is NinoStatus.SUFFIX_BLANK
    assert is_reportable("AB123456 ") is True
    assert is_reportable("ZZ123456C") is False


# ------------------------------------------------------------------ validation

@pytest.fixture(scope="module")
def dataset():
    ty = TaxYear(2025)
    holders, accounts, postings = generate(ty, n_holders=4_000, seed=99)
    return ty, holders, accounts, postings


def test_generator_is_deterministic():
    a = generate(TaxYear(2025), n_holders=300, seed=7)[2]["gross_interest"].sum()
    b = generate(TaxYear(2025), n_holders=300, seed=7)[2]["gross_interest"].sum()
    assert a == b


def test_validation_finds_the_injected_defects(dataset):
    _ty, holders, _accounts, _postings = dataset
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    found = set(exceptions["check"])
    assert {"nino_missing", "nino_malformed", "address_incomplete", "dob_missing"} <= found


def test_every_exception_has_a_severity(dataset):
    _ty, holders, _accounts, _postings = dataset
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    assert exceptions["severity"].isin([validate_mod.BLOCKING, validate_mod.ADVISORY]).all()


def test_only_nino_and_name_faults_block(dataset):
    _ty, holders, _accounts, _postings = dataset
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    blocking = set(exceptions.loc[exceptions["severity"] == validate_mod.BLOCKING, "check"])
    assert blocking <= {"nino_missing", "nino_malformed", "name_missing"}


# -------------------------------------------------------------- tax-year filter

def test_postings_outside_the_year_are_excluded_not_dropped(dataset):
    ty, _holders, _accounts, postings = dataset
    in_year, out_of_year = build_return.postings_in_year(postings, ty)
    assert len(in_year) + len(out_of_year) == len(postings)
    assert len(out_of_year) > 0
    total = in_year["gross_interest"].sum() + out_of_year["gross_interest"].sum()
    assert round(total, 2) == round(postings["gross_interest"].sum(), 2)


def test_no_in_year_posting_falls_outside_the_boundary(dataset):
    ty, _holders, _accounts, postings = dataset
    in_year, _ = build_return.postings_in_year(postings, ty)
    dates = pd.to_datetime(in_year["posted_on"]).dt.date
    assert dates.min() >= ty.start
    assert dates.max() <= ty.end


# ------------------------------------------------------------- joint allocation

def test_joint_account_interest_splits_equally_and_sums_back(dataset):
    ty, _holders, accounts, postings = dataset
    in_year, _ = build_return.postings_in_year(postings, ty)
    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)

    joint_ids = accounts.loc[accounts["holding_type"] == "joint", "account_id"].unique()
    assert len(joint_ids) > 0

    sample = allocated[allocated["account_id"] == joint_ids[0]]
    assert len(sample) >= 2
    assert sample["allocated_interest"].nunique() == 1

    regrouped = allocated.groupby("account_id")["allocated_interest"].sum()
    original = account_interest.set_index("account_id")["account_gross_interest"]
    diff = (regrouped - original).abs().max()
    assert diff < 0.02  # per-holder rounding to pence only


# ------------------------------------------------------------- reconciliation

def test_return_reconciles_to_the_ledger(dataset):
    ty, holders, accounts, postings = dataset
    in_year, out_of_year = build_return.postings_in_year(postings, ty)
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    blocked = validate_mod.blocked_holder_ids(exceptions)

    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)
    submission, held_back = build_return.build(allocated, holders, blocked)

    rec = reconcile_mod.reconcile(
        postings, out_of_year, held_back, submission,
        rounding_events=len(allocated) + len(submission),
    )
    assert rec.balanced, reconcile_mod.break_analysis(rec)


def test_tolerance_scales_with_rounding_events():
    assert reconcile_mod.tolerance_for(0) == reconcile_mod.FLOOR_GBP
    assert reconcile_mod.tolerance_for(10) == reconcile_mod.FLOOR_GBP  # floor still wins
    assert reconcile_mod.tolerance_for(40_000) == pytest.approx(200.0)


def test_a_material_difference_is_still_a_break_at_scale():
    """A big tolerance must not swallow a real break."""
    rec = reconcile_mod.Reconciliation(ledger_total=9_000_000.00, rounding_events=32_000)
    rec.reported_total = 8_990_000.00
    assert not rec.balanced
    assert "BREAK" in reconcile_mod.break_analysis(rec)


def test_an_unexplained_difference_is_reported_as_a_break():
    rec = reconcile_mod.Reconciliation(ledger_total=1_000.00)
    rec.add("Postings outside the tax year", 100.00)
    rec.reported_total = 850.00
    assert not rec.balanced
    assert rec.residual == 50.00
    assert "BREAK" in reconcile_mod.break_analysis(rec)


def test_isa_interest_is_held_back_not_reported(dataset):
    ty, holders, accounts, postings = dataset
    in_year, _ = build_return.postings_in_year(postings, ty)
    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)
    _submission, held_back = build_return.build(allocated, holders, set())
    assert (held_back["hold_reason"] == "isa_exempt").any()
    assert held_back.loc[held_back["hold_reason"] == "isa_exempt", "allocated_interest"].sum() > 0


def test_blocked_holders_do_not_reach_the_return(dataset):
    ty, holders, accounts, postings = dataset
    in_year, _ = build_return.postings_in_year(postings, ty)
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    blocked = validate_mod.blocked_holder_ids(exceptions)
    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)
    submission, _held = build_return.build(allocated, holders, blocked)
    assert not set(submission["holder_id"]) & blocked


def test_reported_holders_all_carry_a_matchable_nino(dataset):
    ty, holders, accounts, postings = dataset
    in_year, _ = build_return.postings_in_year(postings, ty)
    exceptions = validate_mod.check_holders(holders, date(2026, 6, 1))
    blocked = validate_mod.blocked_holder_ids(exceptions)
    account_interest = build_return.interest_by_account(in_year)
    allocated = build_return.allocate_to_holders(account_interest, accounts)
    submission, _ = build_return.build(allocated, holders, blocked)
    assert submission["nino"].map(is_reportable).all()
