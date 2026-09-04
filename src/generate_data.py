"""Seeded synthetic source data for the BBSI pipeline.

Three tables, shaped like the feeds a retail bank's warehouse would hold:

  holders   - one row per reportable person, with the identity fields HMRC
              needs to match the record to a taxpayer
  accounts  - one row per account, joined to a holder; joint accounts appear
              as two rows sharing an account_id
  postings  - interest credited to an account, dated

Defects are injected deliberately and at known rates so the validation and
reconciliation stages have something real to find. `DEFECT_RATES` is the
contract between this module and the tests.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

from tax_year import TaxYear

SEED = 20260907

# Injected defect rates. The tests assert the pipeline finds these back.
DEFECT_RATES = {
    "nino_missing": 0.031,
    "nino_malformed": 0.018,
    "dob_missing": 0.012,
    "address_incomplete": 0.024,
    "posting_outside_tax_year": 0.040,
}

FIRST_NAMES = [
    "Aisha", "Brian", "Catherine", "Dev", "Eleanor", "Farid", "Grace", "Hamish",
    "Imogen", "Jaspreet", "Kwame", "Lucy", "Mohammed", "Niamh", "Oluwaseun",
    "Priya", "Quentin", "Rosalind", "Sanjay", "Tara", "Ualan", "Verity",
    "William", "Xiuying", "Yusuf", "Zainab",
]
LAST_NAMES = [
    "Abbott", "Bhatt", "Chowdhury", "Doyle", "Eriksson", "Fitzgerald", "Gill",
    "Hargreaves", "Iqbal", "Jenkins", "Kaur", "Lindqvist", "Mensah", "Nowak",
    "Okonkwo", "Pemberton", "Quinn", "Rahman", "Sinclair", "Thackeray",
    "Ubadike", "Vasquez", "Whitfield", "Xu", "Yates", "Zielinski",
]
STREETS = [
    "Alder Way", "Bramley Road", "Cathedral Close", "Dovecote Lane",
    "Eastgate", "Fenwick Street", "Granby Row", "Hollybank Avenue",
    "Ivywood Crescent", "Jubilee Terrace",
]
TOWNS = [
    ("Birmingham", "B"), ("Manchester", "M"), ("Leeds", "LS"), ("Bristol", "BS"),
    ("Sheffield", "S"), ("Nottingham", "NG"), ("Newcastle upon Tyne", "NE"),
    ("Cardiff", "CF"), ("Glasgow", "G"), ("Norwich", "NR"),
]

PRODUCTS = [
    # (product, typical balance band, gross rate)
    ("Instant Access Saver", (500, 25_000), 0.0285),
    ("Fixed Rate Bond 1yr", (2_000, 85_000), 0.0450),
    ("Fixed Rate Bond 3yr", (5_000, 120_000), 0.0425),
    ("Cash ISA", (1_000, 20_000), 0.0400),
    ("Current Account", (100, 6_000), 0.0100),
    ("Notice Account 90d", (1_000, 60_000), 0.0390),
]

VALID_PREFIX_FIRST = "ABCEGHJKLMNOPRSTWXYZ"
VALID_PREFIX_SECOND = "ABCEGHJKLMNPRSTWXYZ"
DISALLOWED_PREFIXES = {"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"}


def _make_nino(rng: random.Random) -> str:
    while True:
        prefix = rng.choice(VALID_PREFIX_FIRST) + rng.choice(VALID_PREFIX_SECOND)
        if prefix not in DISALLOWED_PREFIXES:
            break
    digits = "".join(str(rng.randint(0, 9)) for _ in range(6))
    return f"{prefix}{digits}{rng.choice('ABCD')}"


def _break_nino(nino: str, rng: random.Random) -> str:
    """Apply one of the malformations that actually turn up in feeds."""
    how = rng.choice(
        ["disallowed_prefix", "short", "letters_in_digits", "blank_suffix", "bad_suffix"]
    )
    if how == "disallowed_prefix":
        return rng.choice(sorted(DISALLOWED_PREFIXES)) + nino[2:]
    if how == "short":
        return nino[:-2]
    if how == "letters_in_digits":
        return nino[:2] + "O" + nino[3:]
    if how == "blank_suffix":
        return nino[:8] + " "
    return nino[:8] + rng.choice("EFGZ")


def generate(ty: TaxYear, n_holders: int = 40_000, seed: int = SEED):
    """Build the three source frames for one tax year."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)

    # ---- holders -------------------------------------------------------
    holders = []
    for i in range(n_holders):
        hid = f"H{i + 1:07d}"
        nino = _make_nino(rng)
        roll = rng.random()
        if roll < DEFECT_RATES["nino_missing"]:
            nino = ""
        elif roll < DEFECT_RATES["nino_missing"] + DEFECT_RATES["nino_malformed"]:
            nino = _break_nino(nino, rng)

        dob = date(1940, 1, 1) + timedelta(days=rng.randint(0, 24_000))
        dob_str = dob.isoformat()
        if rng.random() < DEFECT_RATES["dob_missing"]:
            dob_str = ""

        town, pc = rng.choice(TOWNS)
        line1 = f"{rng.randint(1, 240)} {rng.choice(STREETS)}"
        postcode = f"{pc}{rng.randint(1, 40)} {rng.randint(1, 9)}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}{rng.choice('ABDEFGHJLNPQRSTUWXYZ')}"
        if rng.random() < DEFECT_RATES["address_incomplete"]:
            if rng.random() < 0.5:
                postcode = ""
            else:
                line1 = ""

        holders.append(
            {
                "holder_id": hid,
                "first_name": rng.choice(FIRST_NAMES),
                "last_name": rng.choice(LAST_NAMES),
                "nino": nino,
                "date_of_birth": dob_str,
                "address_line_1": line1,
                "town": town,
                "postcode": postcode,
            }
        )
    holders_df = pd.DataFrame(holders)

    # ---- accounts ------------------------------------------------------
    accounts = []
    acct_seq = 0
    for h in holders_df["holder_id"]:
        for _ in range(nprng.integers(1, 4)):
            acct_seq += 1
            product, band, rate = rng.choice(PRODUCTS)
            balance = float(nprng.uniform(*band))
            accounts.append(
                {
                    "account_id": f"A{acct_seq:08d}",
                    "holder_id": h,
                    "product": product,
                    "gross_rate": rate,
                    "avg_balance": round(balance, 2),
                    "is_isa": product == "Cash ISA",
                    "holding_type": "sole",
                }
            )
    accounts_df = pd.DataFrame(accounts)

    # A slice of accounts are joint: duplicate the account under a second holder.
    joint_pool = accounts_df.sample(frac=0.06, random_state=seed)
    partners = holders_df["holder_id"].sample(len(joint_pool), random_state=seed + 1).values
    joint = joint_pool.copy()
    joint["holder_id"] = partners
    joint["holding_type"] = "joint"
    accounts_df.loc[joint_pool.index, "holding_type"] = "joint"
    accounts_df = pd.concat([accounts_df, joint], ignore_index=True)
    accounts_df = accounts_df[accounts_df.duplicated(["account_id", "holder_id"]) == False]

    # ---- postings ------------------------------------------------------
    # Interest is credited quarterly. A known slice is dated just outside the
    # tax year, so the boundary logic has something to exclude.
    unique_accounts = accounts_df.drop_duplicates("account_id")
    rows = []
    for rec in unique_accounts.itertuples(index=False):
        annual_gross = rec.avg_balance * rec.gross_rate
        for q in range(4):
            when = ty.start + timedelta(days=90 * q + rng.randint(0, 20))
            if rng.random() < DEFECT_RATES["posting_outside_tax_year"]:
                when = ty.start - timedelta(days=rng.randint(1, 40)) if rng.random() < 0.5 \
                    else ty.end + timedelta(days=rng.randint(1, 40))
            amount = annual_gross / 4 * float(nprng.uniform(0.85, 1.15))
            rows.append(
                {
                    "posting_id": f"P{len(rows) + 1:09d}",
                    "account_id": rec.account_id,
                    "posted_on": when.isoformat(),
                    "gross_interest": round(amount, 2),
                }
            )
    postings_df = pd.DataFrame(rows)

    return holders_df, accounts_df, postings_df
