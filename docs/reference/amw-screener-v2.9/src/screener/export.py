"""CSV exports with consistent naming, ISO timestamps and UTF-8 BOM (opens correctly in Excel, including non-ASCII text)."""
from __future__ import annotations

import pandas as pd

ENCODING = "utf-8-sig"


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out.index = out.index.strftime("%Y-%m-%d %H:%M:%S%z")
        out = out.reset_index().rename(columns={"index": "timestamp", "Datetime": "timestamp", "Date": "timestamp"})
        if out.columns[0] != "timestamp":
            out = out.rename(columns={out.columns[0]: "timestamp"})
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    return out.to_csv(index=False).encode(ENCODING)


def export_name(kind: str, stamp: str | None = None) -> str:
    stamp = stamp or pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    return f"amw_{kind}_{stamp}.csv"
