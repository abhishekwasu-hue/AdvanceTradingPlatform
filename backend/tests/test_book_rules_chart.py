"""Owner 18 Nov: a book strategy's rules as bullets and the read's levels on the chart. Made-up specs only (public repo)."""
from __future__ import annotations

import base64

from app.chart_reader import loader, service

SPEC = {
    "id": "DEMO_ONE", "summary": "Placeholder  summary\nline.", "sides": ["long", "other"],
    "conditions": [{"id": "S1", "text": "Placeholder rule one", "needs": "price", "source": "p.1"},
                   {"id": "S2", "text": "Placeholder rule two", "needs": "two charts", "source": "p.2"},
                   {"id": "", "text": "dropped"}, "not a dict"],
    "trigger": {"text": "Placeholder signal", "source": "p.3", "stated": False},
    "invalidation": [{"text": "Placeholder wrong", "source": "p.4"}, {"text": ""}],
    "not_coded": ["Placeholder time rule", ""],
}


def test_public_rules_shape():
    r = service.public_rules(SPEC)
    assert r is not None
    assert r["summary"] == "Placeholder summary line."
    assert r["sides"] == ["long"]
    assert [c["id"] for c in r["conditions"]] == ["S1", "S2"]
    assert r["conditions"][1]["two_chart"] is True and r["conditions"][0]["page"] == "p.1"
    assert r["trigger"] == {"text": "Placeholder signal", "page": "p.3", "stated": False}
    assert r["invalidation"] == [{"text": "Placeholder wrong", "page": "p.4"}]
    assert r["not_checked"] == ["Placeholder time rule"]
    assert service.public_rules(None) is None
    assert service.public_rules({"conditions": []}) is None


class _Reader:
    name, version, ABHI = "fake", "1", "ABHI"

    def strategies(self):
        return [{"id": "ABHI", "name": "Mine"}, {"id": "DEMO_ONE", "name": "One"}, {"id": "DEMO_TWO", "name": "Two"}]

    def two_chart_conditions(self, strategy):
        return []

    def read(self, **kw):
        return {}

    def strategy_rules(self, strategy):
        if strategy == "DEMO_TWO":
            raise FileNotFoundError("no spec")
        return SPEC


def test_strategy_list_adds_rules_only_to_books_that_have_them():
    loader.set_for_tests(_Reader())
    try:
        out = {s["id"]: s for s in service.strategy_list(loader.load())}
    finally:
        loader.reset_for_tests()
    assert "rules" not in out.get("ABHI", {}) and "rules" not in out["DEMO_TWO"]
    assert out["DEMO_ONE"]["rules"]["conditions"][0]["text"] == "Placeholder rule one"


def test_fake_reader_without_rules_gives_none():
    class Bare(_Reader):
        strategy_rules = None
    loader.set_for_tests(Bare())
    try:
        assert loader.rules("DEMO_ONE") is None
    finally:
        loader.reset_for_tests()


def _bars(n=40):
    out, p = [], 100.0
    for i in range(n):
        c = p + (1 if i % 3 else -1.5)
        out.append({"o": p, "h": max(p, c) + 0.5, "l": min(p, c) - 0.5, "c": c})
        p = c
    return out


def test_book_bands_numbers_levels_and_skips_paired_or_priceless():
    read = {"levels": [{"price_low": 101, "price_high": 102, "chart": "DEMO Daily"},
                       {"price_low": None, "price_high": None, "chart": ""},
                       {"price_low": 99, "price_high": None, "chart": "DEMO Daily"},
                       {"price_low": 90, "price_high": 91, "chart": "PAIR Daily (paired chart, same bars)"}]}
    assert service.book_bands(read) == [{"tag": "L1", "low": 101.0, "high": 102.0}, {"tag": "L3", "low": 99.0, "high": 99.0}]
    assert service.book_bands(None) == []


def test_book_chart_png_only_for_an_ok_book_read_with_levels():
    bars = _bars()
    chart = service.Chart(label="DEMO Daily", symbol="DEMO", timeframe="1d", paired=False, png=b"", fingerprint="f",
                          facts={"bars": bars, "last_close": bars[-1]["c"]})
    ok = {"status": "ok", "read": {"levels": [{"price_low": bars[-1]["c"] - 2, "price_high": bars[-1]["c"] - 1, "chart": ""}]}}
    got = service.book_chart(ok, [chart], "DEMO_ONE", "ABHI")
    assert got is not None and got["tags"] == ["L1"] and got["label"] == "DEMO Daily"
    assert base64.b64decode(got["png_base64"]).startswith(b"\x89PNG")
    assert service.book_chart(ok, [chart], "ABHI", "ABHI") is None
    assert service.book_chart({**ok, "status": "refused"}, [chart], "DEMO_ONE", "ABHI") is None
    assert service.book_chart({"status": "ok", "read": {"levels": []}}, [chart], "DEMO_ONE", "ABHI") is None
    assert service.book_chart(ok, [], "DEMO_ONE", "ABHI") is None
