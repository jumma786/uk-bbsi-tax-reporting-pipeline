"""Load HMRC's published public interest-income table."""

from io import BytesIO

import pandas as pd
import requests

HMRC_URL = "https://assets.publishing.service.gov.uk/media/5ef4a34586650c129e57427d/NS_Table_3_7_1718.xlsx"


def load_hmrc_interest_table() -> pd.DataFrame:
    response = requests.get(HMRC_URL, timeout=60)
    response.raise_for_status()
    raw = pd.read_excel(BytesIO(response.content), sheet_name="T3.7", header=None)
    rows = raw.iloc[14:38, [0, 6, 7, 8]].copy()
    rows.columns = ["income_band_lower_gbp", "taxpayers", "interest_gbp_millions", "mean_interest_gbp"]
    for column in rows.columns:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    return rows.dropna(subset=["income_band_lower_gbp", "interest_gbp_millions"]).reset_index(drop=True)
