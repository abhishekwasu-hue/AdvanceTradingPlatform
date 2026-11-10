"""A future expiry no other test in this run uses: the instrument table is shared by the whole suite, and a random pick
(60 + 0..2000 days) gave two tests the same expiry, strike and right now and then - the second test's leg then resolved to
the first test's contract and had "No live price" (main CI 9 Nov: test_tm7 kill switch, test_term2 R1-R4). One counter for
the run: every call is a different day.

Calls are STEP days apart, not one: a test may also list its contracts a week later (expiry + 7 days, e.g. the roll test's
"next expiry"), and a roll picks the NEAREST later expiry with the same strike and right. With one-day steps another test's
contract 1-6 days later was nearer and the roll moved to it (CI 10 Nov: test_term2 roll preview, '2042-10-01' vs '-04').
So no other call lands within a week after any expiry this helper gives."""
import itertools
import os
from datetime import date, datetime, timedelta

from app.market_data.calendar import IST

STEP = 8                       # > the largest offset a test adds to its expiry (7 days)
# a distinct start per process, so two runs against one database do not meet either (1000 days = 125 calls apart)
_DAYS = itertools.count(60 + (os.getpid() % 20) * 1000, STEP)


def unique_expiry() -> date:
    return datetime.now(IST).date() + timedelta(days=next(_DAYS))
