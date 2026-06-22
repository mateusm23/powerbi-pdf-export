import io
import re
import time

import img2pdf
from playwright.sync_api import sync_playwright

FIRST_PAGE_SETTLE_MS = 12_000
NEXT_PAGE_SETTLE_MS = 10_000
PAGE_COUNTER_RE = re.compile(r"(\d+)\s*of\s*(\d+)", re.IGNORECASE)


def _read_page_counter(page) -> tuple[int, int]:
    text = page.inner_text("body")
    match = PAGE_COUNTER_RE.search(text)
    if not match:
        raise RuntimeError("Could not find 'Page X of Y' counter on the report")
    return int(match.group(1)), int(match.group(2))


def export_to_pdf(report_url: str) -> bytes:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"]
        )
        try:
            page = browser.new_page(
                viewport={"width": 1600, "height": 900}, device_scale_factor=2
            )
            page.goto(report_url, wait_until="networkidle", timeout=60_000)
            page.wait_for_selector(
                "button[aria-label='Next Page'], button[aria-label='Previous Page']",
                timeout=45_000,
            )
            page.wait_for_timeout(FIRST_PAGE_SETTLE_MS)

            _, total_pages = _read_page_counter(page)

            images: list[bytes] = []
            next_btn = page.locator("button[aria-label='Next Page']")
            for i in range(1, total_pages + 1):
                images.append(page.screenshot(full_page=False))
                if i < total_pages:
                    next_btn.click()
                    page.wait_for_timeout(NEXT_PAGE_SETTLE_MS)

            return img2pdf.convert(images)
        finally:
            browser.close()


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
