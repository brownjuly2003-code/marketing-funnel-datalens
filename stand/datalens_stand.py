"""Create the marketing funnel objects in DataLens OSS and compare card numbers with the reference.

Written from the DataLens OSS v2.9.0 sources (datalens-ui 0.3831.0, datalens-backend 0.2457.0).
Every request path below is quoted in docs/runs/2026-11-19-stand-prep.md with its source file;
the request bodies were reconstructed from schemas and tests and verified by the stand run of
20.11.2026 (docs/runs/2026-11-20-stand.md).

Exit codes: 0 numbers match, 1 numbers differ, 2 an API call or a chart run failed.
Standard library only: the stand machine needs no extra packages for this step.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from marketing_funnel.stand_check import (  # noqa: E402
    CHANNEL_FIELDS,
    compare,
    expected_channel_cards,
    expected_funnel,
    expected_unattributed_share,
    format_table,
)

UI = os.environ.get("DATALENS_UI", "http://127.0.0.1:8080")
CONTROL_API = os.environ.get("DATALENS_CONTROL_API", "http://127.0.0.1:8010")
DATA_API = os.environ.get("DATALENS_DATA_API", "http://127.0.0.1:8020")

CHANNEL_FORMULAS = {
    "subscriptions": "SUM([conversions])",
    "revenue_rub": "SUM([revenue_cents]) / 100",
    "cost_rub": "SUM([cost_cents]) / 100",
    "spend_rub": "SUM([spend_cents]) / 100",
    "margin_rub": "SUM([margin_cents]) / 100",
    # CPA is spend divided by conversions after filtering; empty when there are no conversions.
    "cpa_rub": "IF(SUM([conversions]) > 0, SUM([spend_cents]) / SUM([conversions]) / 100, NULL)",
    "unattributed_share": (
        "SUM(IF([channel] = 'unattributed', [conversions], 0.0)) / SUM([conversions])"
    ),
}
FUNNEL_FORMULAS = {"users_total": "SUM([users])"}
CARD_TO_REPORT = {"subscriptions": "conversions", **{f: f for f in CHANNEL_FIELDS[1:]}}


class ApiError(RuntimeError):
    pass


class Api:
    """JSON over HTTP with every exchange written to out/api for the run protocol."""

    def __init__(self, out, secrets_to_hide):
        self.dir = out / "api"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.hide = [s for s in secrets_to_hide if s]

    def _redact(self, text):
        for value in self.hide:
            text = text.replace(value, "***")
        return text

    def call(self, name, method, url, body=None, ok=(200,)):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                status, text = response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            status, text = None, repr(exc)
        self.count += 1
        record = {
            "method": method,
            "url": url,
            "status": status,
            "seconds": round(time.monotonic() - started, 2),
            "request": body,
            "response": text[:200000],
        }
        path = self.dir / f"{self.count:02d}_{name}.json"
        path.write_text(
            self._redact(json.dumps(record, ensure_ascii=False, indent=2)), encoding="utf-8"
        )
        if status not in ok:
            raise ApiError(f"{name}: {method} {url} -> {status}; see {path}")
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise ApiError(f"{name}: response is not JSON; see {path}") from exc


def wait_ready(urls, timeout):
    deadline = time.monotonic() + timeout
    pending = list(urls)
    while pending:
        url = pending[0]
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    print(f"ready: {url}", flush=True)
                    pending.pop(0)
                    continue
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        if time.monotonic() > deadline:
            raise ApiError(f"not ready after {timeout}s: {url}")
        time.sleep(5)


def create_dataset(api, name, workbook_id, connection_id, database, table, formulas):
    """Two validation passes (source, then formulas) and a save, as dsmaker in the backend does."""
    source_id, avatar_id = str(uuid.uuid4()), str(uuid.uuid4())
    first = api.call(
        f"dataset_{table}_source",
        "POST",
        f"{CONTROL_API}/api/v1/datasets/validators/dataset",
        {
            "updates": [
                {
                    "action": "add_source",
                    "source": {
                        "id": source_id,
                        "connection_id": connection_id,
                        "source_type": "CH_TABLE",
                        "title": table,
                        "parameters": {"db_name": database, "table_name": table},
                    },
                },
                {
                    "action": "add_source_avatar",
                    "source_avatar": {
                        "id": avatar_id,
                        "source_id": source_id,
                        "title": table,
                        "is_root": True,
                    },
                },
            ]
        },
    )
    raw = first["dataset"]["result_schema"]
    if not raw:
        raise ApiError(f"dataset {table}: no fields after adding the source")
    updates = [
        {"action": "update_field", "field": {"guid": f["guid"], "aggregation": "none"}} for f in raw
    ]
    updates += [
        {
            "action": "add_field",
            "field": {
                "guid": str(uuid.uuid4()),
                "title": title,
                "calc_mode": "formula",
                "formula": formula,
                "aggregation": "none",
            },
        }
        for title, formula in formulas.items()
    ]
    second = api.call(
        f"dataset_{table}_fields",
        "POST",
        f"{CONTROL_API}/api/v1/datasets/validators/dataset",
        {"dataset": first["dataset"], "updates": updates},
    )
    errors = second["dataset"].get("component_errors", {}).get("items") or []
    if errors:
        raise ApiError(f"dataset {table}: component errors {json.dumps(errors)[:2000]}")
    saved = api.call(
        f"dataset_{table}_save",
        "POST",
        f"{CONTROL_API}/api/v1/datasets",
        {
            "dataset": second["dataset"],
            "name": name,
            "workbook_id": workbook_id,
            "preview": False,
        },
    )
    fields = {f["title"]: f for f in second["dataset"]["result_schema"]}
    missing = [title for title in formulas if title not in fields]
    if missing:
        raise ApiError(f"dataset {table}: fields missing after save: {missing}")
    return {"id": saved["id"], "name": name, "fields": fields}


def read_rows(api, name, dataset, columns, filters=(), order=()):
    """POST /api/data/v2/datasets/{id}/result and return rows as {title: value}."""
    fields = dataset["fields"]
    body = {
        "fields": [
            {"ref": {"type": "id", "id": fields[c]["guid"]}, "role_spec": {"role": "row"}}
            for c in columns
        ],
        "filters": [
            {"ref": {"type": "id", "id": fields[c]["guid"]}, "operation": "EQ", "values": [v]}
            for c, v in filters
        ],
        "order_by": [
            {"ref": {"type": "id", "id": fields[c]["guid"]}, "direction": "asc"} for c in order
        ],
    }
    response = api.call(
        name, "POST", f"{DATA_API}/api/data/v2/datasets/{dataset['id']}/result", body
    )
    rows = []
    for block in response["result_data"]:
        for row in block["rows"]:
            rows.append(dict(zip(columns, row["data"])))
    return rows


def _number(value):
    return None if value is None else float(value)


def read_numbers(api, channel_ds, funnel_ds, models):
    cards, shares = {}, {}
    for model in models:
        rows = read_rows(
            api,
            f"read_channels_{model}",
            channel_ds,
            ["channel", *CARD_TO_REPORT],
            filters=[("model", model)],
            order=["channel"],
        )
        for row in rows:
            cards[(model, row["channel"])] = {
                CARD_TO_REPORT[c]: _number(row[c]) for c in CARD_TO_REPORT
            }
        share = read_rows(
            api, f"read_share_{model}", channel_ds, ["unattributed_share"], [("model", model)]
        )
        shares[(model,)] = {"unattributed_share": _number(share[0]["unattributed_share"])}
    funnel = {
        (int(float(row["step_number"])), row["step"]): {"users": _number(row["users_total"])}
        for row in read_rows(
            api,
            "read_funnel",
            funnel_ds,
            ["step_number", "step", "users_total"],
            order=["step_number"],
        )
    }
    return cards, shares, funnel


def wizard_item(dataset, title):
    field = dict(dataset["fields"][title])
    field.update(datasetId=dataset["id"], datasetName=dataset["name"])
    return field


def flat_table(dataset, titles, sort=()):
    """Wizard config in the shape stored by the release demo data (version 4, flatTable)."""
    return {
        "type": "datalens",
        "version": "4",
        "datasetsIds": [dataset["id"]],
        "datasetsPartialFields": [
            [
                {"guid": f["guid"], "title": f["title"], "calc_mode": f["calc_mode"]}
                for f in dataset["fields"].values()
            ]
        ],
        "visualization": {
            "id": "flatTable",
            "type": "table",
            "name": "label_visualization-flat-table",
            "allowFilters": True,
            "allowColors": True,
            "allowSort": True,
            "placeholders": [
                {
                    "allowedTypes": {},
                    "id": "flat-table-columns",
                    "type": "flat-table-columns",
                    "title": "section_columns",
                    "items": [wizard_item(dataset, t) for t in titles],
                    "required": True,
                    "settings": {"groupping": "on"},
                }
            ],
        },
        "colors": [],
        "colorsConfig": {},
        "extraSettings": {
            "titleMode": "hide",
            "title": "",
            "legendMode": "show",
            "pagination": "off",
            "limit": 100,
            "totals": "off",
        },
        "filters": [],
        "geopointsConfig": {},
        "hierarchies": [],
        "labels": [],
        "links": [],
        "segments": [],
        "shapes": [],
        "shapesConfig": {},
        "sort": [dict(wizard_item(dataset, t), direction="ASC") for t in sort],
        "tooltips": [],
        "updates": [],
    }


def create_chart(api, workbook_id, name, config):
    created = api.call(
        f"chart_{name}",
        "POST",
        f"{UI}/api/charts/v1/charts",
        {
            "template": "datalens",
            "name": name,
            "workbookId": workbook_id,
            "data": config,
            "annotation": {"description": ""},
        },
    )
    return created["entryId"]


def widget_tab_id(key):
    """Id of the single tab of chart widget `key`; dashboard connections refer to it."""
    return f"w-{key}-tab"


def create_dashboard(api, workbook_id, channel_ds, charts):
    model_guid = channel_ds["fields"]["model"]["guid"]
    control = {
        "id": "ctl-model",
        "namespace": "default",
        "type": "control",
        "data": {
            "id": "ctl-model",
            "namespace": "default",
            "title": "Модель атрибуции",
            "sourceType": "dataset",
            "source": {
                "datasetId": channel_ds["id"],
                "datasetFieldId": model_guid,
                "elementType": "select",
                "defaultValue": "last_click",
                "multiselectable": False,
                "showTitle": True,
                "required": True,
            },
        },
        "defaults": {model_guid: "last_click"},
    }
    widgets = [
        {
            "id": f"w-{key}",
            "namespace": "default",
            "type": "widget",
            "data": {
                "hideTitle": False,
                "tabs": [
                    {
                        "id": widget_tab_id(key),
                        "title": title,
                        "description": "",
                        "chartId": chart_id,
                        "isDefault": True,
                        "params": {},
                    }
                ],
            },
        }
        for key, (title, chart_id) in charts.items()
    ]
    layout = [{"i": "ctl-model", "x": 0, "y": 0, "w": 12, "h": 2}]
    for index, widget in enumerate(widgets):
        layout.append({"i": widget["id"], "x": (index % 2) * 18, "y": 2 + (index // 2) * 12})
        layout[-1].update(w=18, h=12)
    data = {
        "counter": 10,
        "salt": secrets.token_hex(8),
        "tabs": [
            {
                "id": "tab-main",
                "title": "Воронка и каналы",
                "items": [control, *widgets],
                "layout": layout,
                # Model comparison and funnel must not follow the single-model selector.
                # A connection names a control id or a widget TAB id, not the widget id:
                # datalens-ui src/server/components/sdk/dash.ts rejects other ids.
                # In an "ignore" connection `from` is the item that ignores and `to` the item
                # it ignores (getMapItemsIgnores in @gravity-ui/dashkit). Both found on the
                # stand runs of 19-20.11.2026.
                "connections": [
                    {"from": widget_tab_id("compare"), "to": "ctl-model", "kind": "ignore"},
                    {"from": widget_tab_id("funnel"), "to": "ctl-model", "kind": "ignore"},
                ],
                "aliases": {"default": []},
            }
        ],
        "settings": {
            "autoupdateInterval": None,
            "maxConcurrentRequests": None,
            "silentLoading": False,
            "dependentSelectors": True,
            "hideTabs": False,
            "expandTOC": False,
        },
    }
    created = api.call(
        "dashboard",
        "POST",
        f"{UI}/gateway/root/mix/createDashboardV1",
        {
            "entry": {
                "workbookId": workbook_id,
                "name": "Маркетинговая воронка",
                "meta": {},
                "data": data,
            },
            "mode": "publish",
        },
    )
    return created["entry"]["entryId"], model_guid


def chart_problem(result):
    """Why an /api/run answer is not a built table, or None.

    A successful run carries `data` (charts-engine processor/index.ts in datalens-ui 0.3831.0);
    for a flatTable it is assumed to hold a nonempty `rows` list. An empty 200 answer ({}) fails.
    The `rows` shape was read from the source and confirmed by the stand run of 20.11.2026.
    """
    if not isinstance(result, dict):
        return "answer is not a JSON object"
    if "error" in result:
        return str(result["error"])[:300]
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return "no data.rows in the answer"
    if not data["rows"]:
        return "the table has no rows"
    return None


def run_charts(api, workbook_id, charts, model_guid):
    """Charts engine /api/run: each chart must return table rows; payloads are kept."""
    failures = []
    for key, (_, chart_id) in charts.items():
        params = {} if key in ("compare", "funnel") else {model_guid: ["last_click"]}
        try:
            result = api.call(
                f"run_{key}",
                "POST",
                f"{UI}/api/run",
                {"id": chart_id, "workbookId": workbook_id, "params": params},
            )
            problem = chart_problem(result)
            if problem:
                failures.append(f"{key}: {problem}")
        except ApiError as exc:
            failures.append(str(exc))
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--clickhouse-host", default="clickhouse")
    parser.add_argument("--ready-timeout", type=int, default=600)
    args = parser.parse_args()
    password = os.environ["CLICKHOUSE_DATALENS_PASSWORD"]
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    api = Api(args.out, [password])
    objects = {}
    try:
        wait_ready(
            [f"{CONTROL_API}/api/v1/ping", f"{DATA_API}/ping", f"{UI}/ping"], args.ready_timeout
        )
        workbook = api.call(
            "workbook",
            "POST",
            f"{UI}/gateway/root/us/createWorkbook",
            {"title": "Маркетинговая воронка"},
        )
        workbook_id = objects["workbook"] = workbook["workbookId"]
        connection = api.call(
            "connection",
            "POST",
            f"{CONTROL_API}/api/v1/connections",
            {
                "name": "marketing_funnel_clickhouse",
                "type": "clickhouse",
                "workbook_id": workbook_id,
                "host": args.clickhouse_host,
                "port": 8123,
                "username": "datalens",
                "password": password,
                "db_name": args.database,
                # OnOffField of the DataLens API: "on"/"off" strings, a boolean is rejected
                # (found on the first stand run, 19.11.2026).
                "secure": "off",
                "readonly": 1,
            },
        )
        objects["connection"] = connection["id"]
        channel_ds = create_dataset(
            api,
            "channel_metrics",
            workbook_id,
            connection["id"],
            args.database,
            "channel_metrics",
            CHANNEL_FORMULAS,
        )
        funnel_ds = create_dataset(
            api,
            "user_funnel",
            workbook_id,
            connection["id"],
            args.database,
            "user_funnel",
            FUNNEL_FORMULAS,
        )
        objects["datasets"] = {"channel_metrics": channel_ds["id"], "user_funnel": funnel_ds["id"]}
        _save(args.out / "objects.json", objects)

        models = sorted({row["model"] for row in report["channels"]})
        cards, shares, funnel = read_numbers(api, channel_ds, funnel_ds, models)
        _save(
            args.out / "numbers.json",
            {
                "channels": [{"model": k[0], "channel": k[1], **v} for k, v in cards.items()],
                "unattributed_share": {k[0]: v["unattributed_share"] for k, v in shares.items()},
                "funnel": [{"step_number": k[0], "step": k[1], **v} for k, v in funnel.items()],
            },
        )
        mismatches = (
            compare(expected_channel_cards(report), cards)
            + compare(expected_unattributed_share(report), shares)
            + compare(expected_funnel(report), funnel)
        )

        charts = {
            "channels": (
                "Каналы выбранной модели",
                create_chart(
                    api,
                    workbook_id,
                    "channels_by_model",
                    flat_table(channel_ds, ["channel", *CARD_TO_REPORT], sort=["channel"]),
                ),
            ),
            "share": (
                "Доля без атрибуции",
                create_chart(
                    api,
                    workbook_id,
                    "unattributed_share",
                    flat_table(channel_ds, ["unattributed_share"]),
                ),
            ),
            "compare": (
                "Сравнение моделей",
                create_chart(
                    api,
                    workbook_id,
                    "model_comparison",
                    flat_table(
                        channel_ds, ["model", "channel", "subscriptions"], sort=["model", "channel"]
                    ),
                ),
            ),
            "funnel": (
                "Воронка",
                create_chart(
                    api,
                    workbook_id,
                    "funnel",
                    flat_table(
                        funnel_ds, ["step_number", "step", "users_total"], sort=["step_number"]
                    ),
                ),
            ),
        }
        objects["charts"] = {key: chart_id for key, (_, chart_id) in charts.items()}
        dash_id, model_guid = create_dashboard(api, workbook_id, channel_ds, charts)
        objects["dashboard"] = dash_id
        objects["model_field_guid"] = model_guid
        _save(args.out / "objects.json", objects)
        chart_failures = run_charts(api, workbook_id, charts, model_guid)
    except (ApiError, KeyError, IndexError, TypeError, ValueError) as exc:
        _save(args.out / "objects.json", objects)
        (args.out / "comparison.txt").write_text(f"API FAILURE: {exc}\n", encoding="utf-8")
        print(f"API FAILURE: {exc}", file=sys.stderr)
        return 2

    lines = [
        f"compared: channel rows {len(cards)}, models {len(shares)}, funnel steps {len(funnel)}"
    ]
    if mismatches:
        lines += ["NUMBERS DIFFER", format_table(mismatches)]
    else:
        lines.append("NUMBERS MATCH the reference report")
    lines += [f"chart run failure: {f}" for f in chart_failures] or [
        "charts: every run returned 200 with table rows"
    ]
    text = "\n".join(lines) + "\n"
    (args.out / "comparison.txt").write_text(text, encoding="utf-8")
    print(text)
    if mismatches:
        return 1
    return 2 if chart_failures else 0


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
