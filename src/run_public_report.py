"""Build outputs from HMRC's official published statistics."""

from pathlib import Path

from public_data import load_hmrc_interest_table

OUT = Path(__file__).resolve().parent.parent / "output"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    table = load_hmrc_interest_table()
    table.to_csv(OUT / "public_hmrc_interest_by_income_band.csv", index=False)
    report = "\n".join([
        "Public data BBSI report",
        "Source: HMRC Table 3.7, Property, interest, dividend and other income",
        "Tax year: 2017 to 2018",
        f"Income-band rows: {len(table):,}",
        f"Interest covered: GBP {table['interest_gbp_millions'].sum():,.0f} million",
        "Aggregate taxpayer statistics; no real names, NINOs, accounts, or postings.",
    ]) + "\n"
    (OUT / "public_report.txt").write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
