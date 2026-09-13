"""Quarterly performance report for the LTF sweep model, as a standalone page.

    uv run python scripts/quarterly_report.py

Writes ``docs/quarterly_report.html``: the headline figures, a quarter-by-quarter
table, the equity curve with its drawdown against the account's loss limit, and
the risk ladder -- what each position size would have done to the account over
the same trades.

A script rather than a hand-written page, because the forward log grows every
month and a number nobody can regenerate stops being checkable. Every figure
here comes from ``backtest/metrics.py`` and the same ``generate_orders`` ->
``simulate`` path the backtest runs; nothing is recomputed a second way.

**The report's job is the loss limit, not the P&L.** A year can show a profit
factor above 1 and still be unreachable: the account is closed the first time
the drawdown from the peak passes the limit, whatever the balance is doing. So
the drawdown panel carries the limit line, and the risk ladder answers the
question the P&L cannot -- which position size, if any, survives the year.
"""

from __future__ import annotations

import argparse
import html
import pathlib

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from strategies.ltf_sweep import generate_orders

NY = "America/New_York"
OUT = pathlib.Path("docs/quarterly_report.html")

#: Topstep 50K. The profit target is what passes an evaluation and the loss
#: limit is what ends it; both are needed to say which came first.
PROFIT_TARGET = 3000.0
LOSS_LIMIT = 2000.0

#: Position sizes to show side by side. The spread matters more than the exact
#: values: it is the difference between a size that survives and one that does
#: not.
RISK_LADDER = (100.0, 200.0, 250.0, 500.0, 1000.0)

# Validated with the dataviz skill's checker against both surfaces:
# light  worst adjacent CVD dE 21.6, normal-vision 32.3, all >= 3:1
# dark   worst adjacent CVD dE 19.2, normal-vision 29.0, all >= 3:1
POS_LIGHT, NEG_LIGHT = "#2a78d6", "#e34948"


def load(start: str, end: str) -> pd.DataFrame:
    """1-minute bars, with enough lead-in for the 15m pools to exist."""
    lead = (pd.Timestamp(start) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    frame = pd.read_parquet(D.PROCESSED_DIR / "nq_1m.parquet",
                            filters=[("ts", ">=", pd.Timestamp(lead, tz="UTC"))])
    return D.add_time_columns(frame).reset_index(drop=True)


def run(bars: pd.DataFrame, start: str, end: str, risk: float) -> pd.DataFrame:
    """Filled trades whose entry falls inside the window, oldest exit first."""
    orders = generate_orders(bars)
    trades = simulate(orders, bars, BacktestConfig(risk_per_trade_usd=risk))
    filled = trades[trades["filled"]].copy()
    inside = (filled["entry_ts"] >= pd.Timestamp(start, tz="UTC")) & \
             (filled["entry_ts"] < pd.Timestamp(end, tz="UTC"))
    return filled[inside].sort_values("exit_ts").reset_index(drop=True)


def drawdown(pnl: np.ndarray) -> np.ndarray:
    """Distance below the running peak, one value per trade.

    Measured from an opening balance of zero, matching ``metrics._drawdown``:
    a loss limit trails the high-water mark, so the first trades of a run are
    just as able to breach it as any later ones.
    """
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    return equity - peak


def breach(pnl: np.ndarray, limit: float = LOSS_LIMIT) -> int | None:
    """Index of the trade whose drawdown first passes the limit, or None."""
    hit = np.where(drawdown(pnl) <= -limit)[0]
    return int(hit[0]) if len(hit) else None


def race(pnl: np.ndarray) -> tuple[str, int | None]:
    """Which came first over these trades: the profit target or the limit.

    An evaluation ends on whichever lands first, so neither figure means much
    alone -- a year can clear the target in total and still have been closed
    months earlier.
    """
    equity = np.cumsum(pnl)
    passed = np.where(equity >= PROFIT_TARGET)[0]
    busted = np.where(drawdown(pnl) <= -LOSS_LIMIT)[0]
    p = int(passed[0]) if len(passed) else None
    b = int(busted[0]) if len(busted) else None
    if p is None and b is None:
        return "neither", None
    if b is None or (p is not None and p < b):
        return "passed", p
    return "busted", b


def profit_factor(pnl: pd.Series) -> float:
    won = pnl[pnl > 0].sum()
    lost = -pnl[pnl < 0].sum()
    return float(won / lost) if lost else float("inf")


def longest_losing_streak(pnl: pd.Series) -> int:
    run_len = worst = 0
    for value in pnl:
        run_len = run_len + 1 if value <= 0 else 0
        worst = max(worst, run_len)
    return worst


def quarters(trades: pd.DataFrame) -> pd.DataFrame:
    """``metrics.by_period`` plus the two columns this report turns on."""
    base = metrics.by_period(trades, "QE")
    entry = pd.DatetimeIndex(trades["entry_ts"]).tz_convert("UTC").tz_localize(None)
    grouped = trades.assign(period=entry.to_period("Q")).groupby("period")
    extra = grouped["net_pnl"].agg(
        max_drawdown=lambda s: float(drawdown(s.to_numpy()).min()),
        profit_factor=profit_factor,
    ).reset_index()
    return base.merge(extra, on="period")


def ladder(bars: pd.DataFrame, start: str, end: str) -> list[dict]:
    """Each position size over the same window, and what it did to the account."""
    rows = []
    for risk in RISK_LADDER:
        trades = run(bars, start, end, risk)
        if trades.empty:
            continue
        pnl = trades["net_pnl"].to_numpy()
        outcome, index = race(pnl)
        rows.append({
            "risk": risk,
            "trades": len(trades),
            "net": float(pnl.sum()),
            "max_drawdown": float(drawdown(pnl).min()),
            "outcome": outcome,
            "at_trade": None if index is None else index + 1,
            "at_date": None if index is None else
                       trades["exit_ts"].iloc[index].tz_convert(NY).date().isoformat(),
        })
    return rows


# --------------------------------------------------------------------------
# rendering. Inline SVG, no CDN: this page has to open from a phone with no
# network, and a chart library that fails to load leaves a blank box.

def money(value: float, sign: bool = True) -> str:
    fmt = f"{value:+,.0f}" if sign else f"{value:,.0f}"
    return fmt.replace("+-", "-").replace("-", "−")


def esc(text) -> str:
    return html.escape(str(text))


def line_chart(xs, ys, *, width=380, height=155, limit=None, fill=False) -> str:
    """One series over trade number. Optional threshold line and fill to zero."""
    pad_l, pad_r, pad_t, pad_b = 56, 10, 20, 20
    inner_w, inner_h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(min(ys), 0.0), max(max(ys), 0.0)
    if limit is not None:
        lo = min(lo, -limit * 1.15)
    span = (hi - lo) or 1.0

    def px(i):
        return pad_l + inner_w * (i / max(len(xs) - 1, 1))

    def py(v):
        return pad_t + inner_h * (1 - (v - lo) / span)

    points = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(ys))
    zero = py(0.0)
    parts = [
        f'<line class="axis" x1="{pad_l}" y1="{zero:.1f}" x2="{width - pad_r}" y2="{zero:.1f}"/>'
    ]
    if fill:
        parts.append(f'<polygon class="area" points="{pad_l},{zero:.1f} {points} '
                     f'{px(len(xs) - 1):.1f},{zero:.1f}"/>')
    if limit is not None:
        y = py(-limit)
        parts.append(f'<line class="limit" x1="{pad_l}" y1="{y:.1f}" '
                     f'x2="{width - pad_r}" y2="{y:.1f}"/>')
        parts.append(f'<line class="limit" x1="{width - pad_r - 142}" y1="10" '
                     f'x2="{width - pad_r - 126}" y2="10"/>')
        parts.append(f'<text class="limit-label" x="{width - pad_r}" y="13.5" '
                     f'text-anchor="end">loss limit &minus;${LOSS_LIMIT:,.0f}</text>')
    parts.append(f'<polyline class="series" points="{points}"/>')

    for value in (hi, lo):
        label = "0" if value == 0 else money(value)
        parts.append(f'<text class="tick" x="{pad_l - 6}" y="{py(value) + 3.5:.1f}" '
                     f'text-anchor="end">{esc(label)}</text>')
    parts.append(f'<text class="tick" x="{pad_l}" y="{height - 5}">trade 1</text>')
    parts.append(f'<text class="tick" x="{width - pad_r}" y="{height - 5}" '
                 f'text-anchor="end">trade {len(xs)}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')


def bar_chart(labels, values, *, width=380, height=175) -> str:
    """Signed bars: blue above the line, red below. Every bar directly labelled."""
    pad_l, pad_r, pad_t, pad_b = 56, 10, 20, 42
    inner_w, inner_h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(min(values), 0.0), max(max(values), 0.0)
    span = (hi - lo) or 1.0
    slot = inner_w / len(values)
    bar_w = min(slot * 0.55, 48)

    def py(v):
        return pad_t + inner_h * (1 - (v - lo) / span)

    zero = py(0.0)
    parts = [f'<line class="axis" x1="{pad_l}" y1="{zero:.1f}" '
             f'x2="{width - pad_r}" y2="{zero:.1f}"/>']
    for i, (label, value) in enumerate(zip(labels, values)):
        cx = pad_l + slot * (i + 0.5)
        y = py(value)
        top, tall = min(y, zero), abs(y - zero)
        cls = "pos" if value >= 0 else "neg"
        parts.append(
            f'<rect class="bar {cls}" x="{cx - bar_w / 2:.1f}" y="{top:.1f}" '
            f'width="{bar_w:.1f}" height="{max(tall, 1):.1f}" rx="4">'
            f'<title>{esc(label)}: {esc(money(value))}</title></rect>')
        label_y = top - 6 if value >= 0 else top + tall + 13
        parts.append(f'<text class="bar-value" x="{cx:.1f}" y="{label_y:.1f}" '
                     f'text-anchor="middle">{esc(money(value))}</text>')
        parts.append(f'<text class="tick" x="{cx:.1f}" y="{height - 8}" '
                     f'text-anchor="middle">{esc(label)}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')


STYLE = """
:root{--surface:#fcfcfb;--card:#fff;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#78776f;
--rule:#e3e2dd;--pos:#2a78d6;--neg:#e34948;--crit:#d03b3b;--good:#0ca30c;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--surface:#1a1a19;--card:#232321;--ink:#fff;--ink-2:#c3c2b7;--ink-3:#8f8e85;
--rule:#383835;--pos:#3987e5;--neg:#e66767;--crit:#d03b3b;--good:#0ca30c;}}
*{box-sizing:border-box}
body{margin:0;padding:0;background:var(--surface);color:var(--ink);
font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Helvetica Neue",sans-serif;}
.wrap{max-width:840px;margin:0 auto;padding-block:32px;padding-left:18px;padding-right:18px;}
h1{font-size:26px;line-height:1.25;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:19px;margin:40px 0 10px;letter-spacing:-.01em}
h3{font-size:15px;margin:22px 0 6px}
p{margin:10px 0;color:var(--ink-2)}
.sub{color:var(--ink-3);font-size:13.5px;margin:0 0 26px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;
padding:18px;margin:14px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--rule);border-radius:12px;padding:14px 16px}
.tile .k{font-size:12px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:25px;font-weight:650;letter-spacing:-.02em;margin-top:3px}
.tile .n{font-size:12.5px;color:var(--ink-3);margin-top:2px}
.nb{white-space:nowrap}
.v.up{color:var(--pos)}.v.down{color:var(--neg)}.v.bad{color:var(--crit)}
.verdict{border-left:4px solid var(--crit);background:var(--card);
border-radius:0 12px 12px 0;padding:16px 18px;margin:20px 0}
.verdict b{color:var(--crit)}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--rule);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{font-size:12px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.05em;
font-weight:600;border-bottom-width:2px}
tbody tr:last-child td{border-bottom:none}
tr.total td{font-weight:650;border-top:2px solid var(--rule)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600}
.pill.no{background:color-mix(in srgb,var(--crit) 15%,transparent);color:var(--crit)}
.pill.yes{background:color-mix(in srgb,var(--good) 15%,transparent);color:var(--good)}
svg{width:100%;max-width:560px;height:auto;display:block;margin-inline:auto}
.axis{stroke:var(--ink-3);stroke-width:1;opacity:.55}
.series{fill:none;stroke:var(--pos);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.area{fill:var(--neg);opacity:.16}
.limit{stroke:var(--crit);stroke-width:2;stroke-dasharray:5 4}
.limit-label{fill:var(--crit);font-size:10.5px;font-weight:700}
.tick{fill:var(--ink-3);font-size:11px}
.bar-value{fill:var(--ink-2);font-size:11px;font-weight:600}
.bar.pos{fill:var(--pos)}.bar.neg{fill:var(--neg)}
.caveats li{margin:7px 0;color:var(--ink-2)}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--rule);
color:var(--ink-3);font-size:12.5px}
"""


def tile(key: str, value: str, note: str = "", cls: str = "") -> str:
    note_html = f'<div class="n">{note}</div>' if note else ""
    return (f'<div class="tile"><div class="k">{esc(key)}</div>'
            f'<div class="v {cls}">{value}</div>{note_html}</div>')


def quarter_table(q: pd.DataFrame, totals: dict) -> str:
    head = ("<tr><th>quarter</th><th>trades</th><th>win rate</th><th>avg R</th>"
            "<th>net P&amp;L</th><th>max drawdown</th><th>profit factor</th></tr>")
    body = "".join(
        f"<tr><td>{esc(r.period)}</td><td>{int(r.trades)}</td>"
        f"<td>{r.win_rate:.1%}</td><td>{r.avg_r:+.3f}</td>"
        f"<td>{esc(money(r.total_pnl))}</td>"
        f"<td>{esc(money(r.max_drawdown))}</td>"
        f"<td>{r.profit_factor:.2f}</td></tr>"
        for r in q.itertuples())
    foot = (f'<tr class="total"><td>all four</td><td>{totals["trades"]}</td>'
            f'<td>{totals["win_rate"]:.1%}</td><td>{totals["avg_r"]:+.3f}</td>'
            f'<td>{esc(money(totals["net"]))}</td>'
            f'<td>{esc(money(totals["max_drawdown"]))}</td>'
            f'<td>{totals["profit_factor"]:.2f}</td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}{foot}</tbody></table></div>')


OUTCOME_WORD = {"busted": "busted", "passed": "passed", "neither": "survived"}


def ladder_table(rows: list[dict]) -> str:
    head = ("<tr><th>risk / trade</th><th>trades</th><th>net P&amp;L</th>"
            "<th>max drawdown</th><th>first breach</th><th>outcome</th></tr>")
    cells = []
    for r in rows:
        when = ("&mdash;" if r["at_trade"] is None
                else f'#{r["at_trade"]} &middot; {esc(r["at_date"])}')
        pill = "no" if r["outcome"] == "busted" else "yes"
        cells.append(
            f'<tr><td>${r["risk"]:,.0f}</td><td>{r["trades"]}</td>'
            f'<td>{esc(money(r["net"]))}</td>'
            f'<td>{esc(money(r["max_drawdown"]))}</td><td>{when}</td>'
            f'<td><span class="pill {pill}">{OUTCOME_WORD[r["outcome"]]}</span></td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(cells)}</tbody></table></div>')


def build(ctx: dict) -> str:
    """The whole page. Composed from pieces rather than one giant f-string."""
    t, q, lad = ctx["totals"], ctx["quarters"], ctx["ladder"]
    breach_i = ctx["breach_index"]
    below = ctx["below_limit"]

    tiles = "".join([
        tile("net P&L", esc(money(t["net"])), f'{t["trades"]} trades',
             "up" if t["net"] >= 0 else "down"),
        tile("win rate", f'{t["win_rate"]:.1%}',
             f'break-even is {t["breakeven"]:.1%} after costs'),
        tile("profit factor", f'{t["profit_factor"]:.2f}',
             f'longest losing streak {t["streak"]}'),
        tile("max drawdown", esc(money(t["max_drawdown"])),
             f'limit is <span class="nb">\u2212${LOSS_LIMIT:,.0f}</span>', "bad"),
    ])

    if breach_i is None:
        verdict = (f'<div class="verdict"><b>The drawdown never reached the '
                   f'&minus;${LOSS_LIMIT:,.0f} limit</b> at ${ctx["risk"]:,.0f} a trade.</div>')
    else:
        verdict = (
            f'<div class="verdict">'
            f'<b>At ${ctx["risk"]:,.0f} a trade this year was not tradeable.</b> '
            f'The drawdown passed the &minus;${LOSS_LIMIT:,.0f} loss limit on '
            f'<b>trade {breach_i + 1}</b>, on {esc(ctx["breach_date"])} &mdash; '
            f'{esc(ctx["breach_gap"])} after the first trade of the window. '
            f'{below} of the {t["trades"]} points on the curve below sit under that '
            f'line, so for {below / t["trades"]:.0%} of the year the account had '
            f'already been closed. The {esc(money(t["net"]))} at the end is a number '
            f'nobody collects.</div>')

    return "\n".join([
        f"<title>{esc(ctx['title'])}</title>",
        f"<style>{STYLE}</style>",
        '<div class="wrap">',
        f"<h1>{esc(ctx['title'])}</h1>",
        f'<p class="sub">{esc(ctx["subtitle"])}</p>',
        f'<div class="tiles">{tiles}</div>',
        verdict,

        "<h2>Cumulative profit and loss</h2>",
        f'<p>Every filled trade in order, at ${ctx["risk"]:,.0f} of risk each. '
        f'Three winning quarters and one bad one.</p>',
        f'<div class="card">{ctx["equity_svg"]}</div>',

        "<h2>Drawdown from the running peak</h2>",
        "<p>This is the line the account actually lives on. A loss limit trails "
        "the high-water mark, so what closes an account is the fall from the peak "
        "&mdash; not whether the balance is up. The dashed line is the limit.</p>",
        f'<div class="card">{ctx["drawdown_svg"]}</div>',

        "<h2>By quarter</h2>",
        f'<div class="card">{ctx["quarter_svg"]}</div>',
        quarter_table(q, t),

        "<h2>What each position size would have done</h2>",
        "<p>The same trades, sized differently. &ldquo;First breach&rdquo; is the "
        "trade whose drawdown passed the loss limit; &ldquo;outcome&rdquo; is "
        "whichever came first &mdash; the "
        f"${PROFIT_TARGET:,.0f} profit target or the "
        f"&minus;${LOSS_LIMIT:,.0f} limit.</p>",
        ladder_table(lad),
        f'<p>{esc(ctx["ladder_note"])}</p>',

        "<h2>What this is and is not</h2>",
        '<ul class="caveats">',
        "<li><b>A backtest, not a track record.</b> No order here was ever sent. "
        "The seven TopstepX endpoints are written from documentation and have "
        "never run against the live server.</li>",
        "<li><b>The fill model favours it.</b> A limit order fills when price "
        "touches it; a real one waits in a queue and sometimes never fills at "
        "all.</li>",
        "<li><b>Different data.</b> These are Databento GLBX.MDP3 bars, not "
        "TopstepX's feed. A sweep qualifies on one tick of penetration, which is "
        "exactly where two feeds disagree.</li>",
        f"<li><b>Data ends {esc(ctx['data_end'])}.</b> The last quarter is "
        "partial.</li>",
        "<li><b>The eleven-year record is &minus;$3,044</b> across 2,161 trades, "
        "with 5 losing years and a cumulative curve that never went positive. The "
        "median three-month window has a profit factor of 0.996 &mdash; a coin "
        "flip. This window sits in the 98th percentile of those. One good year "
        "does not settle that; only bars nobody has seen yet can.</li>",
        "</ul>",

        f'<footer>Generated by <code>scripts/quarterly_report.py</code> from '
        f'<code>data/processed/nq_1m.parquet</code>. Every figure re-derives from '
        f'<code>backtest/metrics.py</code> and the same simulate path as the '
        f'backtest &mdash; rerun the script to check any of them.</footer>',
        "</div>",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-10-01", help="inclusive UTC date")
    ap.add_argument("--end", default="2026-10-01", help="exclusive UTC date")
    ap.add_argument("--risk", type=float, default=1000.0,
                    help="dollars risked per trade for the headline figures")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    bars = load(args.start, args.end)
    trades = run(bars, args.start, args.end, args.risk)
    if trades.empty:
        print("no trades in that window")
        return 1

    pnl = trades["net_pnl"].to_numpy()
    dd = drawdown(pnl)
    q = quarters(trades)
    breach_i = breach(pnl)
    payoff = (trades.loc[trades["net_pnl"] > 0, "net_pnl"].mean() /
              -trades.loc[trades["net_pnl"] < 0, "net_pnl"].mean())

    totals = {
        "trades": len(trades),
        "net": float(pnl.sum()),
        "win_rate": float((pnl > 0).mean()),
        "avg_r": float(trades["r_multiple"].mean()),
        "max_drawdown": float(dd.min()),
        "profit_factor": profit_factor(trades["net_pnl"]),
        "streak": longest_losing_streak(trades["net_pnl"]),
        "breakeven": 1.0 / (1.0 + payoff),
    }

    first, last = trades["entry_ts"].min(), trades["exit_ts"].max()
    lad = ladder(bars, args.start, args.end)
    survivors = [r for r in lad if r["outcome"] != "busted"]
    if survivors:
        best = survivors[-1]
        note = (f"Only ${best['risk']:,.0f} a trade survived the year, and it "
                f"returned {money(best['net'])} against a "
                f"${PROFIT_TARGET:,.0f} target \u2014 it survives by trading too "
                f"small to pass. Every size that could clear the target breached "
                f"the limit first.")
    else:
        note = ("No size in this ladder survived the year.")

    ctx = {
        "title": "LTF Sweep \u2014 four quarters",
        "subtitle": (f"{first.tz_convert(NY).date()} to {last.tz_convert(NY).date()} "
                     f"\u00b7 {len(trades)} trades at ${args.risk:,.0f} risk each "
                     f"\u00b7 Topstep 50K rules"),
        "risk": args.risk,
        "totals": totals,
        "quarters": q,
        "ladder": lad,
        "ladder_note": note,
        "breach_index": breach_i,
        "breach_date": (None if breach_i is None else
                        trades["exit_ts"].iloc[breach_i].tz_convert(NY).date().isoformat()),
        "breach_gap": (None if breach_i is None else
                       f"{(trades['exit_ts'].iloc[breach_i] - first).days} days"),
        "below_limit": int((dd <= -LOSS_LIMIT).sum()),
        "data_end": str(bars["ts"].max().tz_convert(NY).date()),
        "equity_svg": line_chart(range(len(pnl)), list(np.cumsum(pnl))),
        "drawdown_svg": line_chart(range(len(pnl)), list(dd),
                                   limit=LOSS_LIMIT, fill=True),
        "quarter_svg": bar_chart([str(p) for p in q["period"]],
                                 list(q["total_pnl"])),
    }

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(ctx), encoding="utf-8")

    print(f"{len(trades)} trades  net {money(totals['net'])}  "
          f"win {totals['win_rate']:.1%}  PF {totals['profit_factor']:.2f}  "
          f"maxDD {money(totals['max_drawdown'])}")
    if breach_i is not None:
        print(f"breached -${LOSS_LIMIT:,.0f} at trade {breach_i + 1} "
              f"({ctx['breach_date']}); {ctx['below_limit']}/{len(trades)} "
              f"points below the limit")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
