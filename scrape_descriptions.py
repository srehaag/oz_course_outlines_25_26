"""
Scrape Osgoode JD Course & Seminar descriptions from MyOsgoode.
Requires: pip install playwright && playwright install chromium

Usage: python scrape_osgoode.py

The script will open a browser window. Log in with your credentials + MFA,
then press Enter in the terminal to continue scraping.

* Code generated with the assistance of Claude Opus 4.6
"""

import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

BASE_URL = "https://lwdomapp3.osgoode.yorku.ca/myosgoode.nsf"
COURSE_TABLE_URL = f"{BASE_URL}/jdcourseseminars.xsp"

# Button IDs for the 4 tabs
TAB_BUTTONS = {
    "Fall Courses":   "view:_id1:_id2:_id53:button1",
    "Fall Seminars":  "view:_id1:_id2:_id53:button2",
    "Winter Courses": "view:_id1:_id2:_id53:button3",
    "Winter Seminars": "view:_id1:_id2:_id53:button4",
}


async def wait_for_tab_load(page):
    """Wait for the AJAX partial refresh to complete after clicking a tab."""
    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(1000)


async def scrape_links_from_table(page):
    """Extract all course/seminar links and table metadata from the current view."""
    rows = await page.query_selector_all("table.slyTable tbody tr")
    entries = []

    for row in rows:
        link_el = await row.query_selector("td a[href*='syldescription.xsp']")
        if not link_el:
            continue

        cells = await row.query_selector_all("td")
        if len(cells) < 12:
            continue

        title = (await link_el.inner_text()).strip()
        href = await link_el.get_attribute("href")

        cell_texts = []
        for cell in cells:
            cell_texts.append((await cell.inner_text()).strip())

        entries.append({
            "title": title,
            "href": href,
            "instructor": cell_texts[1] if len(cell_texts) > 1 else "",
            "section": cell_texts[2] if len(cell_texts) > 2 else "",
            "hours": cell_texts[3] if len(cell_texts) > 3 else "",
            "catalogue": cell_texts[4] if len(cell_texts) > 4 else "",
            "number": cell_texts[5] if len(cell_texts) > 5 else "",
            "credits": cell_texts[6] if len(cell_texts) > 6 else "",
            "initial_demand": cell_texts[7] if len(cell_texts) > 7 else "",
            "max": cell_texts[8] if len(cell_texts) > 8 else "",
            "final": cell_texts[9] if len(cell_texts) > 9 else "",
            "writing_requirement": cell_texts[10] if len(cell_texts) > 10 else "",
            "praxicum": cell_texts[11] if len(cell_texts) > 11 else "",
        })

    return entries


async def scrape_description_page(page, href):
    """Navigate to a course description page and extract structured data."""
    url = f"{BASE_URL}/{href}" if not href.startswith("http") else href
    data = {}

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(500)

        # --- Main content area ---
        # ID: view:_id1:_id2:_id53:computedField2
        main_html = await page.evaluate("""
            () => {
                const el = document.getElementById('view:_id1:_id2:_id53:computedField2');
                return el ? el.innerHTML : null;
            }
        """)

        if main_html:
            # Extract description (between "Description:" bold tag and next section)
            desc_match = re.search(
                r'<strong>Description:\s*</strong>(.*?)(?=<p><strong>|</p>\s*<p><strong>)',
                main_html, re.DOTALL
            )
            if desc_match:
                data["description"] = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()
            else:
                # Broader fallback
                desc_match = re.search(
                    r'<strong>Description:\s*</strong>(.*?)(?=<strong>Evaluation)',
                    main_html, re.DOTALL
                )
                if desc_match:
                    data["description"] = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()

            # Extract evaluation
            eval_match = re.search(
                r'<strong>Evaluation:\s*</strong>(.*?)(?=</p>|</span>)',
                main_html, re.DOTALL
            )
            if eval_match:
                data["evaluation"] = re.sub(r'<[^>]+>', '', eval_match.group(1)).strip()

            # Full text as fallback
            main_text = await page.evaluate("""
                () => {
                    const el = document.getElementById('view:_id1:_id2:_id53:computedField2');
                    return el ? el.innerText : null;
                }
            """)
            data["full_page_text"] = main_text.strip() if main_text else ""

        # --- Sidebar metadata ---
        # ID: view:_id1:_id2:_id56:computedField2
        sidebar_html = await page.evaluate("""
            () => {
                const el = document.getElementById('view:_id1:_id2:_id56:computedField2');
                return el ? el.innerHTML : null;
            }
        """)

        if sidebar_html:
            def extract_bold(html, label):
                """Extract the <b>value</b> after a label."""
                pattern = rf'{label}:\s*<b>(.*?)</b>'
                m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
                return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ""

            data["term"] = ""
            term_match = re.search(r'<b>(Fall|Winter|Year)</b>', sidebar_html)
            if term_match:
                data["term"] = term_match.group(1)

            data["sidebar_credits"] = extract_bold(sidebar_html, "Credits")
            data["sidebar_hours"] = extract_bold(sidebar_html, "Hours")
            data["max_enrollment"] = extract_bold(sidebar_html, "Max\\.? Enrollment")
            data["prerequisite_courses"] = extract_bold(sidebar_html, "Prerequisite Courses")
            data["preferred_courses"] = extract_bold(sidebar_html, "Preferred Courses")
            data["presentation"] = extract_bold(sidebar_html, "Presentation")
            data["upper_year_writing"] = extract_bold(sidebar_html, r"Upper Year Research.*?Writing Requirement")
            data["praxicum_detail"] = extract_bold(sidebar_html, "Praxicum")

    except Exception as e:
        print(f"  Error fetching {href}: {e}")
        data["error"] = str(e)

    return data


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to the course table page (will redirect to login if needed)
        await page.goto(COURSE_TABLE_URL)

        print("=" * 60)
        print("Please log in with your credentials and complete MFA.")
        print("Once you can see the course table, press ENTER here.")
        print("=" * 60)
        input()

        # Make sure we're on the right page
        await page.goto(COURSE_TABLE_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        all_courses = {}

        for tab_name, button_id in TAB_BUTTONS.items():
            print(f"\n--- Scraping: {tab_name} ---")

            # Click the tab button via JS (avoids CSS escaping issues with colons)
            await page.evaluate(f'document.getElementById("{button_id}").click()')
            await wait_for_tab_load(page)

            # Scrape the table
            entries = await scrape_links_from_table(page)
            print(f"  Found {len(entries)} entries")

            # Visit each link to get the full description
            for i, entry in enumerate(entries):
                print(f"  [{i+1}/{len(entries)}] {entry['title']} ({entry['section']})")
                detail = await scrape_description_page(page, entry["href"])
                entry.update(detail)

                # Navigate back and re-click the tab
                await page.goto(COURSE_TABLE_URL, wait_until="networkidle")
                await page.wait_for_timeout(500)
                await page.evaluate(f'document.getElementById("{button_id}").click()')
                await wait_for_tab_load(page)

            all_courses[tab_name] = entries

        # Save to JSON
        output_path = Path("DATA/osgoode_course_descriptions.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_courses, f, indent=2, ensure_ascii=False)

        total = sum(len(v) for v in all_courses.values())
        print(f"\n✓ Saved {total} entries to {output_path}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
