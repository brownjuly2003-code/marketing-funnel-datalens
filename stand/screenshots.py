"""Take screenshots of the objects created by datalens_stand.py with headless Chromium.

Page routes come from datalens-ui 0.3831.0 src/server/modes/opensource/routes.ts.
The class `dash-body-loaded` comes from LOADED_DASH_CLASS in src/shared/constants/dash.ts.
A page counts as loaded when its main document answers with HTTP status below 400.

The dashboard is shot once per attribution model. The model is switched the way a user does it:
a click on the selector, then a click on the value in its list. A URL parameter does not work
here: datalens-ui reads URL parameters only for keys listed in the dashboard settings
`globalParams` (src/ui/units/dash/utils/url.ts), and global parameters would also reach the
widgets that ignore the selector. Found on the stand runs of 19-20.11.2026, as were the
`data-qa` hooks below. Each dashboard shot is checked: the selector shows the expected model and
the model comparison widget still lists all three models.
"""

import argparse
import json
import os
from pathlib import Path
import sys

UI = os.environ.get("DATALENS_UI", "http://127.0.0.1:8080")
MODELS = ("last_click", "linear", "time_decay")
SELECT = '[data-qa="chartkit-control-select"]'
SELECT_ITEMS = '[data-qa="chartkit-control-select-items"]'
COMPARE_TITLE = "Сравнение моделей"


def page_problem(status):
    """Why the main document of a page did not load, or None for HTTP status below 400."""
    if status is None:
        return "no HTTP response"
    if status >= 400:
        return f"HTTP {status}"
    return None


def dashboard_problem(expected, selected, compare_text):
    """Why a dashboard shot does not show model `expected`, or None.

    `selected` is the text of the selector, `compare_text` the text of the model comparison
    widget, which ignores the selector and must keep the rows of every model.
    """
    if selected.strip() != expected:
        return f"selector shows {selected.strip()!r}, expected {expected!r}"
    lines = {line.strip() for line in compare_text.splitlines()}
    missing = [model for model in MODELS if model not in lines]
    if missing:
        return f"model comparison lacks {', '.join(missing)}"
    return None


def pages(objects):
    """Static pages: (screenshot name, path). The dashboard is handled by shoot_dashboard."""
    result = [("00_workbook", f"/workbooks/{objects['workbook']}")]
    if "connection" in objects:
        result.append(("01_connection", f"/connections/{objects['connection']}"))
    for name, dataset_id in objects.get("datasets", {}).items():
        result.append((f"02_dataset_{name}", f"/datasets/{dataset_id}"))
    for name, chart_id in objects.get("charts", {}).items():
        result.append((f"03_chart_{name}", f"/wizard/{chart_id}"))
    return result


def dashboard_shots():
    """(screenshot name, model): the default model first, then the two switched by a click."""
    return [("04_dashboard_last_click", "last_click")] + [
        (f"05_dashboard_{model}", model) for model in MODELS[1:]
    ]


def open_page(page, path):
    response = page.goto(UI + path, wait_until="networkidle", timeout=120_000)
    return page_problem(None if response is None else response.status)


def shoot_dashboard(page, dash, model):
    """Open the dashboard, pick `model` in the selector unless it is the default; problem or None."""
    problem = open_page(page, f"/{dash}")
    page.wait_for_selector(".dash-body-loaded", timeout=120_000)
    page.wait_for_timeout(5_000)
    if model != MODELS[0]:
        page.locator(SELECT).first.click(timeout=30_000)
        page.locator(f'{SELECT_ITEMS} [data-value="{model}"]').first.click(timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=120_000)
        page.wait_for_timeout(5_000)
    selected = page.locator(SELECT).first.inner_text()
    compare = page.locator(".react-grid-item").filter(has_text=COMPARE_TITLE).first.inner_text()
    return problem or dashboard_problem(model, selected, compare)


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
    # Imported here so that tests can check the pure functions without Playwright installed.
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    failed = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="ru-RU")
        jobs = [(name, lambda path=path: open_page(page, path)) for name, path in pages(objects)]
        if "dashboard" in objects:
            jobs += [
                (name, lambda model=model: shoot_dashboard(page, objects["dashboard"], model))
                for name, model in dashboard_shots()
            ]
        for name, job in jobs:
            try:
                problem = job()
                page.wait_for_timeout(5_000)
            except PlaywrightError as exc:
                problem = str(exc).splitlines()[0]
            if problem:
                failed.append(f"{name}: {problem}")
            page.screenshot(path=str(target / f"{name}.png"), full_page=True)
            print(f"screenshot {name}: {page.url}", flush=True)
        browser.close()
    for line in failed:
        print(f"page failed: {line}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
