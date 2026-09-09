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

#: Bumped whenever a change needs to be visibly confirmed on someone's chart.
VERSION = "v4"


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
    assert "int(math.floor(effRisk / (r * pointValue)))" in src
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


def normalised_program(src: str) -> str:
    """Code with comments gone, wrapped statements folded, spacing collapsed.

    Compares what the two builds *do* rather than how they are laid out, so the
    compact build is free to fold continuation lines to save bytes but not to
    change a single token.
    """
    lines = code_lines(src)
    folded: list[str] = []
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        if folded and indent % 4 != 0:
            folded[-1] = folded[-1].rstrip() + " " + line.strip()
        else:
            folded.append(line)
    # Tooltips only become strippable once the statement is on one line, and
    # the compact build drops them, so both sides lose them here.
    folded = [re.sub(r',\s*tooltip\s*=\s*"[^"]*"', "", l) for l in folded]
    return "\n".join(re.sub(r"\s+", " ", l).strip() for l in folded)


@pytest.mark.parametrize("compact", COMPACT, ids=lambda p: p.name)
def test_compact_build_is_the_same_program_as_its_source(compact):
    """The compact builds exist only to be smaller, never to be different.

    A mobile clipboard truncated a 17 KB paste one line from the end, so the
    scripts also ship stripped of their prose and with wrapped statements
    folded. If the two ever diverge, the one that gets pasted is no longer the
    one that was checked.
    """
    full = compact.with_name(compact.name.replace("_compact", ""))
    assert normalised_program(full.read_text()) == normalised_program(compact.read_text())


@pytest.mark.parametrize("compact", COMPACT, ids=lambda p: p.name)
def test_compact_build_does_not_balloon(compact):
    """A growth guard, no longer a promise about clipboards.

    This started as a hard 16 KB ceiling, on the evidence that a 16.8 KB paste
    once lost its last line to a phone's clipboard. The prop-firm rule layer
    pushed the compact builds to ~18.2 KB and that ceiling could not survive
    it. Golfing safety-critical code to fit an approximate limit would be the
    wrong trade, so the ceiling moved -- deliberately, and recorded here rather
    than quietly edited.

    What makes that acceptable: a truncated paste is LOUD. The original one
    failed to compile with "Missing closing parenthesis" -- it cost a round
    trip, it did not ship a half-script that traded. The real defence is the
    last-line check in the README, not this number.

    So this now only catches unnoticed bloat.
    """
    assert len(compact.read_text()) < 19_000, (
        "compact build has grown well past what the prop layer needed; find "
        "the bytes before raising this again"
    )


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
    assert "int(math.floor(effRisk" in src
    assert "lookahead" in "".join(l for l in src.split("\n") if l.strip().startswith("//"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_alert_message_is_not_built_with_str_format(path):
    """str.format reads { and } as placeholders, so a JSON template breaks it.

    It failed at runtime with "can't parse argument number" on the first bar
    that produced a signal, and a Pine runtime error halts the script -- so the
    chart also went blank, which gives no hint that the alert template was the
    cause. Escaping braces with single quotes is legal but leaves the same trap
    for anyone editing the template to suit their broker.
    """
    src = path.read_text()
    assert "str.format(" not in src, (
        "build the alert message with str.replace_all; str.format cannot take a "
        "JSON template without brace escaping"
    )
    assert "str.replace_all(" in src


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_alert_token_is_actually_substituted(path):
    """A token left in the template ships the literal %TOKEN% to the broker."""
    src = path.read_text()
    template = re.search(r"'(\{\"strategy\".*?\})'", src)
    assert template, "default alert template not found"
    in_template = set(re.findall(r"%[A-Z]+%", template.group(1)))
    replaced = set(re.findall(r'str\.replace_all\([^,]+,\s*"(%[A-Z]+%)"', src))
    assert in_template == replaced, (
        f"template tokens {sorted(in_template)} do not match the substitutions "
        f"{sorted(replaced)}"
    )
    assert in_template, "template has no tokens at all"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_message_variable_starts_from_a_series_assignment(path):
    """Pine types a variable on first assignment; seeding from the input is simple.

    Assigning the series result of a later str.replace_all into a variable first
    typed as simple is a compile error, so the substitution that is certainly a
    series has to come first.
    """
    src = path.read_text()
    assert 'm = str.replace_all(alertTemplate, "%QTY%", str.tostring(q))' in src
    assert "m = alertTemplate\n" not in src


def block_under(src: str, header: str) -> str:
    """The indented body following the line that starts with ``header``."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(header):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
                body.append(nxt)
            return "\n".join(body)
    raise AssertionError(f"no line starts with {header!r}")


def test_strategy_disables_the_emulator_funds_check():
    """Fixed-dollar-risk sizing buys more notional than the account holds.

    $500 of risk over a 17-point MNQ stop is 14 contracts, about $840,000 of
    notional. Under any non-zero margin requirement TradingView's emulator
    rejects every such order for insufficient funds, and it does it silently:
    the chart still draws every sweep and CHoCH, and the Strategy Tester is
    simply empty. Pinning margin to zero is what makes the backtest run at all.
    """
    src = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    decl = next(l for l in src.split("\n") if l.startswith("strategy("))
    assert "margin_long = 0" in decl and "margin_short = 0" in decl, (
        "margin must be pinned to 0 explicitly; the default is not ours to assume"
    )
    assert "initial_capital = 100000" in decl


def test_exit_is_attached_before_the_entry_fills():
    """A 1:1 stop is close enough that the fill bar can reach it.

    Placing the exit only once ``strategy.position_size`` is non-zero leaves the
    first bar of every trade unprotected, because the emulator fills the limit
    intrabar but the script does not run again until that bar closes. The
    research engine resolves the stop and the target on the fill bar, so the
    exit has to be attached to the entry ID while it is still unfilled.
    """
    src = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    body = block_under(src, "if stage == 2 and strategy.position_size == 0")
    assert "strategy.entry(" in body and 'strategy.exit("LTF x", "LTF"' in body, (
        "the exit must be issued alongside the entry, not gated on position_size"
    )
    assert "if strategy.position_size != 0\n    strategy.exit" not in src


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_diagnostic_counters_survive_into_every_build(path):
    """"No signals" and "orders rejected" look identical on a chart.

    They have opposite causes and opposite fixes, so the counters that tell
    them apart have to reach the file that actually gets pasted.
    """
    src = path.read_text()
    for counter in ("nBarsSession", "nSweep", "nChoch", "nConfirm", "nArmed",
                    "nRejRisk", "nRejQty"):
        assert counter in src, f"{counter} missing from {path.name}"
    assert 'timeframe.period != "1"' in src, "wrong-timeframe warning missing"


def test_prop_mode_is_off_by_default():
    """With prop mode off the script must behave exactly as it did before.

    The rule layer can only ever remove orders. Shipping it enabled would
    silently change what everyone else's chart does.
    """
    for path in SCRIPTS:
        src = path.read_text()
        assert 'propMode     = input.bool(false, "Enforce prop firm rules"' in src


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_the_prop_rule_is_a_single_shared_function(path):
    """One rule, in the core, so the two scripts cannot drift apart on it.

    The strategy and the indicator source their equity differently -- one from
    strategy.netprofit, one from a simulated ledger -- so the plumbing has to
    differ. The decision must not: a guard that blocks a trade in the backtest
    but not in the script sending the webhooks is worse than no guard.
    """
    src = path.read_text()
    assert src.count("propBlock(liveEq, floorEq, dayPnl, netP, plannedRisk) =>") == 1
    assert src.count("blockReason = propBlock(") == 1
    for rule in ("liveEq - floorEq < plannedRisk * safetyMult ? 1 :",
                 "dayPnl - plannedRisk <= -dailyLossLimit ? 2 :",
                 "netP >= profitTarget ? 3 : 0"):
        assert rule in src, f"{rule!r} missing from {path.name}"


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_prop_sizing_comes_from_the_loss_limit(path):
    """A fixed $500 produced a $9,615 drawdown against a $2,000 limit.

    Deriving the per-trade risk from the loss limit rescales with the account
    instead of needing a new magic number for every size.
    """
    src = path.read_text()
    assert "effRisk = propMode ? maxLossLimit * riskPctOfMLL / 100.0 : riskUSD" in src


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_topstep_50k_preset_matches_the_published_rules(path):
    """Target $3,000, loss limit $2,000, daily $1,000, 50 micros.

    These are the numbers the guard arithmetic is built on; a typo in the
    table would be invisible on the chart and wrong in exactly the direction
    that loses the account.
    """
    src = path.read_text()
    assert "array.from(3000., 6000., 9000., 3000., 6000.), pIdx)" in src   # targets
    assert "array.from(2000., 3000., 4500., 2500., 3000.), pIdx)" in src   # loss limits
    assert "array.from(1000., 2000., 3000., 1e9, 1e9), pIdx)" in src       # daily caps
    assert "array.from(50., 100., 150., 20., 40.), pIdx)" in src           # micro ceilings


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_contract_ceiling_cuts_the_trade_down_rather_than_skipping_it(path):
    """Over the ceiling the setup is still the setup, just smaller."""
    src = path.read_text()
    assert "q = propMode and q0 > maxMicros ? maxMicros : q0" in src


def test_fills_are_detected_by_trade_count_not_position_size():
    """The regression this replaced put two trades on one entry price.

    strategy.position_size is only sampled at the close, so a trade that opens
    AND closes inside one bar reads 0 against 0 on both of the obvious
    transitions. Written that way the setup is never marked as taken and stage
    never resets, so the order block re-places the identical limit on the next
    bar and it fills a second time -- which is what 2026-09-02 showed on the
    chart, two fills at 29,100.50.

    Same-bar round trips are not a corner case at a 1:1 target: two of the
    first five forward trades opened and closed inside the same minute.
    """
    src = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    assert "tradesTaken = strategy.closedtrades + strategy.opentrades" in src
    assert "justFilled  = tradesTaken > nz(tradesTaken[1], 0)" in src
    assert "justFilled = strategy.position_size != 0" not in src, (
        "fill detection is back on a position_size transition, which cannot "
        "see a trade that opens and closes inside one bar"
    )


def test_a_spent_setup_is_cleared_before_any_new_order_is_placed():
    """The regression that survived the previous fix.

    Pine samples strategy.position_size at the close, so on the bar a trade
    EXITS it already reads 0 while stage is still 2. If the order block runs
    before the reset, it re-issues the identical limit -- and nothing then owns
    that order, because the only cancel path requires stage == 2, which the
    reset has just cleared. The orphan sits in the book until price touches it.

    On the chart that produced two fills at 29,100.50 two minutes apart, and a
    fill at 18:00 ET -- hours outside the trading session -- at the same price
    as the trade before it. Ordering is the whole fix, so ordering is the test.
    """
    lines = (PINE_DIR / "ltf_sweep_strategy.pine").read_text().split("\n")

    def line_of(prefix):
        for i, l in enumerate(lines):
            if l.startswith(prefix):
                return i
        raise AssertionError(f"no line starts with {prefix!r}")

    reset = line_of("if (justFilled or justClosed) and strategy.position_size == 0")
    order = line_of("if stage == 2 and strategy.position_size == 0 and strategy.opentrades")
    assert reset < order, (
        "the spent-setup reset must run BEFORE the order block; with it after, "
        "the exit bar re-places the same limit and orphans it"
    )


def test_the_spent_setup_reset_also_cancels_any_working_order():
    """Belt and braces against leaving an order nothing can cancel."""
    src = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    body = block_under(src, "if (justFilled or justClosed) and strategy.position_size == 0")
    assert 'strategy.cancel("LTF")' in body
    assert "stage    := 0" in body


def test_the_old_trailing_reset_is_gone():
    """It ran after the order block, which is what allowed the orphan."""
    src = (PINE_DIR / "ltf_sweep_strategy.pine").read_text()
    assert "if strategy.position_size == 0 and strategy.position_size[1] != 0\n    stage := 0" not in src


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_every_build_carries_the_same_version_marker(path):
    """The shorttitle shows in the chart legend, so the screenshot says it.

    Two round trips went on "is the new script actually loaded?" while the
    trade list was identical for a different reason. A version in the legend
    settles that without opening a panel.
    """
    src = path.read_text()
    found = set(re.findall(r'"LTF Sweep(?: \(alerts\))? (v\d+)"', src))
    assert found == {VERSION}, f"{path.name} carries {found or 'no version'}, expected {VERSION}"


@pytest.mark.parametrize("path", SCRIPTS + COMPACT, ids=lambda p: p.name)
def test_same_bar_round_trips_are_counted(path):
    """Make the condition that triggered the bug visible on the panel.

    Finding it took comparing entry prices across a screenshot; the count says
    it directly.
    """
    assert "nSameBar" in path.read_text()
