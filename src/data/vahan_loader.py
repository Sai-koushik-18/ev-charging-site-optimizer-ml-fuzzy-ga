"""
Real EV adoption from Vahan, the Government of India's vehicle registration system (MoRTH).

The reports in data/external/vahan/ were exported by hand from the Vahan Public Report
(analytics.parivahan.gov.in/analytics/vahanpublicreport), which needs a CAPTCHA:

  india_states_<year>.xlsx   Y-Axis State, X-Axis Fuel, all of India, one calendar year
  ap_<year>.xlsx             Andhra Pradesh, Y-Axis Fuel, X-Axis Vehicle Category, one year

"Electric" means battery vehicles that plug in: ELECTRIC(BOV), PURE EV and PLUG-IN HYBRID EV
(strong hybrids never plug in, so they are not counted).

The demand model's per-zone base demand in config/settings.yaml was set for a 3% EV share
(demand_simulation.ev_adoption_rate). A place's demand is scaled by its state's real share
of new registrations that are electric, relative to that 3%.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

VAHAN_DIR = Path("data/external/vahan")
EV_FUELS = ("ELECTRIC(BOV)", "PURE EV", "PLUG-IN HYBRID EV")
REFERENCE_EV_SHARE = 0.03
LATEST_FULL_YEAR = 2025
SOURCE = "Vahan (MoRTH, Government of India), new vehicle registrations"

# Vahan's state names that differ from OpenStreetMap's, both after _key()
ALIASES = {
    "andamanandnicobarislands": "andamanandnicobarisland",
    "utofdnhanddd": "dadraandnagarhavelianddamananddiu",
    "dadraandnagarhaveli": "dadraandnagarhavelianddamananddiu",
    "damananddiu": "dadraandnagarhavelianddamananddiu",
    "nationalcapitalterritoryofdelhi": "delhi",
    "nctofdelhi": "delhi",
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "uttaranchal": "uttarakhand",
}


def _key(name: str) -> str:
    k = re.sub(r"[^a-z]", "", str(name).lower().replace("&", "and"))
    return ALIASES.get(k, k)


def read_report(path) -> pd.DataFrame:
    """A Vahan Excel export as numbers, indexed by its first column (State or Fuel), with its Total row."""
    raw = pd.read_excel(path, header=None)
    header = next(i for i in range(min(len(raw), 15)) if str(raw.iloc[i, 0]).strip() in ("State", "Fuel"))
    df = pd.read_excel(path, header=header)
    first = df.columns[0]
    df = df[df[first].notna()].set_index(first)
    df.index = df.index.map(lambda s: str(s).strip())
    return df.apply(lambda col: pd.to_numeric(col.astype(str).str.replace(",", "").str.strip(), errors="coerce")).fillna(0)


@lru_cache(maxsize=None)
def state_table(year: int, folder: str = str(VAHAN_DIR)) -> pd.DataFrame:
    """Per state: all new registrations, electric ones, and the EV share, for one calendar year."""
    df = read_report(Path(folder) / f"india_states_{year}.xlsx")
    ev = df[[c for c in df.columns if c in EV_FUELS]].sum(axis=1)
    out = pd.DataFrame({"total": df["Total"], "ev": ev})
    out["ev_share"] = out["ev"] / out["total"]
    return out


def yearly_table(prefix: str = "ap", folder: str = str(VAHAN_DIR)) -> pd.DataFrame:
    """One state's yearly EV share, overall and for two-wheelers and cars (ap_<year>.xlsx files)."""
    rows = []
    for f in sorted(Path(folder).glob(f"{prefix}_[0-9][0-9][0-9][0-9].xlsx")):
        df = read_report(f)
        ev = df.loc[df.index.isin(EV_FUELS)].sum()
        tot = df.loc["Total"]
        two_w = [c for c in df.columns if "TWO WHEELER" in c]
        rows.append({"year": int(f.stem[-4:]), "total": tot["Total"], "ev": ev["Total"],
                     "ev_share": ev["Total"] / tot["Total"],
                     "two_wheeler_ev_share": ev[two_w].sum() / tot[two_w].sum(),
                     "car_ev_share": ev["LIGHT MOTOR VEHICLE"] / tot["LIGHT MOTOR VEHICLE"]})
    return pd.DataFrame(rows)


def ev_adoption(state: Optional[str], year: int = LATEST_FULL_YEAR, folder: str = str(VAHAN_DIR)) -> Optional[Dict]:
    """
    The real EV share for a state (all India when the state is unknown or unmatched) and the
    demand scale it implies. None when the Vahan files are missing.
    """
    try:
        now = state_table(year, folder)
    except (FileNotFoundError, StopIteration):
        return None
    try:
        before = state_table(year - 1, folder)
    except (FileNotFoundError, StopIteration):
        before = None
    by_key = {_key(s): s for s in now.index if s != "Total"}
    name = by_key.get(_key(state)) if state else None
    row = now.loc[name or "Total"]
    growth = None
    if before is not None and (name or "Total") in before.index and before.loc[name or "Total", "ev"] > 0:
        growth = float(row["ev"] / before.loc[name or "Total", "ev"] - 1)
    share = float(row["ev_share"])
    return {
        "state": name or "All India",
        "matched": bool(name),
        "asked_for": state,
        "year": year,
        "ev_share": share,
        "ev_registrations": int(row["ev"]),
        "all_registrations": int(row["total"]),
        "ev_growth_vs_previous_year": growth,
        "reference_ev_share": REFERENCE_EV_SHARE,
        "demand_scale": share / REFERENCE_EV_SHARE,
        "source": SOURCE,
    }
