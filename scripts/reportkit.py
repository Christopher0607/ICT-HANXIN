"""Shared rendering for the standalone report pages.

``scripts/quarterly_report.py`` and ``scripts/recycling_report.py`` produce the
same kind of artefact: one self-contained HTML file, no CDN, bilingual with
Chinese as the view you get when JavaScript never runs, and a print stylesheet
so Ctrl+P gives the same document as ``scripts/report_pdf.py``.

All of that lives here rather than in either script. Two copies of a stylesheet
diverge the first time one of them is edited, and the divergence shows up as a
second report that prints differently from the first.
"""

from __future__ import annotations

import html


# Validated with the dataviz skill's checker against both surfaces:
# light  worst adjacent CVD dE 21.6, normal-vision 32.3, all >= 3:1
# dark   worst adjacent CVD dE 19.2, normal-vision 29.0, all >= 3:1
POS_LIGHT, NEG_LIGHT = "#2a78d6", "#e34948"


def money(value: float, sign: bool = True) -> str:
    fmt = f"{value:+,.0f}" if sign else f"{value:,.0f}"
    return fmt.replace("+-", "-").replace("-", "−")


def esc(text) -> str:
    return html.escape(str(text))


def t(zh: str, en: str) -> str:
    """One phrase in both languages; CSS shows whichever is selected.

    Chinese is the default and is what shows with JavaScript disabled -- the
    toggle only ever adds a class. A page that needs script to render any text
    at all is a page that can arrive blank.

    The arguments are already-escaped HTML, because most call sites need a
    ``<b>`` somewhere; anything interpolated from data goes through ``esc``
    first.
    """
    return f'<span class="zh">{zh}</span><span class="en">{en}</span>'


def svg_t(zh: str, en: str, class_: str = "tick", **attrs) -> str:
    """The same, for SVG. Chart labels are text too: a half-translated chart is
    the first thing anyone notices after switching language."""
    a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (f'<text class="{class_} zh" {a}>{esc(zh)}</text>'
            f'<text class="{class_} en" {a}>{esc(en)}</text>')


def line_chart(xs, ys, *, width=380, height=155, limit=None, fill=False,
               ends=None, divider=None) -> str:
    """One series over trade number. Optional threshold line and fill to zero.

    ``ends`` names the two x-axis labels as ((zh, en), (zh, en)); the default
    counts trades, which is wrong for a series measured in anything else.

    ``divider`` is ``(index, (zh, en))`` and draws a labelled vertical line at
    that point; pass ``(index, None)`` for the line alone, which is what a
    chart already carrying a ``limit`` label needs -- both labels want the same
    corner and the shorter one loses. It exists for one job: marking where a curve stops being the
    data a rule was chosen on and starts being a test of it. Whether the two
    sides of that line look alike is the whole question, and a curve without
    it invites the reader to judge the shape as a single run.
    """
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
        parts.append(
            f'<text class="limit-label zh" x="{width - pad_r}" y="13.5" '
            f'text-anchor="end">虧損上限 &minus;${limit:,.0f}</text>'
            f'<text class="limit-label en" x="{width - pad_r}" y="13.5" '
            f'text-anchor="end">loss limit &minus;${limit:,.0f}</text>')
    if divider is not None:
        at, labels = divider
        x = px(at)
        parts.append(f'<line class="divider" x1="{x:.1f}" y1="{pad_t}" '
                     f'x2="{x:.1f}" y2="{height - pad_b}"/>')
        if labels is not None:
            # Anchored away from the shorter side so the label never runs off.
            anchor = "start" if at < len(xs) / 2 else "end"
            nudge = 4 if anchor == "start" else -4
            parts.append(svg_t(*labels, x=f"{x + nudge:.1f}", y=pad_t - 6,
                               text_anchor=anchor, class_="divider-label"))
    parts.append(f'<polyline class="series" points="{points}"/>')

    for value in (hi, lo):
        label = "0" if value == 0 else money(value)
        parts.append(f'<text class="tick" x="{pad_l - 6}" y="{py(value) + 3.5:.1f}" '
                     f'text-anchor="end">{esc(label)}</text>')
    left = ends[0] if ends else ("第 1 筆", "trade 1")
    right = ends[1] if ends else (f"第 {len(xs)} 筆", f"trade {len(xs)}")
    parts.append(svg_t(*left, x=pad_l, y=height - 5))
    parts.append(svg_t(*right, x=width - pad_r, y=height - 5, text_anchor="end"))
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')


def bar_chart(labels, values, *, width=380, height=175, show_values=None) -> str:
    """Signed bars: blue above the line, red below.

    ``show_values`` prints each bar's number above or below it. That is the
    right default for a handful of bars and wrong past about six, where the
    numbers are wider than their slots and overlap into an unreadable row --
    so it defaults to printing them only while they fit. A chart with the
    labels off always has the table underneath carrying the same numbers.
    """
    if show_values is None:
        show_values = len(values) <= 6
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
            # A 4px radius on a 9px bar is a lozenge, not a bar.
            f'width="{bar_w:.1f}" height="{max(tall, 1):.1f}" '
            f'rx="{min(4, max(tall, 1) / 3):.1f}">'
            f'<title>{esc(label)}: {esc(money(value))}</title></rect>')
        if show_values:
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
.divider{stroke:var(--ink-3);stroke-width:1.5;stroke-dasharray:3 3;opacity:.7}
.divider-label{fill:var(--ink-3);font-size:10px;font-weight:600}
.tick{fill:var(--ink-3);font-size:11px}
.bar-value{fill:var(--ink-2);font-size:11px;font-weight:600}
.bar.pos{fill:var(--pos)}.bar.neg{fill:var(--neg)}
.caveats li{margin:7px 0;color:var(--ink-2)}
/* Chinese is what renders with no JavaScript: the toggle only adds a class,
   so a script that never runs leaves a readable page rather than a blank one. */
.en{display:none}
html.lang-en .zh{display:none}
html.lang-en .en{display:inline}
text.en{display:none}
html.lang-en text.zh{display:none}
html.lang-en text.en{display:inline}
.langbar{display:flex;justify-content:flex-end;margin-bottom:10px}
.langbar button{font:inherit;font-size:13px;padding:5px 13px;cursor:pointer;
background:var(--card);color:var(--ink-2);border:1px solid var(--rule);
border-radius:999px}
.langbar button:hover{color:var(--ink)}
.muted{color:var(--ink-3);font-weight:400}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--rule);
color:var(--ink-3);font-size:12.5px}
/* Printing. These rules live here rather than in scripts/report_pdf.py so that
   Ctrl+P in a browser and the generated PDF are the same page. A second set of
   print rules that only the script knew about would drift the first time
   somebody printed it themselves. */
@media print{
/* The dark palette is a screen decision. Printed, it is a page of ink, and
   forcing light here also stops prefers-color-scheme from deciding what the
   PDF looks like based on whatever machine rendered it. */
:root{--surface:#fff;--card:#fff;--ink:#0b0b0b;--ink-2:#44433f;--ink-3:#63625b;
--rule:#d6d5cf;--pos:#1e63b8;--neg:#c93c3b;--crit:#b83232;--good:#0a840a;}
/* Without this the browser drops every background: the pills lose their fill,
   the drawdown area disappears, and the quarter bars print as outlines. */
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#fff;font-size:11pt}
/* A button nobody can press. */
.langbar{display:none}
.wrap{max-width:none;padding-block:0;padding-left:0;padding-right:0}
/* overflow-x:auto is how a wide table survives a phone. On paper there is
   nothing to scroll, so the same rule silently cuts the right-hand columns
   off -- and the cost table is seven columns wide. */
.scroll{overflow:visible}
table{font-size:9.5pt}
th,td{padding:6px 7px}
.card,.tile,.verdict,svg,table,.caveats li{break-inside:avoid}
/* A heading and the sentence under it are one unit with the chart or table
   they introduce: break-after on the heading alone still leaves the heading
   and its lead paragraph stranded at the foot of a page with the thing they
   describe overleaf. Only lead paragraphs match h2+p, so this does not glue
   body text together. */
h1,h2,h3,h2+p{break-after:avoid}
h2{margin-top:24px}
/* 460px leaves the drawdown chart just too tall to follow its own heading
   onto the first page, which costs a whole sheet of paper; 420 fits and is
   still wider than the chart is on a phone. */
svg{max-width:420px}
footer{margin-top:28px}
}
"""


def tile(key: str, value: str, note: str = "", cls: str = "") -> str:
    """Key and note are already-escaped HTML -- they carry the bilingual spans,
    so escaping them here would print the markup instead of rendering it."""
    note_html = f'<div class="n">{note}</div>' if note else ""
    return (f'<div class="tile"><div class="k">{key}</div>'
            f'<div class="v {cls}">{value}</div>{note_html}</div>')




def page(title: str, parts: list[str], footer: str) -> str:
    """The shell every report shares: title, styles, language toggle, footer.

    ``parts`` is the body. ``footer`` is already-bilingual markup.

    The toggle only ever ADDS a class, and the stylesheet hides ``.en`` by
    default, so a page whose script never runs is a readable Chinese page
    rather than a blank one or a doubled one.
    """
    return "\n".join([
        f"<title>{esc(title)}</title>",
        f"<style>{STYLE}</style>",
        '<div class="wrap">',
        '<div class="langbar"><button type="button" id="lang">English</button></div>',
        *parts,
        f"<footer>{footer}</footer>",
        "</div>",
        "<script>(function(){var b=document.getElementById('lang'),r="
        "document.documentElement;b.addEventListener('click',function(){"
        "var en=r.classList.toggle('lang-en');b.textContent=en?'\u4e2d\u6587':'English';"
        "});})();</script>",
    ])
