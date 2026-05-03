"""Pre-fetch AMC list + all funds from SEC API and save to CSV."""
from __future__ import annotations

import time

import pandas as pd
import requests

API_KEY = "1bd81fb34be943e0b720df937f1d30e6"
BASE = "https://api.sec.or.th"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}


def main() -> None:
    print("Fetching AMC list...")
    r = requests.get(f"{BASE}/FundFactsheet/fund/amc", headers=HEADERS, timeout=30)
    r.raise_for_status()
    amc = pd.DataFrame(r.json())
    amc.to_csv("amc.csv", index=False)
    print(f"  {len(amc)} AMCs saved → amc.csv")

    rows: list[dict] = []
    name_col = "name_th" if "name_th" in amc.columns else amc.columns[0]
    for i, row in amc.iterrows():
        uid = row["unique_id"]
        amc_name = row[name_col]
        try:
            r = requests.get(
                f"{BASE}/FundFactsheet/fund/amc/{uid}", headers=HEADERS, timeout=30
            )
            if r.status_code == 204 or not r.content:
                print(f"  [{i+1}/{len(amc)}] {amc_name}: empty")
                continue
            r.raise_for_status()
            funds = r.json()
            for f in funds:
                rows.append(
                    {
                        "amc_unique_id": uid,
                        "amc_name": amc_name,
                        "proj_id": f.get("proj_id"),
                        "proj_abbr_name": f.get("proj_abbr_name"),
                        "proj_name_th": f.get("proj_name_th"),
                        "proj_name_en": f.get("proj_name_en"),
                    }
                )
            print(f"  [{i+1}/{len(amc)}] {amc_name}: {len(funds)} funds")
        except Exception as e:
            print(f"  [{i+1}/{len(amc)}] {amc_name}: ERROR {e}")
        time.sleep(0.25)

    funds_df = pd.DataFrame(rows)
    funds_df.to_csv("funds.csv", index=False)
    print(f"\nTotal: {len(funds_df)} funds saved → funds.csv")


if __name__ == "__main__":
    main()
