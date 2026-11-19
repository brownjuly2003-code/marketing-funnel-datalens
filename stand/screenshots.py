"""Take screenshots of the objects created by datalens_stand.py with headless Chromium.

Blind script: written without seeing the DataLens UI. It only opens pages and waits; it clicks
nothing. Page routes come from datalens-ui 0.3831.0 src/server/modes/opensource/routes.ts.
The class `dash-body-loaded` comes from LOADED_DASH_CLASS in src/shared/constants/dash.ts.
The URL parameter that switches the selector (field guid = value) is NOT verified.
A page counts as loaded when its main document answers with HTTP status below 400; only the
dashboard has a known readiness class, other pages are not checked for content.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

UI = os.environ.get("DATALENS_UI", "http://127.0.0.1:8080")


def page_problem(status):
    """Why the main document of a page did not load, or None for HTTP status below 400."""
    if status is None:
        return "no HTTP response"
    if status >= 400:
        return f"HTTP {status}"
    return None


def pages(objects):
    dash = objects.get("dashboard")
    model = objects.get("model_field_guid")
    result = [("00_workbook", f"/workbooks/{objects['workbook']}", None)]
    if "connection" in objects:
        result.append(("01_connection", f"/connections/{objects['connection']}", None))
    for name, dataset_id in objects.get("datasets", {}).items():
        result.append((f"02_dataset_{name}", f"/datasets/{dataset_id}", None))
    for name, chart_id in objects.get("charts", {}).items():
        result.append((f"03_chart_{name}", f"/wizard/{chart_id}", None))
    if dash:
        result.append(("04_dashboard_last_click", f"/{dash}", ".dash-body-loaded"))
        if model:
            for value in ("linear", "time_decay"):
                url = f"/{dash}?{quote(model)}={value}"
                result.append((f"05_dashboard_{value}", url, ".dash-body-loaded"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    objects = json.loads((args.out / "objects.json").read_text(encoding="utf-8"))
    if "workbook" not in objects:
        print("no workbook was created; nothing to screenshot", file=sys.stderr)
        return 1
    target = args.out / "screenshots"
    target.mkdir(parents=True, exist_ok=True)
    # Imported here so that tests can check page_problem without Playwright installed.
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    failed = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="ru-RU")
        for name, path, ready in pages(objects):
            try:
                response = page.goto(UI + path, wait_until="networkidle", timeout=120_000)
                problem = page_problem(None if response is None else response.status)
                if problem:
                    failed.append(f"{name}: {problem}")
                if ready:
                    page.wait_for_selector(ready, timeout=120_000)
                page.wait_for_timeout(5_000)
            except PlaywrightError as exc:
                failed.append(f"{name}: {str(exc).splitlines()[0]}")
            page.screenshot(path=str(target / f"{name}.png"), full_page=True)
            print(f"screenshot {name}: {UI + path}", flush=True)
        browser.close()
    for line in failed:
        print(f"page failed: {line}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
