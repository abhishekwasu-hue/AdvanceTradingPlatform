"""Analyze a chart - test doubles. Everything here is made up: a synthetic chart drawn in code (no real chart, no real
data) and a fake analysis engine whose "method" is a placeholder (the real one lives only in the private package).

`FakeProvider` implements the public `AnalysisProvider` interface AND the optional hybrid entry point (`HybridProvider`,
M59 CR-4): its instructions are a fixed placeholder string, and it parses the reply as JSON in the output contract's
shape. `ImageOnlyFakeProvider` has no hybrid entry point (an engine that serves the code-only hybrid result). `answer(kind)` builds three made-up answers - a formed
setup, a forming setup and no setup - in image pixel space for a `WIDTH` x `HEIGHT` image.
"""
import io
import json
from typing import Any, Dict

from PIL import Image, ImageDraw

from app.chart_analyze.provider import Instructions

WIDTH, HEIGHT = 640, 360
PLACEHOLDER = "PLACEHOLDER INSTRUCTIONS FOR TESTS - the real instructions are in the private package"


def synthetic_chart(fmt: str = "PNG", width: int = WIDTH, height: int = HEIGHT, **save: Any) -> bytes:
    """A drawn placeholder 'chart': a grid and twenty made-up candles. Not market data."""
    img = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for x in range(0, width, 64):
        draw.line([(x, 0), (x, height)], fill=(230, 230, 230))
    level = height // 2
    for i in range(20):
        x = 20 + i * (width - 40) // 20
        step = ((i * 37) % 11) - 5
        top, bottom = level - 30 + step * 3, level + 10 + step * 2
        color = (22, 150, 92) if i % 3 else (214, 56, 56)
        draw.line([(x + 6, top - 10), (x + 6, bottom + 10)], fill=(60, 60, 60))
        draw.rectangle([x, top, x + 12, bottom], fill=color)
        level += -4 if i < 12 else 6
    out = io.BytesIO()
    img.save(out, format=fmt, **save)
    return out.getvalue()


def answer(kind: str = "formed") -> Dict[str, Any]:
    status = {"formed": "formed", "forming": "forming", "none": "none"}[kind]
    setup: Dict[str, Any] = {
        "side": "none" if status == "none" else "bull_put", "label": None if status == "none" else "A", "status": status,
        "waiting_for": "a close back above the made-up area" if status == "forming" else None,
        "trigger": {"text": "a close above the made-up trigger line", "price": 120.5},
        "invalidation": {"text": "a close below the made-up area", "price": 101.0},
        "next_areas": [{"text": "the made-up upper area", "price": 140.0}],
    }
    return {
        "recognised": {"symbol": "DEMO", "exchange": "NSE", "timeframe": "15m", "first_bar": "made-up start", "last_bar": "made-up end"},
        "story": ["Made-up story: the drawn candles rise, pause and rise again.", "Made-up story, part two."],
        "trend": {"side": "up", "text": "made-up higher highs and higher lows"},
        "current_state": {"phase": "pullback" if status != "none" else "range", "text": "made-up current state"},
        "rsi": {"divergence": "none", "text": "made-up: no divergence drawn"},
        "setup": setup,
        "evidence": [
            {"name": "Trend side", "present": True, "reason": "made-up reason one"},
            {"name": "Pullback", "present": status != "none", "reason": "made-up reason two"},
            {"name": "Confluence", "present": False, "reason": "made-up reason three"},
        ],
        "scenarios": {"primary": "Made-up primary scenario.", "alternative": "Made-up alternative scenario.",
                      "primary_invalidated_at": {"text": "below the made-up area", "price": 101.0}},
        "what_to_wait_for": None if status == "formed" else {
            "condition": "a made-up close above the trigger line", "alert": {"kind": "above", "price": 120.5}},
        "htf_context": [{"timeframe": "1h", "trend": "up", "nearest_areas": [{"side": "buyer", "state": "fresh", "price_low": 98.0, "price_high": 101.0}]}],
        "key_levels": [{"kind": "area", "name": "Made-up buyer area", "side": "buyer", "state": "tested", "price_low": 98.0, "price_high": 101.0},
                       {"kind": "line", "name": "Made-up line", "price_low": 120.5, "price_high": 120.5}],
        "annotations": {
            "swings": [{"label": "HL", "x": 120, "y": 250, "price": 100.0}, {"label": "HH", "x": 400, "y": 80, "price": 130.0}],
            "areas": [{"id": "a1", "side": "buyer", "state": "tested", "x0": 100, "y0": 240, "x1": 600, "y1": 270, "price_low": 98.0, "price_high": 101.0,
                       "note": "made-up area"}],
            "lines": [{"id": "l1", "name": "made-up line", "x0": 50, "y0": 150, "x1": 600, "y1": 150, "touches": [{"x": 200, "y": 150}], "note": "made-up"}],
            "rsi_divergence": None,
            "commitment_candle": {"x": 300, "y": 200, "note": "made-up"},
            "ltf_choch": None,
            "setup_lines": [{"kind": "trigger", "y": 150, "price": 120.5}, {"kind": "invalidation", "y": 270, "price": 101.0}],
        },
    }


class FakeProvider:
    name = "fake-engine"
    version = "0.0-test"
    method_version = "test-hash"

    def image_only_instructions(self, *, width: int, height: int) -> Instructions:
        return Instructions(system=PLACEHOLDER, user=f"image {width}x{height}", prompt_version="fake-v1")

    def parse_image_only(self, reply: str, *, width: int, height: int) -> Dict[str, Any]:
        return json.loads(reply)

    # M59 CR-4: the optional hybrid entry point. It records the facts it was handed (tests check they are generic).
    def hybrid_instructions(self, *, width: int, height: int, facts: Dict[str, Any]) -> Instructions:
        self.facts_seen = facts
        return Instructions(system=PLACEHOLDER, user=f"hybrid {width}x{height} {facts.get('symbol')} {facts.get('timeframe')}",
                            prompt_version="fake-hybrid-v1")

    def parse_hybrid(self, reply: str, *, facts: Dict[str, Any]) -> Dict[str, Any]:
        out = json.loads(reply)
        out.setdefault("version", "0.0-test-hybrid")
        out.setdefault("method_version", "test-hybrid-hash")
        return out


class ImageOnlyFakeProvider:
    """An engine without the hybrid entry point."""
    name = "fake-engine"
    version = "0.0-test"
    method_version = "test-hash"

    def image_only_instructions(self, *, width: int, height: int) -> Instructions:
        return Instructions(system=PLACEHOLDER, user=f"image {width}x{height}", prompt_version="fake-v1")

    def parse_image_only(self, reply: str, *, width: int, height: int) -> Dict[str, Any]:
        return json.loads(reply)
