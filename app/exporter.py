import os
import re
import tempfile
import time

import img2pdf
from playwright.sync_api import sync_playwright

FIRST_PAGE_SETTLE_MS = 12_000
VISUAL_SETTLE_MS = 8_000
COUNTER_POLL_TIMEOUT_S = 10
COUNTER_POLL_INTERVAL_MS = 400
MAX_ADVANCE_ATTEMPTS = 3
CHUNK_SIZE = 6
PAGE_COUNTER_RE = re.compile(r"(\d+)\s*of\s*(\d+)", re.IGNORECASE)

CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=site-per-process",
    "--js-flags=--max-old-space-size=256",
]


def _read_page_counter(page) -> tuple[int, int]:
    text = page.inner_text("body")
    match = PAGE_COUNTER_RE.search(text)
    if not match:
        raise RuntimeError("Could not find 'Page X of Y' counter on the report")
    return int(match.group(1)), int(match.group(2))


def _wait_until_on_page(page, target: int) -> bool:
    """Polls the 'X of Y' counter until it reports `target`, or times out."""
    deadline = time.time() + COUNTER_POLL_TIMEOUT_S
    while time.time() < deadline:
        try:
            current, _ = _read_page_counter(page)
        except RuntimeError:
            current = None
        if current == target:
            return True
        page.wait_for_timeout(COUNTER_POLL_INTERVAL_MS)
    return False


def _advance_to_page(page, next_btn, target: int) -> None:
    """Clicks Next Page until the counter confirms we're on `target`.

    The Power BI report is a single-page app, so "Next Page" swaps content
    in place rather than navigating — occasionally a click is silently
    dropped if it fires before the previous transition settles. Verifying
    the on-screen counter (instead of trusting a fixed wait) catches that
    and retries, rather than silently capturing the wrong page.
    """
    for attempt in range(MAX_ADVANCE_ATTEMPTS):
        next_btn.click()
        if _wait_until_on_page(page, target):
            return
    raise RuntimeError(
        f"Não foi possível confirmar a navegação até a página {target} "
        f"após {MAX_ADVANCE_ATTEMPTS} tentativas"
    )


def _open_report(p, report_url: str):
    """Launches a fresh browser+page and loads the report from page 1."""
    browser = p.chromium.launch(args=CHROMIUM_ARGS)
    page = browser.new_page(
        viewport={"width": 1280, "height": 720}, device_scale_factor=1.25
    )
    page.goto(report_url, wait_until="networkidle", timeout=60_000)
    page.wait_for_selector(
        "button[aria-label='Next Page'], button[aria-label='Previous Page']",
        timeout=45_000,
    )
    page.wait_for_timeout(FIRST_PAGE_SETTLE_MS)
    return browser, page


def export_to_pdf(report_url: str) -> bytes:
    """Renders every page of the report to a JPEG and assembles a PDF.

    The Power BI report is a single-page app: clicking "Next Page" swaps the
    visual content without a real navigation/reload, and Chromium's memory
    footprint grows with every swap until it eventually exceeds the 512MB
    free-tier limit. To stay bounded, the browser is closed and relaunched
    every CHUNK_SIZE pages, re-navigating to the report and "skipping" via
    Next Page clicks back to the resume point.
    """
    image_paths: list[str] = []
    tmpdir = tempfile.mkdtemp(prefix="pbi-export-")
    total_pages = None

    try:
        with sync_playwright() as p:
            chunk_start = 1
            while total_pages is None or chunk_start <= total_pages:
                browser, page = _open_report(p, report_url)
                try:
                    if total_pages is None:
                        _, total_pages = _read_page_counter(page)

                    next_btn = page.locator("button[aria-label='Next Page']")

                    for target in range(2, chunk_start + 1):
                        _advance_to_page(page, next_btn, target)
                    if chunk_start > 1:
                        page.wait_for_timeout(VISUAL_SETTLE_MS)

                    chunk_end = min(chunk_start + CHUNK_SIZE - 1, total_pages)
                    for i in range(chunk_start, chunk_end + 1):
                        path = os.path.join(tmpdir, f"page-{i:02d}.jpeg")
                        page.screenshot(
                            path=path, type="jpeg", quality=85, full_page=False
                        )
                        image_paths.append(path)
                        if i < chunk_end:
                            _advance_to_page(page, next_btn, i + 1)
                            page.wait_for_timeout(VISUAL_SETTLE_MS)
                finally:
                    browser.close()

                chunk_start = chunk_end + 1

        return img2pdf.convert(image_paths)
    finally:
        for path in image_paths:
            os.remove(path)
        os.rmdir(tmpdir)


if __name__ == "__main__":
    import sys

    url = sys.argv[1]
    start = time.time()
    pdf_bytes = export_to_pdf(url)
    elapsed = time.time() - start
    out_path = "_test_export.pdf"
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"OK: wrote {out_path} ({len(pdf_bytes)} bytes) in {elapsed:.1f}s")
