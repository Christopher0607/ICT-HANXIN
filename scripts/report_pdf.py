"""Print the quarterly report to a PDF.

    uv run python scripts/report_pdf.py

Reads ``docs/quarterly_report.html`` and writes ``docs/quarterly_report.pdf``.

This script owns none of the layout. Every print rule lives in the ``@media
print`` block of ``STYLE`` in ``scripts/quarterly_report.py``, so pressing
Ctrl+P on the page in a browser produces the same document this does -- with
better Chinese fonts, because it uses the ones on your own machine rather than
whatever the rendering box happens to have installed. All that happens here is
opening the page, picking a language, and asking Chromium to print it.

The report is bilingual with Chinese as the default: the toggle only adds a
``lang-en`` class to ``<html>``. ``--lang en`` adds that class before printing
rather than rendering a second page, so there is one report and two views of
it, not two reports to keep in step.
"""

from __future__ import annotations

import argparse
import glob
import html as html_mod
import re
import os
import pathlib
import shutil
import sys

HINT = ("This needs Playwright and a Chromium build:\n"
        "    uv sync --extra pdf && uv run playwright install chromium")


def find_chromium() -> str | None:
    """A Chromium on this machine that Playwright did not download itself.

    Playwright pins one browser revision per release and refuses to start
    anything else by default. That is the right behaviour for a test suite,
    where a browser that is not the pinned one is a difference nobody asked
    for; here the page is static HTML and any recent Chromium prints it the
    same way. Boxes that ship a pre-installed browser (this project's remote
    environment does) hold a revision the pinned one does not match, and
    downloading a second copy to print one page is not worth it.
    """
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    patterns = ([f"{root}/chromium-*/chrome-linux/chrome",
                 f"{root}/chromium_headless_shell-*/chrome-headless-shell-linux64/"
                 "chrome-headless-shell"] if root else [])
    for pattern in patterns:
        # Highest revision first: the directories sort as chromium-1194 and
        # chromium-983, so compare the number rather than the string.
        found = sorted(glob.glob(pattern),
                       key=lambda q: int("".join(c for c in pathlib.Path(q).parts[-3]
                                                 if c.isdigit()) or 0), reverse=True)
        if found:
            return found[0]
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        which = shutil.which(name)
        if which:
            return which
    return None


def page_title(html: pathlib.Path) -> str:
    """The page's own <title>, for the running footer.

    Hard-coding one report's name put "LTF Sweep" at the foot of every other
    report this script printed.
    """
    head = html.read_text(encoding="utf-8", errors="replace")[:8192]
    found = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
    return html_mod.unescape(found[1]).strip() if found else html.stem


def render(html: pathlib.Path, out: pathlib.Path, lang: str,
           executable: str | None = None) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(f"playwright is not installed.\n{HINT}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            fallback = None if executable else find_chromium()
            if fallback is None:
                raise SystemExit(f"could not start Chromium: {exc}\n{HINT}")
            print(f"pinned browser missing; using {fallback}", file=sys.stderr)
            browser = p.chromium.launch(executable_path=fallback)
        try:
            # print() below already forces print media, but the colour scheme is
            # a separate axis: without this the rendering box's own dark-mode
            # preference decides what the PDF looks like.
            page = browser.new_page(color_scheme="light")
            page.goto(html.resolve().as_uri())
            if lang == "en":
                page.evaluate("document.documentElement.classList.add('lang-en')")
            # Web fonts are inline and there is no network, but the SVG text
            # still needs a layout pass before the page is measured for pages.
            page.wait_for_timeout(300)
            page.emulate_media(media="print", color_scheme="light")
            page.pdf(
                path=str(out),
                format="A4",
                print_background=True,
                # Header and footer are drawn inside the margin box. Leaving the
                # default margins with display_header_footer on prints the page
                # number on top of the content, or clips it away entirely.
                margin={"top": "16mm", "bottom": "16mm",
                        "left": "12mm", "right": "12mm"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#63625b;'
                    'padding:0 12mm;display:flex;justify-content:space-between">'
                    f'<span>{html_mod.escape(page_title(html))}</span>'
                    '<span class="pageNumber"></span></div>'),
            )
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--html", default="docs/quarterly_report.html",
                    help="the page to print (default: %(default)s)")
    ap.add_argument("--out", default="docs/quarterly_report.pdf",
                    help="where to write the PDF (default: %(default)s)")
    ap.add_argument("--lang", choices=("zh", "en"), default="zh",
                    help="which of the two views to print (default: %(default)s)")
    ap.add_argument("--chromium", default=None,
                    help="path to a Chromium binary, if Playwright's own is "
                         "not the one you want used")
    args = ap.parse_args(argv)

    html = pathlib.Path(args.html)
    if not html.exists():
        raise SystemExit(f"{html} does not exist. "
                         "Run scripts/quarterly_report.py first.")

    out = pathlib.Path(args.out)
    render(html, out, args.lang, args.chromium)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {args.lang})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
