"""
Scrape Osgoode JD Course Outline PDFs from the outlines2526 Domino app.

Requires: pip install playwright && playwright install chromium
Usage:    python scrape_osgoode_outlines.py

The script will open a browser window. Log in with your Passport York
credentials, then press Enter in the terminal to continue scraping.

PDFs are saved to:
  DATA/F25/  – Fall 2025 outlines
  DATA/W26/  – Winter 2026 outlines

* Code generated with the assistance of Claude Opus 4.6
"""

import asyncio
import json
import re
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = "https://lwdomapp1.osgoode.yorku.ca"
DB_PATH = "/outlines2526.nsf"
START_URL = f"{BASE_URL}{DB_PATH}/CourseViewTemplate?OpenForm"

# _doClick values extracted from the page HTML for Fall / Winter tabs
FALL_CLICK = "85258CED005631DD.ff8cb09e5aa2b10a85256a1d005a7d4f/$Body/0.CDE"
WINTER_CLICK = "85258CED005631DD.ff8cb09e5aa2b10a85256a1d005a7d4f/$Body/0.E84"

TABS = {
    "Fall":   {"click_val": FALL_CLICK,  "folder": "DATA/F25"},
    "Winter": {"click_val": WINTER_CLICK, "folder": "DATA/W26"},
}


def sanitize_filename(name: str, max_len: int = 200) -> str:
    """Make a string safe for use as a filename."""
    # Replace characters illegal on Windows/macOS/Linux
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


async def click_tab(page, click_val):
    """Submit the Domino form with a specific __Click value to switch tabs."""
    await page.evaluate(f"""
        () => {{
            const form = document._CourseViewTemplate;
            form.__Click.value = "{click_val}";
            form.submit();
        }}
    """)
    await page.wait_for_load_state("networkidle", timeout=30000)
    await page.wait_for_timeout(1000)


async def scrape_course_links(page) -> list[dict]:
    """
    Extract course links and metadata from the current table view.
    The table uses <a href='/outlines2526.nsf/Courses/...'> links inside
    a <table class="coursetable">.
    """
    entries = []
    rows = await page.query_selector_all("table.coursetable tr")

    for row in rows:
        link_el = await row.query_selector("a[href*='/outlines2526.nsf/Courses/']")
        if not link_el:
            continue

        title = (await link_el.inner_text()).strip()
        href = await link_el.get_attribute("href")

        cells = await row.query_selector_all("td")
        cell_texts = []
        for c in cells:
            cell_texts.append((await c.inner_text()).strip())

        entries.append({
            "title": title,
            "href": href,
            "course_number": cell_texts[1] if len(cell_texts) > 1 else "",
            "section": cell_texts[2] if len(cell_texts) > 2 else "",
            "title_variance": cell_texts[3] if len(cell_texts) > 3 else "",
            "term": cell_texts[4] if len(cell_texts) > 4 else "",
            "professor": cell_texts[5] if len(cell_texts) > 5 else "",
        })

    return entries


async def download_pdf_from_course_page(page, context, href: str, dest_dir: Path, entry: dict) -> list[str]:
    """
    Navigate to a course outline page, find the PDF attachment link(s) via
    the Domino $File URL pattern, and download them using the browser
    context's authenticated request API.

    Returns a list of saved file paths.
    """
    url = href if href.startswith("http") else f"{BASE_URL}{href}"
    saved = []

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(500)

        # Extract all unique $File PDF links from the page
        pdf_links = await page.evaluate("""
            () => {
                const seen = new Set();
                const results = [];
                for (const a of document.querySelectorAll('a[href*="$File"]')) {
                    const href = a.getAttribute('href');
                    if (href && !seen.has(href)) {
                        seen.add(href);
                        // Use the link text as the suggested filename
                        results.push({ href, name: a.textContent.trim() });
                    }
                }
                return results;
            }
        """)

        if not pdf_links:
            return saved

        for link_info in pdf_links:
            pdf_href = link_info["href"]
            suggested_name = link_info["name"]

            # Build full URL
            pdf_url = pdf_href if pdf_href.startswith("http") else f"{BASE_URL}{pdf_href}"

            # Download via the authenticated browser context
            resp = await context.request.get(pdf_url)
            if not resp.ok:
                print(f"    ✗ HTTP {resp.status} for {suggested_name}")
                continue

            body = await resp.body()

            # Use the server's filename if it looks like a PDF name,
            # otherwise build one from metadata
            if suggested_name and (suggested_name.lower().endswith(".pdf")
                                   or suggested_name.lower().endswith(".docx")
                                   or suggested_name.lower().endswith(".doc")):
                filename = sanitize_filename(suggested_name)
            else:
                filename = sanitize_filename(entry["title"]) + ".pdf"

            dest = dest_dir / filename
            dest.write_bytes(body)
            saved.append(str(dest))

    except Exception as e:
        print(f"    ✗ Error: {e}")

    return saved


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # Navigate to the start page (will redirect to Passport York login)
        await page.goto(START_URL)

        print("=" * 60)
        print("Please log in with your Passport York credentials.")
        print("Once you can see the course list, press ENTER here.")
        print("=" * 60)
        input()

        # Make sure we're on the right page after login
        await page.goto(START_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        all_results = {}

        for tab_name, cfg in TABS.items():
            dest_dir = Path(cfg["folder"])
            dest_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n{'='*60}")
            print(f"  Scraping: {tab_name} courses/seminars")
            print(f"  Saving to: {dest_dir}")
            print(f"{'='*60}")

            # Click the Fall or Winter link via Domino form submission
            await click_tab(page, cfg["click_val"])

            # Scrape all course links from the table
            entries = await scrape_course_links(page)
            print(f"  Found {len(entries)} entries\n")

            tab_results = []

            for i, entry in enumerate(entries):
                label = f"[{i+1}/{len(entries)}]"
                print(f"  {label} {entry['title']} "
                      f"(#{entry['course_number']} §{entry['section']}) "
                      f"— {entry['professor']}")

                # Navigate to course page and download PDF(s)
                saved = await download_pdf_from_course_page(
                    page, context, entry["href"], dest_dir, entry
                )

                if saved:
                    for s in saved:
                        print(f"    ✓ {Path(s).name}")
                    entry["pdf_paths"] = saved
                else:
                    print(f"    ⚠ No PDF found")
                    entry["pdf_paths"] = []

                tab_results.append(entry)

                # Navigate back to the listing and re-select the tab
                await page.goto(START_URL, wait_until="networkidle")
                await page.wait_for_timeout(500)
                await click_tab(page, cfg["click_val"])

            all_results[tab_name] = tab_results

        # Save a manifest JSON
        manifest_path = Path("DATA/osgoode_outlines_manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)

        total = sum(len(v) for v in all_results.values())
        downloaded = sum(
            1 for v in all_results.values()
            for e in v if e.get("pdf_paths")
        )
        print(f"\n{'='*60}")
        print(f"  ✓ {downloaded}/{total} courses had PDFs downloaded")
        print(f"  Manifest: {manifest_path}")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())