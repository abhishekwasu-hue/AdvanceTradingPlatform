"""M94 PR-6 (docs/design/M94_INTRADAY_PACK.md F15 / F16; OPEN_QUESTIONS M94-Q10): a template's "how it works" screen and
its chart overlay, behind `intraday_pack` (family H, the intraday templates; generic for every template of the family).

* `how_it_works(template, ...)` - the screen's content, every word from the template's own data: 3-5 short lines (the
  idea, the sentence a signal reads, where the idea is wrong, the timeframes, the exit options), the settings, the setup
  scanner (the template's own setup expression with its default settings, opened in the Screener), the footer, and a
  schematic.
* `schematic(template)` - the drawing: the template's default settings run by the engine on the same synthetic random
  walks the template tests use (`app.quality.lookahead.synthetic_bars`, the PR-4 tests' `_walk` sizes), and the first
  signal found drawn as candles with its setup bar, its trigger bar and its invalidation. The prices are rescaled to
  0-100: the drawing carries no real market, no symbol, no date and no price (CLAUDE.md section 3: education surfaces).
* `overlay_parts(recipe)` - each compiled part (setup, trigger, invalidation) as a condition the screener can evaluate:
  the setup and trigger exactly as compiled; the invalidation as "the close is beyond the level" (long: below, short:
  above). The overlay route runs each through the ONE shared M91 evaluation (`watch_screen.screen_marks`), so the chart
  and a screen of the same part can never disagree (tested). An invalidation kept by the engine since the setup bar
  (`SetupExtreme`) has no expression: its level is shown at each signal instead (the engine's own value).

Informational only: nothing here places, proposes or changes an order, and nothing is stored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from app.strategy_templates import exits as exit_settings
from app.strategy_templates import language
from app.strategy_templates.compile import SIDES, Part, Recipe, TemplateError, compile_part, compile_recipe, resolve_settings
from app.strategy_templates.schema import Setup, Template, leaves

# M94-Q10: the order's sentence, exactly, on every M94 surface (the older line stays where it is, F14)
FOOTER = "Information and education only, not investment advice."
PAGE_FAMILIES = frozenset({"H"})        # the families with a "how it works" screen and the template overlay (M94)
# The bars of the synthetic random walk per timeframe - the PR-4 template tests' own `_walk` sizes (tests/test_m94_pr4_templates.py)
WALK_BARS: dict[str, int] = {"1m": 9000, "3m": 4000, "5m": 3000, "15m": 1500}
WALK_SEEDS = 12                         # the seeds the tests search (`_seed_with_signals`)
BEFORE, AFTER = 24, 8                   # the drawing: bars before the setup bar and after the trigger bar
MAX_WAIT = 36                           # a setup this many bars or less before its trigger is preferred for the drawing
SCALE = 100.0                           # the drawing's prices are rescaled to 0..SCALE
PART_NAMES: tuple[str, ...] = ("setup", "trigger", "invalidation")
PART_WORDS = {"setup": "Setup", "trigger": "Trigger", "invalidation": "Wrong beyond"}
SIDE_WORDS = {"long": "upward", "short": "downward"}
TARGET_OPTION_WORDS = {"NONE": "no fixed level", "R_MULTIPLE": "a multiple of your risk", "TEMPLATE_EXIT": "the template's exit signal",
                       "WAVE_PROJECTION": "the wave projection", "TEMPLATE_LEVEL": "a level"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WRONG = "The idea is wrong"


def has_page(template: Template) -> bool:
    return template.family in PAGE_FAMILIES


def _runnable(template: Template) -> list[str]:
    from app.strategy_templates.adapter import ENGINE_LABEL
    return [tf for tf in template.allowed_timeframes if tf in ENGINE_LABEL]


# ------------------------------------------------------------------------------------------------------ the schematic
def _pick(series: Any, has_trigger: bool, size: int) -> int | None:
    """The signal bar the drawing shows: the first with room around it whose setup came shortly before its trigger (the
    same bar when the setup event is the signal); else the first signal with room around it."""
    bars = [t for t in series.signal_bars() if t - BEFORE >= 0 and t + AFTER < size]
    for t in bars:
        wait = t - int(series.setup_bar[t])
        if (has_trigger and 1 <= wait <= MAX_WAIT) or (not has_trigger and wait == 0):
            return t
    return bars[0] if bars else None


def schematic(template: Template) -> dict[str, Any] | None:
    """The drawing of one signal of the template (module docstring), or None when no walk gives one."""
    return _schematic_cached(template.id, template.version)


@lru_cache(maxsize=64)
def _schematic_cached(template_id: str, version: int) -> dict[str, Any] | None:
    from app.quality.lookahead import synthetic_bars
    from app.strategy_templates import engine, library
    template = library.get_version(template_id, version)
    runnable = _runnable(template)
    if not runnable:
        return None
    tf = runnable[0]
    try:
        recipe = compile_recipe(template, resolve_settings(template, tf))
    except TemplateError:
        return None
    for seed in range(WALK_SEEDS):
        df = synthetic_bars(WALK_BARS.get(tf, 900), tf=tf, seed=seed)
        series = engine.compute(recipe, {tf: df})
        t = _pick(series, recipe.has_trigger, len(df))
        if t is None:
            continue
        s0 = int(series.setup_bar[t])
        lo, hi = max(0, min(s0, t) - BEFORE), t + AFTER
        window = df.iloc[lo:hi + 1]
        inv = float(series.invalidation[t])
        top = float(max(window["high"].max(), inv))
        bottom = float(min(window["low"].min(), inv))
        span = (top - bottom) or 1.0

        def scale(v: float) -> float:
            return round((v - bottom) / span * SCALE, 2)
        candles = [[scale(o), scale(h), scale(lw), scale(c)] for o, h, lw, c in
                   window[["open", "high", "low", "close"]].to_numpy(dtype=float)]
        return {"timeframe": tf, "timeframe_words": language.TIMEFRAME_WORDS[tf],
                "side": "long" if series.direction[t] > 0 else "short", "candles": candles,
                "setup": s0 - lo, "trigger": t - lo, "invalidation": scale(inv), "scale": SCALE,
                "same_bar": s0 == t, "seed": seed}
    return None


# ------------------------------------------------------------------------------------------------------------ the lines
def _idea_parts(idea: str) -> tuple[str, str | None]:
    sentences = [s.strip() for s in _SENTENCE.split(idea.strip()) if s.strip()]
    wrong = [s for s in sentences if s.startswith(_WRONG)]
    rest = [s for s in sentences if not s.startswith(_WRONG)]
    return " ".join(rest), (wrong[0] if wrong else None)


def lines(template: Template, reads: dict[str, str | None], exits: dict[str, Any] | None) -> list[dict[str, str]]:
    """3-5 short lines, each from the template's own data: the idea, the sentence a signal reads, where the idea is
    wrong, the timeframes and session, the exit options this template offers on shares."""
    idea, wrong = _idea_parts(template.idea)
    out = [{"key": "idea", "label": "The idea", "text": idea}]
    read = next((r for r in (reads.get("long"), reads.get("short")) if r), None)
    if read:
        out.append({"key": "reads", "label": "A signal reads", "text": read})
    if wrong:
        rest = wrong[len(_WRONG):].strip()
        out.append({"key": "wrong", "label": "The idea is wrong", "text": rest[:1].upper() + rest[1:]})
    runnable = _runnable(template)
    if runnable:
        words = [language.TIMEFRAME_WORDS[tf] for tf in runnable]
        bars = ", ".join(words[:-1]) + f" or {words[-1]}" if len(words) > 1 else words[0]
        bound = all(s.session_bound for s in leaves(template.setup) if isinstance(s, Setup))
        out.append({"key": "runs", "label": "Runs on",
                    "text": f"{bars} bars" + ("; an idea never outlives its session." if bound else ".")})
    if exits:
        offered = [TARGET_OPTION_WORDS[t] for t in exits["precedence"][exit_settings.UNDERLYING]["targets"] if t in TARGET_OPTION_WORDS]
        levels = [exit_settings.level_words(str(lv["value"])) for lv in exits.get("levels") or []]
        text = ", ".join(offered)
        if levels:
            text += " (" + ", ".join(levels) + ")"
        out.append({"key": "exits", "label": "Exit options", "text": f"Your own settings: {text}."})
    return out[:5]


# ------------------------------------------------------------------------------------------------------ the scanner
def scanner(template: Template) -> dict[str, dict[str, Any] | None]:
    """The template's setup as a screen, per side, with its default settings on its first timeframe - what the
    Screener opens as the template's setup scanner (the screener validates and gates it as any screen)."""
    out: dict[str, dict[str, Any] | None] = {"long": None, "short": None}
    runnable = _runnable(template)
    if not runnable:
        return out
    try:
        recipe = compile_recipe(template, resolve_settings(template, runnable[0]))
    except TemplateError:
        return out
    for side in recipe.settings.side_list():
        part = recipe.part("setup", side)
        if part is not None:
            out[side] = {"text": part.text, "params": dict(part.params), "base_tf": recipe.settings.timeframe}
    return out


# ------------------------------------------------------------------------------------------------------ the overlay
@dataclass
class OverlayPart:
    """One compiled part of one side as a condition (`part` None: the engine keeps the invalidation since the setup)."""
    name: str
    side: str
    part: Part | None

    @property
    def label(self) -> str:
        return f"{PART_WORDS[self.name]} ({SIDE_WORDS[self.side]})"


def overlay_parts(recipe: Recipe) -> list[OverlayPart]:
    """Setup, trigger and invalidation of every side the settings trade, in that order (module docstring)."""
    out: list[OverlayPart] = []
    for side in SIDES:
        if side not in recipe.settings.side_list():
            continue
        out.append(OverlayPart("setup", side, recipe.part("setup", side)))
        trigger = recipe.part("trigger", side)
        if trigger is not None:
            out.append(OverlayPart("trigger", side, trigger))
        inv = recipe.part("invalidation", side)
        if inv is not None:
            beyond = f"close {'<' if side == 'long' else '>'} ({inv.text})"
            out.append(OverlayPart("invalidation", side, compile_part(beyond, what=f"invalidation ({side})", want="bool",
                                                                      template=recipe.template, settings=recipe.settings)))
        elif recipe.invalidation_since_setup:
            out.append(OverlayPart("invalidation", side, None))
    return out


def loading_part(recipe: Recipe, parts: list[OverlayPart]) -> Part:
    """One condition that reads everything the parts read (their OR) - only to size and fetch the bars once."""
    texts = [f"({p.part.text})" for p in parts if p.part is not None]
    return compile_part(" OR ".join(texts), what="overlay", want="bool", template=recipe.template, settings=recipe.settings)


def signals_in(series: Any, index: Any, bars: int) -> list[dict[str, Any]]:
    """The engine's signals on the newest `bars` bars: the bar, the side, the setup bar and the invalidation level."""
    first = max(0, len(index) - bars)
    out = []
    for t in series.signal_bars():
        if t < first:
            continue
        out.append({"bar": index[t], "setup_bar": index[int(series.setup_bar[t])], "side": "long" if series.direction[t] > 0 else "short",
                    "invalidation": float(series.invalidation[t]) if np.isfinite(series.invalidation[t]) else None})
    return out


__all__ = ["FOOTER", "PAGE_FAMILIES", "WALK_BARS", "WALK_SEEDS", "has_page", "schematic", "lines", "scanner", "OverlayPart",
           "overlay_parts", "loading_part", "signals_in", "PART_WORDS", "SIDE_WORDS"]
