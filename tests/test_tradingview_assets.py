"""Checks on the Pine Script files that do not need a Pine interpreter.

Pine cannot be compiled here, so the scripts ship unexecuted. These tests lock
in the properties that were verified by hand, above all the one that actually
broke: the files must be pure ASCII.

The scripts' only delivery route is copy-paste into TradingView's Pine Editor,
and that route mangles UTF-8. An em dash inside the title string became a
curly closing quote, which terminated the string literal early and produced a
syntax error reported four lines away from the real cause. Decorative
characters in a file that has to survive a clipboard are not worth the risk.
"""

from __future__ import annotations

import pathlib
import re

import pytest

PINE_DIR = pathlib.Path(__file__).resolve().parent.parent / "tradingview"
SCRIPTS = sorted(p for p in PINE_DIR.glob("*.pine") if not p.stem.endswith("_compact"))
COMPACT = sorted(PINE_DIR.glob("*_compact.pine"))


def strip_comments_and_strings(src: str) -> str:
    src = re.sub(r'"[^"\n]*"', '""', src)
    return "\n".join(line.split("//")[0] for line in src.split("\n"))


def test_both_scripts_exist():
    assert {p.name for p in SCRIPTS} == {
        "ltf_sweep_strategy.pine", "ltf_sweep_indicator.pine"
    }
    assert {p.name for p in COMPACT} == {
        "ltf_sweep_strategy_compact.pine", "ltf_sweep_indicator_compact.pine"
    }


def code_lines(src: str) -> list[str]:
    """Executable lines only: comments and blank lines removed."""
    out = []
    for line in src.split("\n"):
        if line.strip().startswith("//"):
            continue
        line = re.sub(r"\s+//.*$", "", line).rstrip()
        if line.strip():
            out.append(line)
    return out


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_script_is_pure_ascii(path):
    """The regression. A single non-ASCII byte can break the paste."""
    raw = path.read_text(encoding="utf-8")
    offenders = {}
    for lineno, line in enumerate(raw.split("\n"), 1):
        for ch in line:
            if ord(ch) > 127:
                offenders.setdefault(f"U+{ord(ch):04X} {ch!r}", []).append(lineno)
    assert not offenders, (
        f"{path.name} contains non-ASCII characters, which the clipboard path "
        f"into the Pine Editor corrupts: {offenders}"
    )


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_declares_pine_v6(path):
    assert path.read_text().startswith("//@version=6")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_delimiters_balance(path):
    code = strip_comments_and_strings(path.read_text())
    assert code.count("(") == code.count(")")
    assert code.count("[") == code.count("]")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_higher_timeframe_request_is_the_non_repainting_idiom(path):
    """`expr[1]` together with lookahead_on returns the last CLOSED 15m bar.

    TradingView's documentation states the two are interdependent and neither
    can be removed. Dropping either reintroduces lookahead bias into the
    liquidity pool, which is the input the whole model rests on.
    """
    src = path.read_text()
    calls = re.findall(r"request\.security\([^\n]*", src)
    assert len(calls) == 2, f"expected two pool lookups, found {len(calls)}"
    for call in calls:
        assert "[1]" in call, f"missing the [1] offset: {call}"
        assert "lookahead = barmerge.lookahead_on" in call, f"missing lookahead_on: {call}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_sweep_cannot_confirm_itself_on_its_own_bar(path):
    """Ports `confirmed_at > sweep_confirmed` from strategies/ltf_sweep.py.

    Without this a sweep and a CHoCH printed by the same 1-minute bar would
    both fire, entering on a close that had not happened when the sweep was
    read.
    """
    src = path.read_text()
    assert "afterSweep = stage == 1 and bar_index > sweepBar" in src
    # It must gate the confirmation and the FVG search, not just one of them.
    assert re.search(r"^confirmed = afterSweep and", src, re.M)
    assert src.count("if afterSweep and setupDir ==") == 2


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_position_size_rounds_down(path):
    """Rounding up would breach the risk budget on every awkward stop."""
    src = path.read_text()
    assert "int(math.floor(riskUSD / (r * pointValue)))" in src
    assert "math.ceil" not in src
    assert "math.round" not in src


def test_the_two_scripts_share_one_core_verbatim():
    """Everything from the inputs to the trade pricing must be identical.

    The strategy version is used to check the logic and the indicator version
    to trade it; if their cores drift, that check stops meaning anything.
    """
    strat = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    indic = (PINE_DIR / "ltf_sweep_indicator.pine").read_text()
    core_s = strat[strat.index("// -- inputs"):strat.index("// -- orders --")]
    core_i = indic[indic.index("// -- inputs"):indic.index("// -- simulated position")]
    assert core_s.strip() == core_i.strip()
    assert len(core_s.strip().split("\n")) > 150, "core looks truncated"


@pytest.mark.parametrize("compact", COMPACT, ids=lambda p: p.name)
def test_compact_build_matches_its_source_line_for_line(compact):
    """The compact builds exist only to be smaller, never to be different.

    A mobile clipboard truncated a 17 KB paste one line from the end, so the
    scripts also ship stripped of their prose. If the two ever diverge, the
    one that gets pasted is no longer the one that was checked.
    """
    full = compact.with_name(compact.name.replace("_compact", ""))
    assert code_lines(full.read_text()) == code_lines(compact.read_text())


@pytest.mark.parametrize("compact", COMPACT, ids=lambda p: p.name)
def test_compact_build_is_smaller_and_still_ascii(compact):
    full = compact.with_name(compact.name.replace("_compact", ""))
    assert len(compact.read_text()) < len(full.read_text())
    assert all(ord(c) < 128 for c in compact.read_text())


@pytest.mark.parametrize("compact", COMPACT, ids=lambda p: p.name)
def test_compact_build_keeps_the_invariants_that_fail_silently(compact):
    """Stripping prose must not strip the two warnings that matter.

    Lookahead bias and same-bar confirmation both produce a script that runs
    fine and reports numbers that are wrong, so the comments guarding them earn
    their bytes even in the small build.
    """
    src = compact.read_text()
    assert src.startswith("//@version=6")
    assert "lookahead = barmerge.lookahead_on" in src
    assert "afterSweep = stage == 1 and bar_index > sweepBar" in src
    assert "int(math.floor(riskUSD" in src
    assert "lookahead" in "".join(l for l in src.split("\n") if l.strip().startswith("//"))
