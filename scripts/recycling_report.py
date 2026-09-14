"""Topstep recycling simulator: is buying evaluations a business?

    uv run python scripts/recycling_report.py

Writes ``docs/recycling_report.html``. The quarterly report stops at the first
pass, which is where the spending ends and before any money arrives. This one
keeps going -- pass, get funded, take payouts, lose the account, buy another --
and reports cash withdrawn against cash spent.

Two things it refuses to do, both of which would make the answer look better
than it is:

**It does not count money sitting in a funded account.** A run that ends with
$2,800 of balance and no payout earned nothing; the account can still die.

**It does not draw its worst cases from a shuffle.** Reordering trades treats
each attempt as independent, and this model's losses arrive in runs -- the same
year needs 9 evaluations in its real order and far fewer shuffled. Worst cases
come from the 12-month windows that actually happened across 2016-2026. The
shuffle is shown too, beside them, as a measure of how much it flatters.
"""

from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd

from backtest.engine import BacktestConfig, simulate
from backtest.recycle import Policy, XFARules, recycle
from ict import data as D
from reportkit import bar_chart, esc, line_chart, money, page, t, tile
from strategies.ltf_sweep import generate_orders

OUT = pathlib.Path("docs/recycling_report.html")
RULES = XFARules()

#: The ladder the question asked for. The two-stage row is the plan in
#: docs/GO_LIVE.md: $900 evaluations, $250 once funded.
LADDER = (100.0, 200.0, 250.0, 500.0, 900.0)
TWO_STAGE = (900.0, 250.0 / 900.0)

#: History used for the distribution. 2016 rather than 2010 for the reason
#: scripts/run_backtest.py gives: the Asian session is only fully populated
#: from then.
HISTORY_START = "2016-01-01"
#: The window the quarterly report covers, so the two can be read together.
YEAR = ("2025-10-01", "2026-09-08")

#: Trades per synthetic window, matched to the real year so the bootstrap
#: comparison is about ordering rather than sample size.
BOOTSTRAP_N = 198
BOOTSTRAP_RUNS = 2000
BOOTSTRAP_BLOCK = 20


def load_bars(start: str) -> pd.DataFrame:
    lead = (pd.Timestamp(start) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    frame = pd.read_parquet(D.PROCESSED_DIR / "nq_1m.parquet",
                            filters=[("ts", ">=", pd.Timestamp(lead, tz="UTC"))])
    return D.add_time_columns(frame).reset_index(drop=True)


def fills(orders: pd.DataFrame, bars: pd.DataFrame, risk: float) -> pd.DataFrame:
    trades = simulate(orders, bars, BacktestConfig(risk_per_trade_usd=risk))
    return trades[trades["filled"]].sort_values("exit_ts").reset_index(drop=True)


def between(trades: pd.DataFrame, start, end) -> pd.DataFrame:
    inside = ((trades["entry_ts"] >= pd.Timestamp(start, tz="UTC")) &
              (trades["entry_ts"] < pd.Timestamp(end, tz="UTC")))
    return trades[inside].reset_index(drop=True)


def run_row(trades: pd.DataFrame, risk: float, scale: float = 1.0,
            policy: Policy = Policy.LOCK_FIRST) -> dict:
    out = recycle(trades, policy=policy, funded_scale=scale)
    return {"risk": risk, "funded_risk": risk * scale, "trades": len(trades), **out}


def windows(trades: pd.DataFrame, months: int = 12, step_months: int = 1):
    """Every real ``months``-long window, oldest first.

    Same stepping as ``backtest.metrics.rolling_windows``; that one scores a
    window with ``summarize``, this one needs the trades themselves to run the
    account through them.
    """
    exits = pd.DatetimeIndex(trades["exit_ts"]).tz_convert("UTC")
    start, last = exits.min(), exits.max()
    while start + pd.DateOffset(months=months) <= last + pd.DateOffset(days=1):
        end = start + pd.DateOffset(months=months)
        chunk = trades[(exits >= start) & (exits < end)]
        if len(chunk) >= 20:
            yield start, end, chunk.reset_index(drop=True)
        start = start + pd.DateOffset(months=step_months)


def calendar_years(trades: pd.DataFrame):
    """Non-overlapping years -- the observations, as opposed to the windows."""
    years = sorted(pd.DatetimeIndex(trades["exit_ts"]).tz_convert("UTC").year.unique())
    for year in years:
        chunk = between(trades, f"{year}-01-01", f"{year + 1}-01-01")
        if len(chunk) >= 20:
            yield year, chunk


def resequence(pnl: np.ndarray, contracts: np.ndarray) -> pd.DataFrame:
    """A synthetic run of trades, one per business day.

    Resampling produces duplicate timestamps, and ``recycle`` refuses two
    trades on one day because the loss limit is an end-of-day rule. Fresh
    consecutive dates keep the accounting honest: a synthetic sequence has no
    real dates anyway, and the spacing is what the cost side reads.
    """
    days = pd.bdate_range("2016-01-04", periods=len(pnl), tz="UTC")
    return pd.DataFrame({"net_pnl": pnl, "contracts": contracts,
                         "entry_ts": days, "exit_ts": days,
                         "trading_date": days.date})


def bootstrap(pool: pd.DataFrame, block: int, runs: int, seed: int) -> np.ndarray:
    """Net cash over ``runs`` synthetic years drawn from ``pool``.

    ``block`` of 1 is the ordinary shuffle: every trade drawn independently,
    which is exactly the assumption that losing runs do not exist. A larger
    block keeps that many consecutive trades together, so some clustering
    survives the resampling.
    """
    rng = np.random.default_rng(seed)
    pnl = pool["net_pnl"].to_numpy()
    contracts = pool["contracts"].to_numpy()
    out = np.empty(runs)
    for i in range(runs):
        if block <= 1:
            idx = rng.integers(0, len(pnl), BOOTSTRAP_N)
        else:
            starts = rng.integers(0, len(pnl) - block, BOOTSTRAP_N // block + 1)
            idx = np.concatenate([np.arange(s, s + block) for s in starts])[:BOOTSTRAP_N]
        out[i] = recycle(resequence(pnl[idx], contracts[idx]))["net"]
    return out


def pct(values: np.ndarray) -> dict:
    """The tail the question asked for, plus the middle to read it against."""
    return {"median": float(np.median(values)),
            "p10": float(np.percentile(values, 10)),
            "p5": float(np.percentile(values, 5)),
            "p1": float(np.percentile(values, 1)),
            "worst": float(values.min()),
            "best": float(values.max()),
            "profitable": float((values > 0).mean())}


# --------------------------------------------------------------------- render

def ladder_table(rows: list[dict]) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("每單風險", "risk"), ("帳戶", "accounts"), ("XFA", "XFA"),
        ("出金", "payouts"), ("總出金", "gross"), ("入袋", "kept"),
        ("成本", "cost"), ("淨利", "net"), ("ROI", "ROI"),
        ("連爆", "streak"), ("沒領走", "left in"))) + "</tr>"
    body = []
    for r in rows:
        label = (f'${r["risk"]:,.0f}' if r["funded_risk"] == r["risk"]
                 else f'${r["risk"]:,.0f} &rarr; ${r["funded_risk"]:,.0f}')
        cls = "up" if r["net"] > 0 else "down"
        body.append(
            f'<tr><td class="nb">{label}</td><td>{r["accounts_bought"]}</td>'
            f'<td>{r["funded_earned"]}</td><td>{r["payouts"]}</td>'
            f'<td>{esc(money(r["gross_payout"]))}</td>'
            f'<td>{esc(money(r["cash"]))}</td>'
            f'<td>&minus;${r["cost"]:,.0f}</td>'
            f'<td class="v {cls}">{esc(money(r["net"]))}</td>'
            f'<td>{r["roi"]:+.0%}</td><td>{r["longest_bust_streak"]}</td>'
            f'<td class="muted">{esc(money(r["unrealised"]))}</td></tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def policy_table(rows: list[tuple[float, dict, dict]]) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("每單風險", "risk"), ("A 一符合就領", "A: take it early"),
        ("B 先鎖再領", "B: lock first"), ("差額", "difference"))) + "</tr>"
    body = "".join(
        f'<tr><td class="nb">${risk:,.0f}</td>'
        f'<td>{esc(money(a["net"]))} <span class="muted">({a["payouts"]})</span></td>'
        f'<td>{esc(money(b["net"]))} <span class="muted">({b["payouts"]})</span></td>'
        f'<td class="v {"up" if b["net"] >= a["net"] else "down"}">'
        f'{esc(money(b["net"] - a["net"]))}</td></tr>'
        for risk, a, b in rows)
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def dist_table(real: dict, block: dict, shuffled: dict, n_windows: int) -> str:
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("", ""), ("中位數", "median"), ("10% 最差", "10% worst"),
        ("5% 最差", "5% worst"), ("1% 最差", "1% worst"),
        ("最差", "worst"), ("賺錢的比例", "share profitable"))) + "</tr>"
    rows = [
        (t(f"真實的 12 個月視窗（{n_windows} 個）",
           f"real 12-month windows ({n_windows})"), real),
        (t(f"打亂但保留 {BOOTSTRAP_BLOCK} 筆連續",
           f"block bootstrap, {BOOTSTRAP_BLOCK} in a row"), block),
        (t("完全打亂順序", "shuffled, every trade independent"), shuffled),
    ]
    body = "".join(
        f'<tr><td>{label}</td>'
        f'<td>{esc(money(d["median"]))}</td><td>{esc(money(d["p10"]))}</td>'
        f'<td>{esc(money(d["p5"]))}</td><td>{esc(money(d["p1"]))}</td>'
        f'<td>{esc(money(d["worst"]))}</td><td>{d["profitable"]:.0%}</td></tr>'
        for label, d in rows)
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def year_table(rows: list[tuple[int, dict]]) -> str:
    # The last year runs to the end of the data, which is not the end of a year.
    partial = '<span class="muted">' + t("（未完）", " (partial)") + "</span>"
    head = "<tr>" + "".join(f"<th>{t(zh, en)}</th>" for zh, en in (
        ("年", "year"), ("交易", "trades"), ("買的帳戶", "accounts"),
        ("拿到 XFA", "XFA"), ("出金", "payouts"), ("入袋", "cash"),
        ("成本", "cost"), ("淨利", "net"))) + "</tr>"
    body = "".join(
        f'<tr><td>{year}{partial if year == rows[-1][0] else ""}</td>'
        f'<td>{r["trades"]}</td>'
        f'<td>{r["accounts_bought"]}</td><td>{r["funded_earned"]}</td>'
        f'<td>{r["payouts"]}</td><td>{esc(money(r["cash"]))}</td>'
        f'<td>&minus;${r["cost"]:,.0f}</td>'
        f'<td class="v {"up" if r["net"] > 0 else "down"}">'
        f'{esc(money(r["net"]))}</td></tr>'
        for year, r in rows)
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def build(ctx: dict) -> str:
    """The page. Composed from pieces, not one f-string."""
    year = ctx["headline"]
    whole = ctx["whole"]
    real = ctx["real"]
    raw = ctx["raw_pnl"]

    tiles = "".join([
        tile(t("11 年淨現金", "net cash, 11 years"), esc(money(whole["net"])),
             t(f'花了 ${whole["cost"]:,.0f}，領回 ${whole["cash"]:,.0f}',
               f'${whole["cost"]:,.0f} spent, ${whole["cash"]:,.0f} withdrawn'),
             "up" if whole["net"] > 0 else "down"),
        tile(t("ROI", "ROI"), f'{whole["roi"]:+.0%}',
             t(f'買了 {whole["accounts_bought"]} 個帳戶',
               f'{whole["accounts_bought"]} accounts bought')),
        tile(t("策略自己的損益", "the strategy's own P&L"), esc(money(raw)),
             t("同一批交易，沒有帳戶結構",
               "the same trades, without the account structure"),
             "down" if raw < 0 else "up"),
        tile(t("最久沒有進帳", "longest without a payout"),
             t(f'{whole["longest_dry_days"]:,.0f} 天',
               f'{whole["longest_dry_days"]:,.0f} days'),
             t(f'連續 {whole["longest_bust_streak"]} 個帳戶爆掉',
               f'{whole["longest_bust_streak"]} dead accounts in a row'), "bad"),
    ])

    verdict = '<div class="verdict">' + t(
        f'<b>會賺錢，但賺的不是交易。</b>同一批 {ctx["history_trades"]:,} 筆交易，'
        f'自己做是 {esc(money(raw))}；放進 Topstep 的帳戶結構裡是 '
        f'<b>{esc(money(whole["net"]))}</b>。差別在於<b>虧損不是你付的</b> —— '
        f'一個帳戶爆掉你只付 ${RULES.reset:,.0f} 的重置費，那 $2,000 是 Topstep 吃的。'
        f'你買的不是交易的期望值，是一張 ${RULES.reset:,.0f} 的彩票。',
        f'<b>It makes money, and not from trading.</b> The same '
        f'{ctx["history_trades"]:,} trades return {esc(money(raw))} on their own '
        f'and <b>{esc(money(whole["net"]))}</b> inside Topstep\'s account '
        f'structure. The difference is that <b>you do not pay the losses</b>: a '
        f'dead account costs you the ${RULES.reset:,.0f} reset, and Topstep '
        f'absorbs the $2,000. What is being bought is not the edge. It is a '
        f'${RULES.reset:,.0f} lottery ticket on one.') + '</div>'

    caveats = [
        ("<b>這是回測，一張單都沒有真的送出去過。</b>成交模型偏向它（限價單碰到就算成交），"
         "資料源是 Databento 不是 TopstepX。季度報告那份的每一條保留都還在。",
         "<b>A backtest; no order here was ever sent.</b> The fill model favours "
         "it, and the data is Databento rather than TopstepX's feed. Every "
         "caveat in the quarterly report still applies."),
        (f"<b>規則是 2026-09-14 查的，而規則會變。</b>出金門檻、上限、分成、"
         f"MLL 鎖死的條件，全部寫在 <code>backtest/recycle.py</code> 的 "
         f"<code>XFARules</code> 裡，改一個地方就會全部重算。",
         f"<b>The rules were read on 2026-09-14 and rules change.</b> Payout "
         f"thresholds, caps, the split and the MLL lock all live in "
         f"<code>XFARules</code> in <code>backtest/recycle.py</code>; edit there "
         f"and every figure recomputes."),
        ("<b>這整套只在 Express Funded 這一層成立。</b>官方書面答覆寫得很清楚："
         "<b>Live Funded 不能透過 TopstepX API</b>。而拿到 Live Funded 的時候"
         "「所有 Express Funded 帳戶都會關閉」。所以這個模型假設你一直留在 XFA —— "
         "我沒有找到強制升級的規則，但如果有，這裡的跑道就比算出來的短。",
         "<b>All of this lives at the Express Funded layer.</b> Topstep's written "
         "answer is explicit that <b>Live Funded Accounts cannot use the TopstepX "
         "API</b>, and receiving a Live Funded Account closes every Express Funded "
         "one. So the model assumes you stay on XFAs indefinitely. I could not "
         "find a rule forcing the upgrade, but if one exists the runway is "
         "shorter than this."),
        ("<b>出金不是瞬間到帳，也沒有算稅。</b>模型在符合資格的那一天就記入現金。",
         "<b>Payouts are neither instant nor taxed here.</b> The model books the "
         "cash on the day it becomes eligible."),
        (f"<b>百分位的有效樣本只有 11。</b>{len(ctx['windows'])} 個視窗是重疊的，"
         f"背後只有 11 個年份。「1% 最壞情況」是外插，不是量出來的 —— "
         f"要看真的發生過什麼，看上面的逐年表。",
         f"<b>The percentiles rest on 11 observations.</b> The "
         f"{len(ctx['windows'])} windows overlap heavily and come from 11 years. "
         f"A \u201c1% worst case\u201d from that is an extrapolation. For what "
         f"actually happened, read the year-by-year table above."),
    ]

    # Two-digit years: eleven four-digit labels run into each other, and the
    # table directly below carries every number anyway.
    year_bars = bar_chart([f"\u2019{y % 100:02d}" for y, _ in ctx["years"]],
                          [r["net"] for _, r in ctx["years"]])

    nets = [e["net"] for e in whole["timeline"]]
    curve = line_chart(range(len(nets)), nets, ends=(
        ("2016", "2016"), (esc(ctx["data_end"][:4]), esc(ctx["data_end"][:4]))))

    # The <title> tag is the browser tab and the artifact's name; it cannot
    # carry the bilingual spans the body uses, so it is one plain string.
    return page("Topstep Recycling Simulator", [
        f'<h1>{t("Topstep 回收模擬器", "Topstep recycling simulator")}</h1>',
        '<p class="sub">' + t(
            f'爆了再買，拿到 funded 就領錢，領完繼續 · 每單風險 $900 · '
            f'{HISTORY_START} 至 {esc(ctx["data_end"])}',
            f'Buy, pass, get paid, lose it, buy again &middot; $900 a trade '
            f'&middot; {HISTORY_START} to {esc(ctx["data_end"])}') + '</p>',
        f'<div class="tiles">{tiles}</div>',
        verdict,

        f'<h2>{t("錢什麼時候進來", "When the money arrives")}</h2>',
        '<p>' + t(
            f'一條線：領到的現金減掉付出去的錢，從 2016 年第一個帳戶開始。'
            f'總數是 {esc(money(whole["net"]))}，但<b>前 {whole["longest_dry_days"]:,.0f} '
            f'天一毛錢都沒有進來</b>，而且中間連續 {whole["longest_bust_streak"]} '
            f'個帳戶爆掉。會殺死這門生意的不是最後那個數字，是這段。',
            f'Cash withdrawn less money spent, from the first account in 2016. '
            f'The total is {esc(money(whole["net"]))}, but <b>the first '
            f'{whole["longest_dry_days"]:,.0f} days returned nothing at all</b> '
            f'and {whole["longest_bust_streak"]} accounts died in a row along the '
            f'way. What ends this business is not the last number; it is that '
            f'stretch.') + '</p>',
        f'<div class="card">{curve}</div>',

        f'<h2>{t("逐年", "Year by year")}</h2>',
        '<p>' + t(
            "每一年都當成重新開始：一個新帳戶、新的計費。"
            "所以這張表問的是「如果你那一年才進場」。",
            "Each year starts fresh -- a new account and a new billing clock -- "
            "so this table asks what happens if that is the year you start.") + '</p>',
        f'<div class="card">{year_bars}</div>',
        year_table(ctx["years"]),
        '<p>' + t(
            f'11 年裡 {sum(1 for _, r in ctx["years"] if r["net"] > 0)} 年是賺的 —— '
            f'但<b>開頭三年全部是虧的</b>。2016 年進場的人要熬到第四年才看到第一筆出金。',
            f'{sum(1 for _, r in ctx["years"] if r["net"] > 0)} of the 11 years '
            f'made money &mdash; but <b>the first three all lost</b>. Someone who '
            f'started in 2016 waited until the fourth year for a first payout.') + '</p>',

        f'<h2>{t("換成別的倉位大小", "At each position size")}</h2>',
        '<p>' + t(
            f'走季度報告的同一年（{YEAR[0]} 至 {esc(ctx["data_end"])}），'
            f'好讓兩份報告可以對着看。最後一行是 <code>docs/GO_LIVE.md</code> '
            f'裡的兩階段計畫：考試 $900，funded 之後降到 $250。',
            f'Over the same year as the quarterly report ({YEAR[0]} to '
            f'{esc(ctx["data_end"])}) so the two can be read together. The last '
            f'row is the two-stage plan in <code>docs/GO_LIVE.md</code>: $900 '
            f'evaluations, $250 once funded.') + '</p>',
        ladder_table(ctx["ladder"]),
        '<p>' + t(
            "「沒領走」那一欄是沒有領出來的餘額。它<b>不算收入</b> —— "
            "帳戶還會死，死了就沒了。$100/單那一行整年連一次 funded 都沒拿到。"
            "<b>但這一年不能拿來挑倉位。</b>$200 和 $250 在這一年是小賺，"
            "放到 11 年卻是實實在在的虧損（下一節那張 11 年的表）—— "
            "倉位太小的問題是月費一直在走，而 $3,000 的目標好幾年都到不了。",
            "\u201cLeft behind\u201d is balance never withdrawn. It is <b>not "
            "income</b>: the account can still die and take it. The $100 row never "
            "reached a funded account at all. <b>Do not pick a size from this "
            "year, though.</b> $200 and $250 show a small profit here and a real "
            "loss over eleven (the eleven-year table in the next section): too "
            "small a position keeps paying the subscription while the $3,000 "
            "target stays out of reach for years at a time.") + '</p>',

        f'<h2>{t("要不要先鎖住再領", "Take it early, or lock it first")}</h2>',
        '<p>' + t(
            f'XFA 的虧損上限追着<b>收盤最高餘額</b>走，而餘額一旦到 '
            f'${RULES.mll_locks_at:,.0f}，上限就永久鎖在 $0 —— 之後帳戶殺不死，'
            f'只會被掏空。<b>出金會把餘額拉下來，但不會把那個最高點拉下來</b>，'
            f'所以太早領錢等於一直不買那張保險。',
            f'An XFA\'s loss limit follows the highest end-of-day balance, and '
            f'once the balance reaches ${RULES.mll_locks_at:,.0f} the limit locks '
            f'at $0 for good: from then on the account cannot be killed, only '
            f'emptied. <b>A payout lowers the balance but not the high-water '
            f'mark</b>, so taking money early means never buying that insurance.') + '</p>',
        f'<h3>{t("報告那一年", "over the report year")}</h3>',
        policy_table(ctx["policies"]),
        f'<h3>{t("整整 11 年", "over the whole eleven years")}</h3>',
        policy_table(ctx["policies_long"]),
        '<p>' + t(
            f'一年的樣本說 B 略好，11 年的樣本說 <b>A 在每一檔都贏，而且贏 '
            f'${abs(min(b["net"] - a["net"] for _, a, b in ctx["policies_long"])):,.0f} '
            f'上下</b>。理由不難懂：帳戶在鎖上之前就死掉的次數夠多，'
            f'等待那張保險的代價比保險本身貴。'
            f'{ctx["risk_note"]}'
            f'<b>結論是領得早比領得巧重要</b> —— 但要知道這也只是一條路徑，'
            f'不是一個定律。',
            f'One year says B is slightly better. Eleven years say <b>A wins at '
            f'every size, by around '
            f'${abs(min(b["net"] - a["net"] for _, a, b in ctx["policies_long"])):,.0f}'
            f'</b>. The reason is not subtle: accounts die before they lock often '
            f'enough that waiting for the insurance costs more than the insurance '
            f'saves. {ctx["risk_note_en"]}'
            f'<b>Taking it early beats timing it</b> &mdash; though this is still '
            f'one path and not a law.') + '</p>',

        f'<h2>{t("最壞的情況", "The worst cases")}</h2>',
        '<p>' + t(
            f'{len(ctx["windows"])} 個真實的 12 個月視窗，每個都是實際發生過的順序。'
            f'下面兩行是把同一批交易打亂之後的結果，放在這裡是為了說明'
            f'<b>為什麼不能用打亂順序算尾端風險</b>。',
            f'{len(ctx["windows"])} real 12-month windows, each one an order that '
            f'actually happened. The two rows below resample the same trades, and '
            f'are here to show <b>why a shuffle cannot be used for tail risk</b>.') + '</p>',
        dist_table(real, ctx["block"], ctx["shuffled"], len(ctx["windows"])),
        '<p>' + t(
            f'最壞的一年是 {esc(money(real["worst"]))} —— 而且<b>它有底</b>：'
            f'一年最多就是付掉月費、重置和啟用費。這是整個結構最重要的一個性質 —— '
            f'上檔沒有上限，下檔被手續費封住。'
            f'打亂順序的中位數比真實的高（{esc(money(ctx["shuffled"]["median"]))} '
            f'對 {esc(money(real["median"]))}），因為它把連續的虧損打散了。',
            f'The worst year is {esc(money(real["worst"]))}, and it has a floor: '
            f'a year can cost no more than its subscriptions, resets and '
            f'activations. That is the most important property of the structure '
            f'&mdash; the upside is open and the downside is capped by fees. The '
            f'shuffled median is higher than the real one '
            f'({esc(money(ctx["shuffled"]["median"]))} against '
            f'{esc(money(real["median"]))}) because shuffling breaks up the losing '
            f'runs.') + '</p>',

        f'<h2>{t("這份東西是什麼、不是什麼", "What this is and is not")}</h2>',
        '<ul class="caveats">',
        *(f"<li>{t(zh, en)}</li>" for zh, en in caveats),
        "</ul>",
    ], t(
        "由 <code>scripts/recycling_report.py</code> 產生，帳戶邏輯在 "
        "<code>backtest/recycle.py</code>，交易來自和回測同一條 simulate 路徑。"
        "規則參數都在 <code>XFARules</code>，改了重跑就是新的答案。",
        "Generated by <code>scripts/recycling_report.py</code>. The account logic "
        "is <code>backtest/recycle.py</code> and the trades come from the same "
        "simulate path as the backtest. Every rule is a field on "
        "<code>XFARules</code>; edit it and rerun for a different answer."))


def compute(orders: pd.DataFrame, bars: pd.DataFrame) -> dict:
    """Everything the page reports. Separated from rendering so the numbers can
    be printed and checked without building a page."""
    year_rows, policy_rows = [], []
    for risk in LADDER:
        trades = between(fills(orders, bars, risk), *YEAR)
        year_rows.append(run_row(trades, risk))
        policy_rows.append((risk,
                            run_row(trades, risk, policy=Policy.IMMEDIATE),
                            year_rows[-1]))
    staged = between(fills(orders, bars, TWO_STAGE[0]), *YEAR)
    year_rows.append(run_row(staged, TWO_STAGE[0], scale=TWO_STAGE[1]))

    # One year is one path, and on the payout question it gives the opposite
    # answer to eleven. Both are shown rather than the flattering one.
    long_rows = []
    for risk in LADDER:
        h = between(fills(orders, bars, risk), HISTORY_START, "2027-01-01")
        long_rows.append((risk, run_row(h, risk, policy=Policy.IMMEDIATE),
                          run_row(h, risk)))

    # The distribution is built at the plan's own size. Every risk level would
    # be six times the work and the shape of the answer does not change.
    history = between(fills(orders, bars, 900.0), HISTORY_START, "2027-01-01")
    win = [(s, e, recycle(chunk)) for s, e, chunk in windows(history)]
    nets = np.array([w[2]["net"] for w in win])
    # A single total can hide a policy that wins big occasionally and loses
    # most of the time. This counts the windows rather than the dollars.
    early = np.array([recycle(chunk, policy=Policy.IMMEDIATE)["net"]
                      for _, _, chunk in windows(history)])
    lock_wins = float((nets > early).mean())
    years = [(y, run_row(chunk, 900.0)) for y, chunk in calendar_years(history)]

    return {
        "ladder": year_rows,
        # The $900 row of the ladder, which the headline quotes.
        "headline": next(r for r in year_rows if r["risk"] == 900.0
                         and r["funded_risk"] == 900.0),
        # One unbroken chain from 2016 to the end of the data: what someone who
        # started at the beginning and never stopped would actually hold.
        "whole": recycle(history),
        # The same trades with no account structure at all -- the comparison
        # the whole report exists to make.
        "raw_pnl": float(history["net_pnl"].sum()),
        "policies": policy_rows,
        "policies_long": long_rows,
        # How often waiting actually paid, across the real windows.
        "risk_note": f"{len(win)} 個視窗裡也只有 {lock_wins:.0%} 是 B 比較好。",
        "risk_note_en": f"Across the windows, B is ahead in only "
                        f"{lock_wins:.0%} of them. ",
        "windows": win,
        "window_nets": nets,
        "real": pct(nets),
        "block": pct(bootstrap(history, BOOTSTRAP_BLOCK, BOOTSTRAP_RUNS, 7)),
        "shuffled": pct(bootstrap(history, 1, BOOTSTRAP_RUNS, 7)),
        "years": years,
        "history_trades": len(history),
        "data_end": str(history["exit_ts"].max().tz_convert("America/New_York").date()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--orders", default=None,
                    help="parquet of cached orders, to skip the 3-minute scan")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    bars = load_bars(HISTORY_START)
    orders = (pd.read_parquet(args.orders) if args.orders
              else generate_orders(bars))
    ctx = compute(orders, bars)

    print(f"{'risk':>14}  {'acct':>4} {'xfa':>3} {'pay':>3} "
          f"{'cash':>9} {'cost':>7} {'net':>9} {'roi':>6} {'left':>8}")
    for r in ctx["ladder"]:
        label = (f'${r["risk"]:,.0f}' if r["funded_risk"] == r["risk"]
                 else f'${r["risk"]:,.0f}->${r["funded_risk"]:,.0f}')
        print(f"{label:>14}  {r['accounts_bought']:>4} {r['funded_earned']:>3} "
              f"{r['payouts']:>3} {r['cash']:>9,.0f} {r['cost']:>7,.0f} "
              f"{r['net']:>9,.0f} {r['roi']:>+6.0%} {r['unrealised']:>8,.0f}")

    print(f"\n{len(ctx['windows'])} real 12-month windows, "
          f"{ctx['history_trades']} trades since {HISTORY_START}")
    for name in ("real", "block", "shuffled"):
        d = ctx[name]
        print(f"  {name:>9}  median {d['median']:>8,.0f}  p10 {d['p10']:>8,.0f}  "
              f"p5 {d['p5']:>8,.0f}  p1 {d['p1']:>8,.0f}  "
              f"worst {d['worst']:>8,.0f}  profitable {d['profitable']:.0%}")

    print("\nby calendar year")
    for year, r in ctx["years"]:
        print(f"  {year}  {r['trades']:>3} trades  {r['accounts_bought']:>2} acct  "
              f"{r['funded_earned']} xfa  {r['payouts']:>2} pay  "
              f"net {r['net']:>9,.0f}")

    w = ctx["whole"]
    print(f"\nstraight through 2016-{ctx['data_end'][:4]}: "
          f"{w['accounts_bought']} accounts, {w['funded_earned']} XFA, "
          f"{w['payouts']} payouts, cash ${w['cash']:,.0f} less ${w['cost']:,.0f} "
          f"= ${w['net']:,.0f} ({w['roi']:+.0%})")
    print(f"  the strategy on its own over the same trades: ${ctx['raw_pnl']:,.0f}")
    print(f"  worst run of dead accounts {w['longest_bust_streak']}, "
          f"longest stretch with no payout {w['longest_dry_days']:,.0f} days")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(ctx), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
