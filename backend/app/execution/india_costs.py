"""Indian trading costs by DATE - statutory charges per executed leg, with each rate as it was on the trade day.

Ported from Trade@0df3e09d0aa5942e3352327d5b998c5f6939ac61, elliott/costs.py (index options), extended to futures and
equity so one table drives every estimate in ATP (paper fills, backtests, the options backtester).

Per leg (buy or sell x price x quantity), the rates of THAT day:
  STT    options: sell premium only - 0.017% to 31 May 2016, 0.05% from 1 Jun 2016, 0.0625% from 1 Apr 2023, 0.1% from
         1 Oct 2024, 0.15% from 1 Apr 2026. Futures: sell side - 0.017% to 31 May 2013, 0.01% from 1 Jun 2013, 0.0125%
         from 1 Apr 2023, 0.02% from 1 Oct 2024, 0.05% from 1 Apr 2026. Equity intraday: sell side 0.025%. Equity
         delivery: both sides 0.1%. Exercise of a long in-the-money option at expiry: on the intrinsic (settlement) value -
         0.125%, 0.15% from 1 Apr 2026 (Finance Act 2026; NSE circular NSE/FATAX/73524 of 31 Mar 2026).
  Exchange transaction charges (both sides; NSE, the first slab while charges were slabbed): options (of premium) 0.05%
         -> 0.053% (1 Jan 2021) -> 0.05% (1 Apr 2023) -> 0.0495% (1 Apr 2024) -> 0.03503% (1 Oct 2024) -> 0.0355299%
         (1 Mar 2026); futures 0.0019% -> 0.002% -> 0.0019% -> 0.00188% -> 0.00173% -> 0.0018299%; equity 0.00325% ->
         0.00345% (1 Jan 2021) -> 0.00325% (1 Apr 2023) -> 0.00297% (1 Oct 2024) -> 0.0030699% (1 Mar 2026). The NSE
         IPFT contribution (Rs 10 / crore, Rs 50 / crore of premium for options, 1 Apr 2023 - 28 Feb 2026; Rs 0.01 / crore
         otherwise) is not modelled: on 1 Mar 2026 NSE moved it into the transaction charge, total unchanged.
  SEBI turnover fee 0.0001% (Rs 10 / crore), both sides. Stamp duty (buy only): options 0.003%, futures 0.002%, equity
         intraday 0.003%, delivery 0.015% (from 1 Jul 2020; before that each State's own rate, ~0.002%).
  GST 18% on brokerage + exchange + SEBI (service tax before Jul 2017: 12.36% -> 14% -> 14.5% -> 15%).
  Brokerage = per executed order (a setting). Slippage belongs in the fill price, not here.
Other statutory rates (read by the financial-year tax report, `app/tax/report.py`; not part of `leg_cost`):
  CTT    MCX non-agricultural commodity futures: sell side 0.01%.
  VDA    crypto / virtual digital assets: TDS 1% of the sale consideration (section 194S; section 393(1) of the
         Income-tax Act 2025 from 1 Apr 2026) and tax 30% of the gain (section 115BBH).
  These carry one row each (the value the tax report has always used); their start dates are not modelled because no
  platform trade predates them.
Sources (TX-1): every row of every table has an entry in `SOURCES` - the document (URL), its reference (circular /
notification / Act) and date. A row whose rate is not settled by an official document keeps a `verify` text saying
exactly what is missing (`unverified()` lists them); change a row only together with its source. A table's first row
(dated 1 Jan 2000) is a floor: its source covers the rate up to the next row, not every year back to 2000.
"STT on the wrong side" is impossible by construction: STT is attached to the sell leg (or both, for delivery) of each
executed order, whichever leg opened the position.
"""
import datetime as dt
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import pandas as pd

Table = Sequence[Tuple[dt.date, float]]
D = dt.date

STT_SELL: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.00017), (D(2016, 6, 1), 0.0005), (D(2023, 4, 1), 0.000625), (D(2024, 10, 1), 0.001),
               (D(2026, 4, 1), 0.0015)],
    "FUTURE": [(D(2000, 1, 1), 0.00017), (D(2013, 6, 1), 0.0001), (D(2023, 4, 1), 0.000125), (D(2024, 10, 1), 0.0002),
               (D(2026, 4, 1), 0.0005)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.00025)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.001)],
}
STT_BUY: Dict[str, Table] = {"EQUITY_DELIVERY": [(D(2000, 1, 1), 0.001)]}
EXCHANGE: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.0005), (D(2021, 1, 1), 0.00053), (D(2023, 4, 1), 0.0005), (D(2024, 4, 1), 0.000495),
               (D(2024, 10, 1), 0.0003503), (D(2026, 3, 1), 0.000355299)],
    "FUTURE": [(D(2000, 1, 1), 0.000019), (D(2021, 1, 1), 0.00002), (D(2023, 4, 1), 0.000019), (D(2024, 4, 1), 0.0000188),
               (D(2024, 10, 1), 0.0000173), (D(2026, 3, 1), 0.000018299)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.0000325), (D(2021, 1, 1), 0.0000345), (D(2023, 4, 1), 0.0000325),
                        (D(2024, 10, 1), 0.0000297), (D(2026, 3, 1), 0.000030699)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.0000325), (D(2021, 1, 1), 0.0000345), (D(2023, 4, 1), 0.0000325),
                        (D(2024, 10, 1), 0.0000297), (D(2026, 3, 1), 0.000030699)],
}
STAMP_BUY: Dict[str, Table] = {
    "OPTION": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00003)],
    "FUTURE": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00002)],
    "EQUITY_INTRADAY": [(D(2000, 1, 1), 0.00002), (D(2020, 7, 1), 0.00003)],
    "EQUITY_DELIVERY": [(D(2000, 1, 1), 0.0001), (D(2020, 7, 1), 0.00015)],
}
TAX: Table = [(D(2000, 1, 1), 0.1236), (D(2015, 6, 1), 0.14), (D(2015, 11, 15), 0.145), (D(2016, 6, 1), 0.15), (D(2017, 7, 1), 0.18)]
SEBI = 0.000001
SEBI_FEE: Table = [(D(2000, 1, 1), SEBI)]          # the constant above as a one-row table, so it has a source row too
STT_EXERCISE: Table = [(D(2000, 1, 1), 0.00125), (D(2026, 4, 1), 0.0015)]
CTT_SELL_COMMODITY_FUTURE: Table = [(D(2000, 1, 1), 0.0001)]
VDA_TDS: Table = [(D(2000, 1, 1), 0.01)]
VDA_TAX: Table = [(D(2000, 1, 1), 0.30)]


# --- sources (TX-1) ------------------------------------------------------------------------------------------------------

class Source(NamedTuple):
    """Where one dated rate comes from. `verify` is empty when an official document settles the rate; otherwise it says
    exactly what is missing (the rate stays the provisional default until a document is found)."""
    url: str
    ref: str          # circular / notification / Act and section
    date: str         # the document's date (ISO), or the Act's assent date
    note: str = ""
    verify: str = ""

    def as_dict(self) -> Dict[str, str]:
        return dict(self._asdict())


_NSE_STT_2026 = Source("https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf",
                       "NSE/FATAX/73524 (Circular Ref. 02/2026): Finance Act 2026, assent 30 Mar 2026", "2026-03-31",
                       "Sale of an option 0.15% (to 31 Mar 2026 0.10%); option exercised 0.15% (to 31 Mar 2026 0.125%, payable by "
                       "the purchaser); sale of a future 0.05% (to 31 Mar 2026 0.02%); effective 1 Apr 2026.")
_NSE_STT_2024 = Source("https://nsearchives.nseindia.com/content/circulars/FATAX63809.pdf",
                       "NSE/FATAX/63809: Finance (No. 2) Act 2024, assent 16 Aug 2024", "2024-09-09",
                       "Sale of an option 0.10% (to 30 Sep 2024 0.0625%); sale of a future 0.02% (to 30 Sep 2024 0.0125%); "
                       "effective 1 Oct 2024; exercise unchanged.")
_NSE_STT_2023 = Source("https://archives.nseindia.com/content/circulars/FATAX56235.pdf",
                       "NSE/FATAX/56235: Finance Act 2023 (No. 8 of 2023), assent 31 Mar 2023", "2023-03-31",
                       "Sale of an option 0.0625% (was 0.05%); sale of a future 0.0125% (was 0.01%); effective 1 Apr 2023; "
                       "exercise unchanged at 0.125%. The date is the Act's assent; the circular's own date was not read.")
_NSE_FACTBOOK_2016 = Source("https://nsearchives.nseindia.com/web/sites/default/files/inline-files/nsefactbook2016.pdf",
                            "NSE Fact Book 2016, STT table (Finance Act 2016)", "2016",
                            "Sale of options not exercised raised from 0.017% to 0.05% with effect from 1 Jun 2016; exercised "
                            "options 0.125% (purchaser); futures 0.01% (seller).")
_FINANCE_2013 = Source("https://www.indiabudget.gov.in/budget2013-2014/ub2013-14/mem/mem1.pdf",
                       "Finance Bill 2013 memorandum (clause 125, s.98 Finance (No. 2) Act 2004)", "2013-02-28",
                       "STT on sale of futures in securities reduced from 0.017% to 0.01% for transactions on or after 1 Jun 2013.")
_NSE_STT_PAGE = Source("https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies",
                       "NSE: SEBI Turnover Fees, STT and Other levies (s.98 Finance (No. 2) Act 2004)", "2026",
                       "Equity delivery 0.1% purchase and 0.1% sale; equity not settled by delivery 0.025% sale (seller). "
                       "Unchanged by the Finance Act 2026 (NSE/FATAX/73524 lists no equity change).")
_NSE_TC_2021 = Source("https://archives.nseindia.com/content/circulars/FA46730.pdf", "NSE/FA/46730", "2020-12-18",
                      "Transaction charges raised from 1 Jan 2021 to augment the IPFT corpus: cash first slab Rs 3.25 -> 3.45 "
                      "per lakh, equity futures first slab Rs 1.90 -> 2.00 per lakh, options (Rs 3-100 cr premium slab) "
                      "Rs 50 -> 53 per lakh of premium.")
_NSE_TC_2023 = Source("https://archives.nseindia.com/content/circulars/FA56129.pdf", "NSE/FA/56129", "2023-03-24",
                      "The 1 Jan 2021 increase rolled back from 1 Apr 2023 (cash Rs 3.25, futures Rs 1.90, options Rs 50 per "
                      "lakh, first slabs); IPFT contribution raised to Rs 10 / crore (Rs 50 / crore of options premium).")
_NSE_TC_2024A = Source("https://nsearchives.nseindia.com/content/circulars/FA61137.pdf", "NSE/FA/61137", "2024-03-14",
                       "From 1 Apr 2024: options Rs 50 -> 49.50 per lakh of premium, futures first slab Rs 1.90 -> 1.88 per lakh.")
_NSE_TC_2024B = Source("https://nsearchives.nseindia.com/content/circulars/FA64232.pdf",
                       "NSE/FA/64232 (SEBI/HO/MRD/TPD-1/P/CIR/2024/92 of 1 Jul 2024: uniform MII charges)", "2024-09-27",
                       "Flat charges from 1 Oct 2024, each side: cash Rs 2.97 per lakh, equity futures Rs 1.73 per lakh, equity "
                       "options Rs 35.03 per lakh of premium.")
_NSE_TC_2026 = Source("https://nsearchives.nseindia.com/content/circulars/FA73061.pdf", "NSE/FA/73061", "2026-02-27",
                      "From 1 Mar 2026 the IPFT contribution goes back to Rs 0.01 / crore and the transaction charge absorbs "
                      "it, total unchanged: cash Rs 297 -> 306.99 per crore, equity futures Rs 173 -> 182.99 per crore, equity "
                      "options Rs 3,503 -> 3,552.99 per crore of premium. Cross-check: each new charge is the old charge plus the "
                      "Rs 9.99 / 49.99 per crore the IPFT drops.")
_STAMP_2020 = Source("https://www.pib.gov.in/PressReleasePage.aspx?PRID=1635399",
                     "Indian Stamp Act 1899 Schedule I as amended by the Finance Act 2019; notifications of 30 Mar 2020 (PIB)",
                     "2020-06-30",
                     "Collected by the clearing corporations from 1 Jul 2020, on the buyer: delivery 0.015%, non-delivery 0.003%, "
                     "futures 0.002%, options 0.003% of premium. Also NSE: "
                     "https://www.nseindia.com/static/invest/first-time-investor-stamp-duty-charges-taxes")
_STAMP_STATES = Source("", "", "", verify="Before 1 Jul 2020 each State levied stamp duty on securities at its own rate; there "
                       "is no single official rate for this row (a placeholder of about 0.002% / 0.01% delivery).")
_GST_2017 = Source("https://cbic-gst.gov.in/hindi/pdf/central-tax-rate/Notification11-CGST.pdf",
                   "Notification 11/2017-Central Tax (Rate), Heading 9971 (financial and related services)", "2017-06-28",
                   "9% CGST + 9% SGST (or 18% IGST) from 1 Jul 2017: the Heading 9971 entry for financial and related services "
                   "not listed elsewhere. Later amendments (e.g. 05/2025-CT(R)) were not read line by line.")
_SERVICE_TAX_2015 = Source("https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=121872",
                           "PIB: service tax 12% -> 14% from 1 Jun 2015 (Finance Act 2015)", "2015-05",
                           "12.36% before (12% + 2% education cess + 1% secondary and higher education cess); 14% from 1 Jun 2015 "
                           "with the education cesses subsumed. The notification number was not looked up.")
_SBC_2015 = Source("https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=130401",
                   "PIB: Swachh Bharat Cess 0.5% on all taxable services from 15 Nov 2015 (s.119 Finance Act 2015)", "2015-11",
                   "14% + 0.5% = 14.5%. The notification number was not looked up.")
_KKC_2016 = Source("https://www.pib.gov.in/newsite/PrintRelease.aspx?relid=164456",
                   "PIB: cesses abolished for GST, naming the Krishi Kalyan Cess of the Finance Act 2016", "2017-06",
                   verify="No official document read states the 1 Jun 2016 start or the 0.5% rate of the Krishi Kalyan Cess.")
_SEBI_FEE = Source("https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies",
                   "NSE: SEBI turnover fee 0.0001% (Rs 10 / crore)", "2026",
                   verify="Ambiguous: a SEBI Gazette amendment of Schedule III (https://www.sebi.gov.in/sebi_data/attachdocs/"
                          "1488970004632.pdf) sets Rs 15 / crore for cash and equity derivatives, while NSE's page says Rs 10 / "
                          "crore; the date of any change between them was not found. Kept at Rs 10 / crore.")
_EXERCISE_BASE = Source("https://nsearchives.nseindia.com/web/sites/default/files/inline-files/nsefactbook2016.pdf",
                        "NSE Fact Book 2016, STT table", "2016",
                        "Exercised options 0.125% (purchaser), unchanged by the 2023 and 2024 Acts (NSE/FATAX/56235, "
                        "NSE/FATAX/63809) up to 31 Mar 2026 (NSE/FATAX/73524).")
_CTT = Source("https://www.mcxindia.com/market-operations/clearing-settlement/commodities-transaction-tax",
              "Finance Act 2013 Chapter VII (Commodities Transaction Tax); MCX CTT page", "2013-07-01",
              "0.01% of the price of a sale of non-agricultural commodity derivatives (futures), payable by the seller, from "
              "1 Jul 2013. Finance Bill text: https://www.indiabudget.gov.in/budget2013-2014/ub2013-14/fb/bill7.pdf")
_VDA_TDS = Source("https://www.incometaxindia.gov.in/w/circular-no.-13/2022-guidelines-for-removal-of-difficulties-under-sub-section-6-"
                  "of-section-194s-of-the-income-tax-act-1961",
                  "s.194S Income-tax Act 1961 (CBDT Circular 13/2022 of 22 Jun 2022 and Circular 14/2022 of 28 Jun 2022: the "
                  "s.194S guidelines); s.393(1) Table Sl. 8(vi) Income-tax Act 2025 from 1 Apr 2026", "2022-06-22",
                  "1% of the consideration for transfer of a virtual digital asset (from 1 Jul 2022). From 1 Apr 2026 the same "
                  "1% with a nil threshold in the s.393(1) table (page URL not confirmed - verify: "
                  "https://www.incometaxindia.gov.in/w/section-393-5).")
_VDA_TAX = Source("https://www.incometaxindia.gov.in/w/section-115bbh",
                  "s.115BBH Income-tax Act 1961 (Finance Act 2022, from 1 Apr 2022 / AY 2023-24; CBDT Circular 23/2022 explains it)", "2022-03-30",
                  "30% of the income from transfer of a virtual digital asset, no deduction but the cost of acquisition, no "
                  "set-off of losses.",
                  verify="From 1 Apr 2026 the Income-tax Act 2025 applies; the section that carries the 30% rate in the new Act "
                         "was not found in an official text (secondary sources say the rate is kept).")

# (table, segment, since) -> source. Segment "" for the tables that have none.
SOURCES: Dict[Tuple[str, str, dt.date], Source] = {
    ("STT_SELL", "OPTION", D(2000, 1, 1)): _NSE_FACTBOOK_2016,
    ("STT_SELL", "OPTION", D(2016, 6, 1)): _NSE_FACTBOOK_2016,
    ("STT_SELL", "OPTION", D(2023, 4, 1)): _NSE_STT_2023,
    ("STT_SELL", "OPTION", D(2024, 10, 1)): _NSE_STT_2024,
    ("STT_SELL", "OPTION", D(2026, 4, 1)): _NSE_STT_2026,
    ("STT_SELL", "FUTURE", D(2000, 1, 1)): _FINANCE_2013,
    ("STT_SELL", "FUTURE", D(2013, 6, 1)): _FINANCE_2013,
    ("STT_SELL", "FUTURE", D(2023, 4, 1)): _NSE_STT_2023,
    ("STT_SELL", "FUTURE", D(2024, 10, 1)): _NSE_STT_2024,
    ("STT_SELL", "FUTURE", D(2026, 4, 1)): _NSE_STT_2026,
    ("STT_SELL", "EQUITY_INTRADAY", D(2000, 1, 1)): _NSE_STT_PAGE,
    ("STT_SELL", "EQUITY_DELIVERY", D(2000, 1, 1)): _NSE_STT_PAGE,
    ("STT_BUY", "EQUITY_DELIVERY", D(2000, 1, 1)): _NSE_STT_PAGE,
    **{("EXCHANGE", seg, D(2000, 1, 1)): _NSE_TC_2021 for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    **{("EXCHANGE", seg, D(2021, 1, 1)): _NSE_TC_2021 for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    ("EXCHANGE", "OPTION", D(2023, 4, 1)): _NSE_TC_2023,
    ("EXCHANGE", "FUTURE", D(2023, 4, 1)): _NSE_TC_2023,
    **{("EXCHANGE", seg, D(2023, 4, 1)): Source(
        _NSE_TC_2023.url, _NSE_TC_2023.ref, _NSE_TC_2023.date, _NSE_TC_2023.note,
        verify="NSE/FA/61137 (14 Mar 2024) also revised the cash-market slabs from 1 Apr 2024; its first-slab figure was not "
               "confirmed, so no 1 Apr 2024 row is added and this rate runs to 30 Sep 2024.")
       for seg in ("EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    ("EXCHANGE", "OPTION", D(2024, 4, 1)): _NSE_TC_2024A,
    ("EXCHANGE", "FUTURE", D(2024, 4, 1)): _NSE_TC_2024A,
    **{("EXCHANGE", seg, D(2024, 10, 1)): _NSE_TC_2024B for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    **{("EXCHANGE", seg, D(2026, 3, 1)): _NSE_TC_2026 for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    **{("STAMP_BUY", seg, D(2000, 1, 1)): _STAMP_STATES for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    **{("STAMP_BUY", seg, D(2020, 7, 1)): _STAMP_2020 for seg in ("OPTION", "FUTURE", "EQUITY_INTRADAY", "EQUITY_DELIVERY")},
    ("TAX", "", D(2000, 1, 1)): _SERVICE_TAX_2015,
    ("TAX", "", D(2015, 6, 1)): _SERVICE_TAX_2015,
    ("TAX", "", D(2015, 11, 15)): _SBC_2015,
    ("TAX", "", D(2016, 6, 1)): _KKC_2016,
    ("TAX", "", D(2017, 7, 1)): _GST_2017,
    ("SEBI_FEE", "", D(2000, 1, 1)): _SEBI_FEE,
    ("STT_EXERCISE", "", D(2000, 1, 1)): _EXERCISE_BASE,
    ("STT_EXERCISE", "", D(2026, 4, 1)): _NSE_STT_2026,
    ("CTT_SELL_COMMODITY_FUTURE", "", D(2000, 1, 1)): _CTT,
    ("VDA_TDS", "", D(2000, 1, 1)): _VDA_TDS,
    ("VDA_TAX", "", D(2000, 1, 1)): _VDA_TAX,
}


def tables() -> Dict[Tuple[str, str], Table]:
    """Every dated table by (name, segment) - what `SOURCES` must cover row for row."""
    out: Dict[Tuple[str, str], Table] = {}
    for name, by_segment in (("STT_SELL", STT_SELL), ("STT_BUY", STT_BUY), ("EXCHANGE", EXCHANGE), ("STAMP_BUY", STAMP_BUY)):
        out.update({(name, seg): table for seg, table in by_segment.items()})
    for name, table in (("TAX", TAX), ("SEBI_FEE", SEBI_FEE), ("STT_EXERCISE", STT_EXERCISE),
                        ("CTT_SELL_COMMODITY_FUTURE", CTT_SELL_COMMODITY_FUTURE), ("VDA_TDS", VDA_TDS), ("VDA_TAX", VDA_TAX)):
        out[(name, "")] = table
    return out


def source(table: str, segment: str, day) -> Source:
    """The source of the rate `table` / `segment` applies on `day` (the row in force that day)."""
    rows = tables()[(table, segment)]
    d = pd.Timestamp(day).date()
    since = rows[0][0]
    for start, _ in rows:
        if d >= start:
            since = start
    return SOURCES[(table, segment, since)]


def unverified() -> List[Tuple[str, str, dt.date, str]]:
    """(table, segment, since, what is missing) for every row an official document does not settle yet."""
    return [(t, s, since, src.verify) for (t, s, since), src in SOURCES.items() if src.verify]


SEGMENTS = tuple(STT_SELL)


def segment_for(instrument_kind: Optional[str], *, delivery: bool = False) -> str:
    """ATP instrument kinds -> cost segment. UNDERLYING is equity (intraday unless `delivery`)."""
    k = (instrument_kind or "UNDERLYING").upper()
    if k == "OPTION":
        return "OPTION"
    if k == "FUTURE":
        return "FUTURE"
    return "EQUITY_DELIVERY" if delivery else "EQUITY_INTRADAY"


def _at(table: Table, day) -> float:
    d = pd.Timestamp(day).date()
    v = table[0][1]
    for since, x in table:
        if d >= since:
            v = x
    return v


def rates(day, segment: str = "OPTION") -> Dict[str, float]:
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}")
    return {"stt_sell": _at(STT_SELL[segment], day), "stt_buy": _at(STT_BUY[segment], day) if segment in STT_BUY else 0.0,
            "exch": _at(EXCHANGE[segment], day), "stamp_buy": _at(STAMP_BUY[segment], day), "tax": _at(TAX, day), "sebi": SEBI}


def ctt_sell(day) -> float:
    """CTT on the sell side of an MCX non-agricultural commodity future, as a fraction, on `day`."""
    return _at(CTT_SELL_COMMODITY_FUTURE, day)


def vda_rates(day) -> Dict[str, float]:
    """Crypto / VDA on `day`: `tds` (section 194S, of the sale consideration) and `tax` (section 115BBH, of the gain)."""
    return {"tds": _at(VDA_TDS, day), "tax": _at(VDA_TAX, day)}


def leg_cost(day, side: str, price: float, qty: float, segment: str = "OPTION", brokerage_per_order: float = 0.0) -> dict[str, float]:
    """Total cost (Rs) of one executed order - slippage excluded (it is in the fill). side: "buy" | "sell"."""
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    r = rates(day, segment)
    turnover = float(price) * float(qty)
    stt = (r["stt_sell"] if side == "sell" else r["stt_buy"]) * turnover
    exch = r["exch"] * turnover
    sebi = r["sebi"] * turnover
    stamp = r["stamp_buy"] * turnover if side == "buy" else 0.0
    brok = float(brokerage_per_order)
    tax = r["tax"] * (brok + exch + sebi)
    return {"stt": stt, "exch": exch, "sebi": sebi, "stamp": stamp, "brokerage": brok, "tax": tax,
            "total": stt + exch + sebi + stamp + brok + tax}


def round_trip(entry_day, exit_day, entry_price: float, exit_price: float, qty: float, segment: str, *, sold_first: bool = False,
               settled: bool = False, brokerage_per_order: float = 0.0) -> dict[str, float]:
    """Open + close. `sold_first`: the position was opened with a sell (written option / short). `settled`: it ended by
    expiry settlement - no closing order (exercise STT is `exercise_cost`)."""
    open_side, close_side = ("sell", "buy") if sold_first else ("buy", "sell")
    a = leg_cost(entry_day, open_side, entry_price, qty, segment, brokerage_per_order)
    if settled:
        return a
    b = leg_cost(exit_day, close_side, exit_price, qty, segment, brokerage_per_order)
    return {k: a[k] + b[k] for k in a}


def spread_cost(day, short_px: float, long_px: float, qty: float, opening: bool = True, brokerage_per_order: float = 0.0) -> dict[str, float]:
    """A two-leg option spread opened (short sell + long buy) or closed (short buy + long sell)."""
    if opening:
        a, b = leg_cost(day, "sell", short_px, qty, "OPTION", brokerage_per_order), leg_cost(day, "buy", long_px, qty, "OPTION", brokerage_per_order)
    else:
        a, b = leg_cost(day, "buy", short_px, qty, "OPTION", brokerage_per_order), leg_cost(day, "sell", long_px, qty, "OPTION", brokerage_per_order)
    return {k: a[k] + b[k] for k in a}


def exercise_cost(day, intrinsic_per_unit: float, settle_price: float, qty: float) -> float:
    """A long option settled in the money is exercised: STT on the intrinsic value (from 1 Jun 2016; before that on the
    settlement price x quantity)."""
    if intrinsic_per_unit <= 0 or qty <= 0:
        return 0.0
    base = intrinsic_per_unit if pd.Timestamp(day).date() >= D(2016, 6, 1) else settle_price
    return _at(STT_EXERCISE, day) * base * qty
