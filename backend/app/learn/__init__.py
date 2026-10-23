"""Readiness C13 (docs/owner-orders/ATP_SESSION_PRODUCTION_READINESS.md C13): the Learn section and the public
marketing pages.

* Learn (`ui_v2` + `learn`, OFF): short lessons in plain English (`lessons.json`), the glossary (frontend) and video links
  Abhi sets in the admin console (`settings.py`, empty by default).
* Marketing pages (`marketing_site`, OFF, read globally because a visitor has no organisation): Features, How it works,
  About (`site.json`), served to the public front page with its SEO tags.

Education only: the idea library's content guard runs over every line (tests). Nothing here reads or places an order.
"""
FLAG = "learn"
PAGE_FLAG = "ui_v2"
SITE_FLAG = "marketing_site"
