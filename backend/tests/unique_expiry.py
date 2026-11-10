"""A future expiry no other test in this run uses: the instrument table is shared by the whole suite, and a random pick
(60 + 0..2000 days) gave two tests the same expiry, strike and right now and then - the second test's leg then resolved to
the first test's contract and had "No live price" (main CI 9 Nov: test_tm7 kill switch, test_term2 R1-R4). One counter for
the run: every call is a different day."""
import itertools
import os
from datetime import date, datetime, timedelta

from app.market_data.calendar import IST

# a distinct start per process, so two runs against one database do not meet either
_DAYS = itertools.count(60 + (os.getpid() % 50) * 400)


def unique_expiry() -> date:
    return datetime.now(IST).date() + timedelta(days=next(_DAYS))
