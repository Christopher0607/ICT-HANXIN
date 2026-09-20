"""Equity curve for the failed-breakout fade, with the selection line drawn on.

    uv run python scripts/fade_report.py

Writes ``docs/fade_report.html``. The rule came out of a search over 900
combinations in ``scripts/search_orb.py``, which means the interesting question
is not what the curve does but whether its two halves look alike: everything
before 2024-01-01 is data the search could see when it picked, and everything
after is not. Every equity chart here carries that line.

Two configurations are plotted, not one. The cell the search ranked first is
the luckiest of 900 draws by construction, so it is shown beside an ordinary
member of the same family that nothing selected. If they disagree, the ranking
was the finding rather than the rule.
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd

from backtest import metrics
from backtest.engine import BacktestConfig, simulate
from backtest.recycle import Policy, recycle
from ict import data as D
from reportkit import bar_chart, esc, line_chart, money, page, t, tile
from strategies.orb_family import DetectConfig, PriceConfig, detect, price

OUT = pathlib.Path("docs/fade_report.html")
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
HISTORY_START = "2016-01-01"

#: $500 a trade is what every other strategy in this project is scored at.
RISK = 500.0
EXEC = BacktestConfig(risk_per_trade_usd=RISK, entry_side="stop",
                      entry_slippage_ticks=1.0)

#: The cell the search ranked first, and an ordinary member of the same family
#: that nothing selected. The only difference is the opening-body filter.
VARIANTS = {
    "selected": dict(min_body_frac=0.4),
    "unselected": dict(min_body_frac=0.0),
}
BASE = dict(entry_kind="fail_fade", side_rule="either", stop_kind="height",
            stop_value=1.0, exit_minute=10 * 60 + 45, target_r=1.0)

#: Straight from scripts/search_orb.py, which prints them on every run.
PAYOFF_TABLE = ((0.25, 0.788, 0.77, -0.027), (0.40, 0.711, 0.86, -0.018),
                (0.50, 0.666, 0.89, -0.014), (0.75, 0.582, 0.95, 0.004),
                (1.00, 0.517, 1.00, 0.023))
FAMILY_TABLE = (("break_first", 300, 0.52, 0.54, 0.0),
                ("break_retest", 300, 0.67, 0.78, 0.0),
                ("fail_fade", 300, 0.96, 0.89, 0.14))


def drawdown(pnl: np.ndarray) -> np.ndarray:
    """Distance below the running peak, from an opening balance of zero."""
    equity = np.cumsum(pnl)
    return equity - np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]


def run(setups, bars, risk: float = RISK, **kw) -> pd.DataFrame:
    """Filled trades from HISTORY_START onward.

    Bars are loaded from December 2015 so the first sessions of 2016 have their
    lead-in, and those December sessions produce setups of their own. Left in,
    they put a stray twelfth bar on a chart captioned as eleven years.
    """
    orders = price(setups, bars, PriceConfig(**{**BASE, **kw}))
    cfg = EXEC if risk == RISK else BacktestConfig(
        risk_per_trade_usd=risk, entry_side="stop", entry_slippage_ticks=1.0)
    trades = simulate(orders, bars, cfg)
    filled = trades[trades["filled"]].sort_values("exit_ts")
    start = pd.Timestamp(HISTORY_START, tz="UTC")
    return filled[pd.DatetimeIndex(filled["exit_ts"]) >= start].reset_index(drop=True)


def split_index(trades: pd.DataFrame) -> int:
    """Where the out-of-sample period starts, as a position on the curve."""
    return int((pd.DatetimeIndex(trades["exit_ts"]) < SPLIT).sum())


def summarise(trades: pd.DataFrame) -> dict:
    dev = trades[pd.DatetimeIndex(trades["exit_ts"]) < SPLIT]
    oos = trades[pd.DatetimeIndex(trades["exit_ts"]) >= SPLIT]
    return {"all": metrics.summarize(trades), "dev": metrics.summarize(dev),
            "oos": metrics.summarize(oos)}


def curve(trades: pd.DataFrame) -> str:
    pnl = trades["net_pnl"].to_numpy()
    return line_chart(range(len(pnl)), list(np.cumsum(pnl)),
                      divider=(split_index(trades), ("樣本外由此開始",
                                                     "out of sample starts here")),
                      ends=(("2016", "2016"), ("2026", "2026")))


def period_table(rows: dict) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("", ""), ("交易", "trades"), ("勝率", "win rate"), ("平均 R", "avg R"),
        ("獲利因子", "profit factor"), ("淨損益", "net P&amp;L"))) + "</tr>"
    labels = {"dev": ("開發期 2016–2024", "development 2016-2024"),
              "oos": ("樣本外 2024–2026", "out of sample 2024-2026"),
              "all": ("全部 11 年", "all eleven years")}
    body = "".join(
        f'<tr><td>{t(*labels[key])}</td><td>{m["trades"]:,}</td>'
        f'<td>{m["win_rate"]:.1%}</td><td>{m["avg_r"]:+.3f}</td>'
        f'<td>{m["profit_factor"]:.2f}</td>'
        f'<td class="v {"up" if m["total_pnl"] > 0 else "down"}">'
        f'{esc(money(m["total_pnl"]))}</td></tr>'
        for key, m in rows.items())
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def payoff_table() -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("停利", "target"), ("勝率", "win rate"), ("獲利因子", "profit factor"),
        ("平均 R", "avg R"))) + "</tr>"
    body = "".join(
        f'<tr><td class="nb">{r:.2f}R</td><td>{w:.1%}</td><td>{pf:.2f}</td>'
        f'<td class="v {"up" if avg > 0 else "down"}">{avg:+.3f}</td></tr>'
        for r, w, pf, avg in PAYOFF_TABLE)
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def family_table() -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("進場方式", "entry"), ("組合數", "combos"), ("開發期 PF", "dev PF"),
        ("樣本外 PF", "oos PF"), ("兩期都賺", "profitable in both"))) + "</tr>"
    names = {"break_first": ("突破直接進", "break, enter at once"),
             "break_retest": ("突破回踩進", "break, then retest"),
             "fail_fade": ("假突破反做", "failed break, faded")}
    body = "".join(
        f'<tr><td>{t(*names[k])}</td><td>{n}</td><td>{dev:.2f}</td>'
        f'<td>{oos:.2f}</td>'
        f'<td class="v {"up" if both > 0 else "down"}">{both:.0%}</td></tr>'
        for k, n, dev, oos, both in FAMILY_TABLE)
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def topstep_table(runs: dict) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("每單風險", "risk"), ("買的帳戶", "accounts"), ("拿到 XFA", "XFA"),
        ("出金", "payouts"), ("入袋", "cash"), ("成本", "cost"),
        ("淨利", "net"), ("ROI", "ROI"),
        ("最久沒進帳", "longest dry"))) + "</tr>"
    body = []
    for risk, r in runs.items():
        dry = t(f'{r["longest_dry_days"]:,.0f} 天',
                f'{r["longest_dry_days"]:,.0f} days')
        body.append(
            f'<tr><td class="nb">${risk:,.0f}</td>'
            f'<td>{r["accounts_bought"]}</td><td>{r["funded_earned"]}</td>'
            f'<td>{r["payouts"]}</td><td>{esc(money(r["cash"]))}</td>'
            f'<td>&minus;${r["cost"]:,.0f}</td>'
            f'<td class="v {"up" if r["net"] > 0 else "down"}">'
            f'{esc(money(r["net"]))}</td><td>{r["roi"]:+.0%}</td>'
            f'<td>{dry}</td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')

def build(ctx: dict) -> str:
    sel, unsel = ctx["selected"], ctx["unselected"]
    m = unsel["periods"]["all"]
    dev, oos = unsel["periods"]["dev"], unsel["periods"]["oos"]

    tiles = "".join([
        tile(t("交易筆數", "trades"), f'{m["trades"]:,}',
             t("11 年，一天最多一筆", "eleven years, one a day at most")),
        tile(t("勝率", "win rate"), f'{m["win_rate"]:.1%}',
             t("停利 1.0R，和停損等距", "target 1.0R, the same distance as the stop")),
        tile(t("平均每筆", "average trade"), f'{m["avg_r"]:+.3f}R',
             t(f'獲利因子 {m["profit_factor"]:.2f}',
               f'profit factor {m["profit_factor"]:.2f}'),
             "up" if m["avg_r"] > 0 else "down"),
        tile(t("淨損益", "net P&amp;L"), esc(money(m["total_pnl"])),
             t(f'每單風險 ${RISK:,.0f}', f'at ${RISK:,.0f} a trade'),
             "up" if m["total_pnl"] > 0 else "down"),
    ])

    verdict = '<div class="verdict">' + t(
        f'<b>這條曲線是搜出來的，所以先看那條垂直線。</b>'
        f'線左邊是搜尋挑參數時看得到的資料，線右邊不是。'
        f'開發期每筆 {dev["avg_r"]:+.3f}R，樣本外 {oos["avg_r"]:+.3f}R —— '
        f'兩邊沒有斷掉，這是它值得繼續看的唯一理由。'
        f'但<b>優勢很薄</b>：獲利因子 {m["profit_factor"]:.2f}，'
        f'平均每筆 {m["avg_r"]:+.3f}R。這是一個待驗證的假設，不是結論。',
        f'<b>This curve came out of a search, so look at the vertical line '
        f'first.</b> Everything left of it is data the search could see when it '
        f'picked; everything right of it is not. The two sides read '
        f'{dev["avg_r"]:+.3f}R and {oos["avg_r"]:+.3f}R a trade, and that they '
        f'do not break is the only reason this is worth more of your time. '
        f'<b>The edge is thin</b>: a profit factor of {m["profit_factor"]:.2f} '
        f'and {m["avg_r"]:+.3f}R a trade. A hypothesis, not a finding.') + '</div>'

    caveats = [
        ("<b>這是搜尋，不是預先註冊。</b>900 組參數在我已經看過的資料上比過，"
         "最好的那一格按定義就是最幸運的一格。<code>registry.py</code> "
         "那七個策略是先寫死再跑的，這個不是。",
         "<b>A search, not a pre-registration.</b> 900 combinations compared on "
         "data already examined; the top cell is by construction the luckiest "
         "draw. The seven in <code>registry.py</code> were specified before "
         "their results existed. This was not."),
        (f"<b>2026 年是負的。</b>所有變體都一樣。最近的一年是最弱的一年，"
         f"而那正是離你下個月開始交易最近的一段。",
         f"<b>2026 is negative</b> in every variant. The most recent year is the "
         f"weakest one, and it is the stretch closest to when you would start."),
        ("<b>沒有 forward test。</b>LTF Sweep 有 <code>scripts/forward_log.py</code>"
         "在跑，從 2026-08-29 之後的每一根新 K 棒都是乾淨的檢驗。這個還沒有。",
         "<b>No forward test yet.</b> LTF Sweep has one running in "
         "<code>scripts/forward_log.py</code>, where every bar after 2026-08-29 "
         "is a clean check. This has nothing equivalent."),
        ("<b>成交模型偏向它。</b>停損單進場算了一檔滑價，但真實的停損單在快市裡"
         "滑得更多，而這個策略正好進在剛被突破反轉的價位附近。",
         "<b>The fill model favours it.</b> Stop entries are charged one tick, "
         "and a real stop in a fast market pays more -- which is exactly the "
         "condition this model enters in."),
    ]

    return page("Failed Breakout Fade", [
        f'<h1>{t("假突破反做 —— 收益曲線", "Failed breakout fade")}</h1>',
        '<p class="sub">' + t(
            f'NQ 1 分鐘 · {HISTORY_START} 至 {esc(ctx["data_end"])} · '
            f'每單風險 ${RISK:,.0f} · 900 組參數搜尋的產物',
            f'NQ 1-minute &middot; {HISTORY_START} to {esc(ctx["data_end"])} '
            f'&middot; ${RISK:,.0f} a trade &middot; the output of a 900-way '
            f'search') + '</p>',
        f'<div class="tiles">{tiles}</div>',
        verdict,

        f'<h2>{t("收益曲線：沒被選中的那一組", "The curve nothing selected")}</h2>',
        '<p>' + t(
            "先看這一條。它是假突破反做這一家的普通成員，搜尋沒有挑它 —— "
            "所以它沒有「最好的那一格」自帶的運氣。",
            "Start here. This is an ordinary member of the fade family that the "
            "search did not pick, so it carries none of the luck that being "
            "ranked first implies.") + '</p>',
        f'<div class="card">{unsel["curve"]}</div>',
        period_table(unsel["periods"]),

        f'<h2>{t("收益曲線：被選中的那一組", "The curve the search picked")}</h2>',
        '<p>' + t(
            "同一個家族，多了一條「開盤 K 實體 ≥ 區間 40%」的過濾。"
            "它在 900 組裡排第一 —— <b>兩條形狀相似才有意義</b>；"
            "如果只有這條好看，那好看的是排名，不是規則。",
            "The same family with one filter added: an opening body of at least "
            "40% of the range. It ranked first of 900. <b>What matters is that "
            "the two shapes agree</b> -- if only this one looks good, the "
            "ranking was the finding rather than the rule.") + '</p>',
        f'<div class="card">{sel["curve"]}</div>',
        period_table(sel["periods"]),

        f'<h2>{t("從高點起算的回撤", "Drawdown from the running peak")}</h2>',
        '<p>' + t(
            f'配 Topstep 50K 的 &minus;$2,000 上限線。每單風險 ${RISK:,.0f} 之下'
            f'這條曲線穿過它很多次 —— 那是倉位的問題，不是策略的問題，'
            f'下面的帳戶結構那節用三檔風險重算過。',
            f'Against the Topstep 50K &minus;$2,000 limit. At ${RISK:,.0f} a '
            f'trade the curve passes it repeatedly -- a position-sizing fact '
            f'rather than a strategy one, which the account section below '
            f'recomputes at three sizes.') + '</p>',
        f'<div class="card">{unsel["dd"]}</div>',

        f'<h2>{t("逐年", "Year by year")}</h2>',
        f'<div class="card">{ctx["years_svg"]}</div>',
        '<p>' + t(
            f'11 年裡 {ctx["years_positive"]} 年的淨損益為正。'
            f'以「每筆平均 R」計是 9 年為正，換停損規則（固定點數、回踩極值）'
            f'還是 8–9 年 —— 它不是只在某一組參數上成立。',
            f'{ctx["years_positive"]} of eleven years are positive in dollars. '
            f'Measured as mean R a trade it is nine, and still eight or nine '
            f'when the stop rule is swapped for a fixed distance or the pullback '
            f'extreme. It does not live in one parameter set.') + '</p>',

        f'<h2>{t("你原本要的高勝率", "The high win rate you asked for")}</h2>',
        '<p>' + t(
            "同一批交易，只改停利。<b>勝率單調上升，期望值單調下降</b> —— "
            "0.25R 的停利確實給你 78.8% 的勝率，代價是每筆 &minus;0.027R。"
            "停利拉近讓觸及機率上升的幅度，幾乎剛好等於每筆賺得變少的幅度。",
            "The same trades with only the target moved. <b>The win rate rises "
            "monotonically and the expectancy falls</b>: a 0.25R target really "
            "does hit 78.8% of the time, at &minus;0.027R a trade. Moving the "
            "target closer raises the hit rate by very nearly the amount it "
            "shrinks the win.") + '</p>',
        payoff_table(),

        f'<h2>{t("為什麼是反做，不是突破", "Why fading, not breaking")}</h2>',
        family_table(),
        '<p>' + t(
            "<b>600 組突破裡沒有一組兩期都賺。</b>這包含上一輪那個 OR5 突破回踩 —— "
            "它不是寫壞了，是整個方向沒有優勢。",
            "<b>Not one of 600 breakout combinations is profitable in both "
            "periods.</b> That includes the OR5 retest from the previous round: "
            "it was not implemented badly, the direction has no edge.") + '</p>',

        f'<h2>{t("放進 Topstep 帳戶結構", "Inside the Topstep account structure")}</h2>',
        topstep_table(ctx["topstep"]),
        '<p class="muted">' + t(
            "11 年一條不斷的鏈，一符合資格就出金，規則同 "
            "<code>backtest/recycle.py</code>。對照 LTF Sweep 在 $900 是 "
            "+$42,414、最久 1,184 天沒進帳。",
            "One unbroken eleven-year chain, taking payouts as soon as eligible, "
            "under the same rules as <code>backtest/recycle.py</code>. LTF Sweep "
            "at $900 returns +$42,414 with a longest dry spell of 1,184 days.") + '</p>',

        f'<h2>{t("這份東西是什麼、不是什麼", "What this is and is not")}</h2>',
        '<ul class="caveats">',
        *(f"<li>{t(zh, en)}</li>" for zh, en in caveats),
        "</ul>",
    ], t(
        "由 <code>scripts/fade_report.py</code> 產生，規則在 "
        "<code>strategies/orb_family.py</code>，搜尋在 "
        "<code>scripts/search_orb.py</code>。每個數字重跑就能查證。",
        "Generated by <code>scripts/fade_report.py</code>. The rules are in "
        "<code>strategies/orb_family.py</code> and the search is "
        "<code>scripts/search_orb.py</code>. Rerun either to check any figure."))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    bars = D.add_time_columns(pd.read_parquet(
        D.PROCESSED_DIR / "nq_1m.parquet",
        filters=[("ts", ">=", pd.Timestamp("2015-12-01", tz="UTC"))])
    ).reset_index(drop=True)
    setups = detect(bars, DetectConfig())

    ctx = {}
    for name, kw in VARIANTS.items():
        trades = run(setups, bars, **kw)
        ctx[name] = {"trades": trades, "curve": curve(trades),
                     "periods": summarise(trades)}
        m = ctx[name]["periods"]
        print(f"{name:<11} {m['all']['trades']:>5} trades  "
              f"win {m['all']['win_rate']:.1%}  PF {m['all']['profit_factor']:.2f}  "
              f"avg R {m['all']['avg_r']:+.3f}  "
              f"dev {m['dev']['avg_r']:+.3f} / oos {m['oos']['avg_r']:+.3f}")

    base = ctx["unselected"]["trades"]
    ctx["unselected"]["dd"] = line_chart(
        range(len(base)), list(drawdown(base["net_pnl"].to_numpy())),
        limit=2000.0, fill=True,
        # Label omitted: the limit label already owns that corner, and the two
        # curves above have established what the line means.
        divider=(split_index(base), None),
        ends=(("2016", "2016"), ("2026", "2026")))

    years = base.assign(y=pd.DatetimeIndex(base["exit_ts"])
                        .tz_convert("America/New_York").year)
    by_year = years.groupby("y")["net_pnl"].sum()
    ctx["years_svg"] = bar_chart([f"’{y % 100:02d}" for y in by_year.index],
                                 list(by_year.values))
    ctx["years_positive"] = int((by_year > 0).sum())

    ctx["topstep"] = {
        risk: recycle(run(setups, bars, risk=risk, **VARIANTS["unselected"]),
                      policy=Policy.IMMEDIATE)
        for risk in (250.0, 500.0, 900.0)
    }

    ctx["data_end"] = str(base["exit_ts"].max().tz_convert("America/New_York").date())

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(ctx), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
