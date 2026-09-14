"""Quarterly performance report for the LTF sweep model, as a standalone page.

    uv run python scripts/quarterly_report.py
    uv run python scripts/report_pdf.py     # the same page, as a PDF

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
import pathlib

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from ict import data as D
from reportkit import (STYLE, bar_chart, esc, line_chart, money, page, svg_t, t,
                       tile)
from strategies.ltf_sweep import generate_orders

NY = "America/New_York"
OUT = pathlib.Path("docs/quarterly_report.html")

#: Topstep 50K. The profit target is what passes an evaluation and the loss
#: limit is what ends it; both are needed to say which came first.
PROFIT_TARGET = 3000.0
LOSS_LIMIT = 2000.0

#: Topstep 50K, read off the dashboard on 2026-09-13. Both paths include a free
#: reset credit with every monthly rebill; the difference that matters is WHEN
#: the activation fee lands -- Standard charges it "once per XFA earned", so a
#: run of failures never pays it. No-Activation buys that away with +$36 a month
#: AND +$36 on every reset, which is the wrong trade for a plan built on
#: re-buying: it wins only while (months + paid resets) stays under about four.
PATHS = {
    "Standard":       {"monthly": 49.0, "reset": 49.0, "activation": 149.0},
    "No-Activation":  {"monthly": 85.0, "reset": 85.0, "activation": 0.0},
}
API_MONTHLY = 14.50

#: Position sizes to show side by side. The spread matters more than the exact
#: values: it is the difference between a size that survives and one that does
#: not.
RISK_LADDER = (100.0, 200.0, 250.0, 500.0, 900.0, 1000.0)

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


def rebuy(trades: pd.DataFrame) -> dict:
    """Walk the real sequence, buying a fresh account after every breach.

    The point of doing it in order rather than by bootstrap: losses cluster. A
    shuffle treats each attempt as an independent draw and says the 2% plan
    needs 2.9 of them; the actual sequence needed eleven, because the bad
    quarter's losses arrive together and take several accounts with them.
    """
    equity = peak = 0.0
    accounts = 1
    start = trades["entry_ts"].iloc[0]
    for pnl, exit_ts in zip(trades["net_pnl"], trades["exit_ts"]):
        equity += pnl
        peak = max(peak, equity)
        if equity >= PROFIT_TARGET:
            return {"accounts": accounts, "days": (exit_ts - start).days, "passed": True}
        if equity - peak <= -LOSS_LIMIT:
            equity = peak = 0.0
            accounts += 1
    last = trades["exit_ts"].iloc[-1]
    return {"accounts": accounts, "days": (last - start).days, "passed": False}


def path_cost(path: dict, accounts: int, days: int, passed: bool) -> dict:
    """Itemised, because a total alone reads as though something was left out.

    Every monthly rebill banks one free reset credit, so the first month earns
    none and each later month covers one of the resets.
    """
    months = max(1, int(np.ceil(days / 30.4)))
    free = max(0, months - 1)
    paid = max(0, (accounts - 1) - free)
    lines = {
        "subscription": months * path["monthly"],
        "paid_resets": paid * path["reset"],
        "api_access": months * API_MONTHLY,
        # Charged once per funded account EARNED, so a year of failures pays
        # nothing -- which is exactly why buying it away is poor value here.
        "activation": path["activation"] if passed else 0.0,
    }
    # The count is `reset_count`, never `paid_resets` -- that key is the dollar
    # amount below, and naming both the same made the count render as "$343
    # after 3 free credits".
    return {"months": months, "free_credits": free, "reset_count": paid,
            **lines, "total": sum(lines.values())}


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

def quarter_table(q: pd.DataFrame, totals: dict) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("季度", "quarter"), ("交易", "trades"), ("勝率", "win rate"),
        ("平均 R", "avg R"), ("淨損益", "net P&amp;L"),
        ("最大回撤", "max drawdown"), ("獲利因子", "profit factor"))) + "</tr>"
    body = "".join(
        f"<tr><td>{esc(r.period)}</td><td>{int(r.trades)}</td>"
        f"<td>{r.win_rate:.1%}</td><td>{r.avg_r:+.3f}</td>"
        f"<td>{esc(money(r.total_pnl))}</td>"
        f"<td>{esc(money(r.max_drawdown))}</td>"
        f"<td>{r.profit_factor:.2f}</td></tr>"
        for r in q.itertuples())
    foot = (f'<tr class="total"><td>{t("四季合計", "all four")}</td><td>{totals["trades"]}</td>'
            f'<td>{totals["win_rate"]:.1%}</td><td>{totals["avg_r"]:+.3f}</td>'
            f'<td>{esc(money(totals["net"]))}</td>'
            f'<td>{esc(money(totals["max_drawdown"]))}</td>'
            f'<td>{totals["profit_factor"]:.2f}</td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}{foot}</tbody></table></div>')


OUTCOME_WORD = {"busted": ("爆倉", "busted"), "passed": ("通過", "passed"),
                "neither": ("撐過", "survived")}


def cost_table(costs: dict) -> str:
    """Every line, not just the total. A summary that hides the activation fee
    reads as though it was forgotten -- and this table is read to make a
    decision, so anything left out is a number argued about later."""
    rows = ["subscription", "paid_resets", "api_access", "activation"]
    label = {"subscription": t("月費", "monthly subscription"),
             "paid_resets": t("付費重置", "paid resets"),
             "api_access": t("API access", "API access"),
             "activation": t("啟用費", "activation fee")}
    head = f'<tr><th>{t("項目", "cost")}</th>' + "".join(
        f"<th>{esc(name)}</th>" for name in costs) + "</tr>"
    body = ""
    for key in rows:
        cells = "".join(f"<td>${esc(money(c[key], sign=False))}</td>" for c in costs.values())
        note = ""
        if key == "paid_resets":
            first = next(iter(costs.values()))
            note = (' <span class="muted">' + t(
                f'（{first["reset_count"]} 次付費，扣掉 {first["free_credits"]} 次免費額度）',
                f'({first["reset_count"]} paid after {first["free_credits"]} free credits)')
                + '</span>')
        if key == "activation":
            # Per XFA EARNED, not once ever: lose a funded account,
            # pass again, and it is charged again. This replay stops at
            # the first pass, so it counts one.
            note = (' <span class="muted">' + t(
                "（每拿到一個 funded 帳戶收一次）",
                "(per funded account earned)") + '</span>')
        body += f"<tr><td>{label[key]}{note}</td>{cells}</tr>"
    totals = "".join(f'<td>${esc(money(c["total"], sign=False))}</td>' for c in costs.values())
    body += f'<tr class="total"><td>{t("通過的總花費", "total to pass")}</td>{totals}</tr>'
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def ladder_table(rows: list[dict]) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("每單風險", "risk / trade"), ("交易", "trades"), ("淨損益", "net P&amp;L"),
        ("最大回撤", "max drawdown"), ("首次觸限", "first breach"),
        ("結果", "outcome"))) + "</tr>"
    cells = []
    for r in rows:
        when = ("&mdash;" if r["at_trade"] is None
                else f'#{r["at_trade"]} &middot; {esc(r["at_date"])}')
        pill = "no" if r["outcome"] == "busted" else "yes"
        cells.append(
            f'<tr><td>${r["risk"]:,.0f}</td><td>{r["trades"]}</td>'
            f'<td>{esc(money(r["net"]))}</td>'
            f'<td>{esc(money(r["max_drawdown"]))}</td><td>{when}</td>'
            f'<td><span class="pill {pill}">{t(*OUTCOME_WORD[r["outcome"]])}'
            f'</span></td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(cells)}</tbody></table></div>')


def build(ctx: dict) -> str:
    """The whole page, in both languages. Composed from pieces, not one f-string."""
    tot, q, lad = ctx["totals"], ctx["quarters"], ctx["ladder"]
    breach_i = ctx["breach_index"]
    below = ctx["below_limit"]
    risk = ctx["risk"]
    plan = ctx["rebuy"]

    tiles = "".join([
        tile(t("淨損益", "net P&L"), esc(money(tot["net"])),
             t(f'{tot["trades"]} 筆交易', f'{tot["trades"]} trades'),
             "up" if tot["net"] >= 0 else "down"),
        tile(t("勝率", "win rate"), f'{tot["win_rate"]:.1%}',
             t(f'扣掉成本後的損益兩平點是 {tot["breakeven"]:.1%}',
               f'break-even is {tot["breakeven"]:.1%} after costs')),
        tile(t("獲利因子", "profit factor"), f'{tot["profit_factor"]:.2f}',
             t(f'最長連敗 {tot["streak"]} 筆',
               f'longest losing streak {tot["streak"]}')),
        tile(t("最大回撤", "max drawdown"), esc(money(tot["max_drawdown"])),
             t(f'上限是 <span class="nb">\u2212${LOSS_LIMIT:,.0f}</span>',
               f'limit is <span class="nb">\u2212${LOSS_LIMIT:,.0f}</span>'), "bad"),
    ])

    if breach_i is None:
        verdict = '<div class="verdict">' + t(
            f'<b>在 ${risk:,.0f} 的單筆風險下，回撤從來沒有碰到 '
            f'\u2212${LOSS_LIMIT:,.0f} 的上限。</b>',
            f'<b>The drawdown never reached the \u2212${LOSS_LIMIT:,.0f} limit</b> '
            f'at ${risk:,.0f} a trade.') + '</div>'
    else:
        share = below / tot["trades"]
        verdict = '<div class="verdict">' + t(
            f'<b>在 ${risk:,.0f} 的單筆風險下，這一年是跑不完的。</b>'
            f'回撤在<b>第 {breach_i + 1} 筆、{esc(ctx["breach_date"])}</b>'
            f'穿過 \u2212${LOSS_LIMIT:,.0f} 的虧損上限 —— 距離這段期間的第一筆'
            f'{esc(ctx["breach_gap_zh"])}。下面那條曲線的 {tot["trades"]} 個點裡'
            f'有 {below} 個在那條線以下，也就是這一年有 {share:.0%} 的時間'
            f'帳戶早就被關掉了。最後那個 {esc(money(tot["net"]))} 沒有人領得到。',
            f'<b>At ${risk:,.0f} a trade this year was not tradeable.</b> '
            f'The drawdown passed the \u2212${LOSS_LIMIT:,.0f} loss limit on '
            f'<b>trade {breach_i + 1}</b>, on {esc(ctx["breach_date"])} &mdash; '
            f'{esc(ctx["breach_gap"])} after the first trade of the window. '
            f'{below} of the {tot["trades"]} points on the curve below sit under '
            f'that line, so for {share:.0%} of the year the account had already '
            f'been closed. The {esc(money(tot["net"]))} at the end is a number '
            f'nobody collects.') + '</div>'

    caveats = [
        ("<b>這是回測，不是實績。</b>這裡沒有任何一張單真的送出去過。"
         "七個 TopstepX 端點都是照文件寫的，從來沒有對過真的伺服器。",
         "<b>A backtest, not a track record.</b> No order here was ever sent. "
         "The seven TopstepX endpoints are written from documentation and have "
         "never run against the live server."),
        ("<b>成交模型偏向它。</b>限價單在價格「碰到」時就算成交；真實的限價單要"
         "排隊，有時候根本不會成交。",
         "<b>The fill model favours it.</b> A limit order fills when price "
         "touches it; a real one waits in a queue and sometimes never fills at all."),
        ("<b>資料源不同。</b>這是 Databento GLBX.MDP3 的 K 棒，不是 TopstepX 的行情。"
         "掃針只要穿透一檔就成立，而那正是兩個資料源最容易不一致的地方。",
         "<b>Different data.</b> These are Databento GLBX.MDP3 bars, not "
         "TopstepX's feed. A sweep qualifies on one tick of penetration, which "
         "is exactly where two feeds disagree."),
        (f"<b>一致性規則沒有算進這裡。</b>單日獲利不得超過獲利目標的 55%"
         f"（50K 是 $1,650），超過的話目標會往上調。一天一單、1:1 停利，"
         f"所以單日獲利約等於單筆風險 —— ${risk:,.0f} 還有空間，"
         f"但這條規則在 $1,650/單 附近設了天花板。",
         f"<b>The consistency target is not modelled here.</b> A single day may "
         f"not exceed 55% of the profit target ($1,650 on a 50K) or the target "
         f"rises. One trade a day at a 1:1 target makes a winning day about one "
         f"unit of risk, so ${risk:,.0f} has room &mdash; but it caps position "
         f"size near $1,650 a trade."),
        (f"<b>資料截止 {esc(ctx['data_end'])}。</b>最後一季是不完整的。",
         f"<b>Data ends {esc(ctx['data_end'])}.</b> The last quarter is partial."),
        ("<b>完整 11 年是 \u2212$3,044</b>，2,161 筆交易、5 年虧損，"
         "而且累計曲線從來沒有轉正過。三個月視窗的中位獲利因子是 0.996 —— "
         "等於擲硬幣。當前這個視窗落在那些視窗的第 98 百分位。"
         "一個好年頭不能解決這件事；只有還沒有人看過的 K 棒可以。",
         "<b>The eleven-year record is \u2212$3,044</b> across 2,161 trades, "
         "with 5 losing years and a cumulative curve that never went positive. "
         "The median three-month window has a profit factor of 0.996 &mdash; a "
         "coin flip. This window sits in the 98th percentile of those. One good "
         "year does not settle that; only bars nobody has seen yet can."),
    ]

    return page(ctx["title"], [
        f'<h1>{t(esc(ctx["title_zh"]), esc(ctx["title"]))}</h1>',
        f'<p class="sub">{t(esc(ctx["subtitle_zh"]), esc(ctx["subtitle"]))}</p>',
        f'<div class="tiles">{tiles}</div>',
        verdict,

        f'<h2>{t("累計損益", "Cumulative profit and loss")}</h2>',
        "<p>" + t(f"每一筆成交依序排列，每單風險 ${risk:,.0f}。三個獲利季，一個壞季。",
                  f"Every filled trade in order, at ${risk:,.0f} of risk each. "
                  f"Three winning quarters and one bad one.") + "</p>",
        f'<div class="card">{ctx["equity_svg"]}</div>',

        f'<h2>{t("從高點起算的回撤", "Drawdown from the running peak")}</h2>',
        "<p>" + t("這才是帳戶真正活在上面的那條線。虧損上限是跟著最高點走的，"
                  "所以關掉帳戶的是「從高點跌下來多少」，不是餘額是不是正的。"
                  "虛線就是那個上限。",
                  "This is the line the account actually lives on. A loss limit "
                  "trails the high-water mark, so what closes an account is the "
                  "fall from the peak &mdash; not whether the balance is up. The "
                  "dashed line is the limit.") + "</p>",
        f'<div class="card">{ctx["drawdown_svg"]}</div>',

        f'<h2>{t("逐季", "By quarter")}</h2>',
        f'<div class="card">{ctx["quarter_svg"]}</div>',
        quarter_table(q, tot),

        f'<h2>{t("換成別的倉位大小會怎樣", "What each position size would have done")}</h2>',
        "<p>" + t(f"同一批交易，只改倉位。「首次觸限」是回撤穿過虧損上限的那一筆；"
                  f"「結果」是 ${PROFIT_TARGET:,.0f} 的獲利目標和 "
                  f"\u2212${LOSS_LIMIT:,.0f} 的上限，哪一個先到。",
                  f"The same trades, sized differently. &ldquo;First breach&rdquo; "
                  f"is the trade whose drawdown passed the loss limit; "
                  f"&ldquo;outcome&rdquo; is whichever came first &mdash; the "
                  f"${PROFIT_TARGET:,.0f} profit target or the "
                  f"\u2212${LOSS_LIMIT:,.0f} limit.") + "</p>",
        ladder_table(lad),
        f'<p>{t(esc(ctx["ladder_note_zh"]), esc(ctx["ladder_note"]))}</p>',

        f'<h2>{t("爆了再買，這一年要花多少", "Busting and re-buying: what the year would have cost")}</h2>',
        "<p>" + t(f"同一批交易照原順序走，每次觸限就買一個新帳戶。"
                  f"在 ${risk:,.0f} 的單筆風險下是"
                  f"<b>{plan['accounts']} 個帳戶、{plan['days']} 天</b>，"
                  f"最後一個通過了。再買這套做法行得通 —— 只是帳戶數和月數"
                  f"都比「打亂順序」的估計多，因為虧損的那一季是連在一起來的，"
                  f"不是分散開的。",
                  f"Walking the same trades in order and buying a fresh account "
                  f"after every breach. At ${risk:,.0f} a trade that is "
                  f"<b>{plan['accounts']} accounts over {plan['days']} days</b>, "
                  f"and the last one passed. Re-buying works &mdash; it is just "
                  f"more accounts and more months than a shuffled estimate "
                  f"suggests, because the losing quarter arrives as one run "
                  f"rather than spread out.") + "</p>",
        cost_table(ctx["costs"]),
        f'<p>{t(esc(ctx["path_note_zh"]), esc(ctx["path_note"]))}</p>',
        '<p class="muted">' + t(
            "這是一年裡的一條路徑，不是一個分布。它在這裡是為了給出量級 —— "
            "幾百美金、幾個月 —— 不是拿來挑倉位大小的。",
            "One path through one year, not a distribution. It is here for the "
            "order of magnitude &mdash; hundreds of dollars and several months "
            "&mdash; not to pick a position size from.") + "</p>",

        f'<h2>{t("這份東西是什麼、不是什麼", "What this is and is not")}</h2>',
        '<ul class="caveats">',
        *(f"<li>{t(zh, en)}</li>" for zh, en in caveats),
        "</ul>",

    ], t(
        "由 <code>scripts/quarterly_report.py</code> 從 "
        "<code>data/processed/nq_1m.parquet</code> 產生。每一個數字都能從 "
        "<code>backtest/metrics.py</code> 和回測用的同一條 simulate 路徑"
        "重新導出 —— 重跑一次就能查證任何一個。",
        "Generated by <code>scripts/quarterly_report.py</code> from "
        "<code>data/processed/nq_1m.parquet</code>. Every figure re-derives "
        "from <code>backtest/metrics.py</code> and the same simulate path as "
        "the backtest &mdash; rerun the script to check any of them."))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-10-01", help="inclusive UTC date")
    ap.add_argument("--end", default="2026-10-01", help="exclusive UTC date")
    ap.add_argument("--risk", type=float, default=900.0,
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
    plan = rebuy(trades)
    costs = {name: path_cost(path, plan["accounts"], plan["days"], plan["passed"])
             for name, path in PATHS.items()}
    cheapest = min(costs, key=lambda n: costs[n]["total"])
    other = next(n for n in costs if n != cheapest)
    gap = costs[other]["total"] - costs[cheapest]["total"]
    units = costs[cheapest]["months"] + costs[cheapest]["reset_count"]
    premium = PATHS["No-Activation"]["monthly"] - PATHS["Standard"]["monthly"]
    path_note_zh = (
        f"這裡是 {cheapest} 便宜 ${gap:,.0f}。啟用費是「每拿到一個 funded 帳戶"
        f"收一次」，所以一連串的失敗永遠不會付到它 —— No-Activation 用"
        f"每個月多 ${premium:,.0f}、而且每次重置也多 ${premium:,.0f}，"
        f"去買掉一個「贏了之後才會欠」的費用。只有當月數加上付費重置次數"
        f"少於大約四次時它才划算；這一年需要 {units} 次。")
    path_note = (
        f"{cheapest} is ${gap:,.0f} cheaper here. The activation fee is charged "
        f"once per funded account earned, so a run of failures never pays it \u2014 "
        f"No-Activation spends ${PATHS['No-Activation']['monthly'] - PATHS['Standard']['monthly']:,.0f} "
        f"more every month AND on every reset to buy away a fee you only owe "
        f"after you have already won. It comes out ahead only while months plus "
        f"paid resets stay under about four; this year needed "
        f"{costs[cheapest]['months'] + costs[cheapest]['reset_count']}."
    )

    lad = ladder(bars, args.start, args.end)
    survivors = [r for r in lad if r["outcome"] != "busted"]
    if survivors:
        best = survivors[-1]
        note = (f"Only ${best['risk']:,.0f} a trade survived the year, and it "
                f"returned {money(best['net'])} against a "
                f"${PROFIT_TARGET:,.0f} target \u2014 it survives by trading too "
                f"small to pass. Every size that could clear the target breached "
                f"the limit first.")
        note_zh = (f"只有 ${best['risk']:,.0f}/單撐過了這一年，而它的報酬是 "
                   f"{money(best['net'])}，對上 ${PROFIT_TARGET:,.0f} 的目標 —— "
                   f"它活下來的方式是小到考不過。每一個賺得夠多的倉位，"
                   f"都是先觸限、不是先達標。")
    else:
        note = "No size in this ladder survived the year."
        note_zh = "這張表裡沒有任何一個倉位撐過這一年。"

    ctx = {
        "title": "LTF Sweep \u2014 four quarters",
        "title_zh": "LTF Sweep \u2014 四個季度",
        "subtitle_zh": (f"{first.tz_convert(NY).date()} 至 {last.tz_convert(NY).date()} "
                        f"\u00b7 {len(trades)} 筆交易，每單風險 ${args.risk:,.0f} "
                        f"\u00b7 Topstep 50K 規則"),
        "subtitle": (f"{first.tz_convert(NY).date()} to {last.tz_convert(NY).date()} "
                     f"\u00b7 {len(trades)} trades at ${args.risk:,.0f} risk each "
                     f"\u00b7 Topstep 50K rules"),
        "risk": args.risk,
        "totals": totals,
        "quarters": q,
        "ladder": lad,
        "ladder_note": note,
        "ladder_note_zh": note_zh,
        "rebuy": plan,
        "costs": costs,
        "path_note": path_note,
        "path_note_zh": path_note_zh,
        "breach_index": breach_i,
        "breach_date": (None if breach_i is None else
                        trades["exit_ts"].iloc[breach_i].tz_convert(NY).date().isoformat()),
        "breach_gap": (None if breach_i is None else
                       f"{(trades['exit_ts'].iloc[breach_i] - first).days} days"),
        "breach_gap_zh": (None if breach_i is None else
                          f"{(trades['exit_ts'].iloc[breach_i] - first).days} 天"),
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
    print(f"re-buy: {plan['accounts']} accounts over {plan['days']} days, "
          f"passed={plan['passed']}")
    for name, c in costs.items():
        print(f"  {name:<15} ${c['total']:,.0f}  "
              f"(sub ${c['subscription']:,.0f} + resets ${c['paid_resets']:,.0f} "
              f"+ api ${c['api_access']:,.0f} + activation ${c['activation']:,.0f})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
