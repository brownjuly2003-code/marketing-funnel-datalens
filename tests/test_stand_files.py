"""Static checks of the DataLens stand files. They do not start DataLens."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
STAND = ROOT / "stand"
COMPOSE = yaml.safe_load((STAND / "docker-compose.yml").read_text(encoding="utf-8"))
RUN_SH = (STAND / "run.sh").read_text(encoding="utf-8")
PINNED = re.compile(r"^[\w./-]+:(?P<tag>[\w.-]+)@sha256:[0-9a-f]{64}$")
SECRET_KEYS = re.compile(r"PASSWORD|MASTER_TOKEN|CRYPTO_KEY|KEY_VAL")


def test_compose_has_the_datalens_release_services_and_clickhouse():
    assert COMPOSE["name"] == "marketing-funnel-datalens-stand"
    assert {"postgres", "control-api", "data-api", "us", "ui", "clickhouse"} <= set(
        COMPOSE["services"]
    )
    for service in COMPOSE["services"].values():
        assert "container_name" not in service


def test_every_image_is_pinned_by_tag_and_digest():
    images = {name: s["image"] for name, s in COMPOSE["services"].items()}
    for name, image in images.items():
        match = PINNED.match(image)
        assert match, (name, image)
        assert match["tag"] != "latest", (name, image)
    assert images["ui"].startswith("ghcr.io/datalens-tech/datalens-ui:0.3831.0@")
    assert images["clickhouse"].startswith("clickhouse/clickhouse-server:24.8.14.39@")
    ch_test = (ROOT / "tests/clickhouse_cases.py").read_text(encoding="utf-8")
    ch_image = "".join(re.findall(r'"([^"]*)"', ch_test.split("IMAGE = (", 1)[1].split(")")[0]))
    assert images["clickhouse"] == ch_image


def test_secrets_come_from_env_file_and_the_example_lists_them():
    example = (STAND / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z_]+)=", example, flags=re.M))
    required = set()
    for service in COMPOSE["services"].values():
        for key, value in (service.get("environment") or {}).items():
            value = str(value)
            required |= set(re.findall(r"\$\{([A-Z_]+):\?", value))
            if SECRET_KEYS.search(key) or "postgres://" in value:
                assert value == "-" or ":?" in value, (key, value)
    assert required and required <= declared


def test_bind_mounts_exist():
    for service in COMPOSE["services"].values():
        for volume in service.get("volumes") or []:
            source = volume.split(":")[0]
            if source.startswith("."):
                assert (STAND / source).exists(), source


def test_run_script_is_strict_and_tears_down():
    assert "set -euo pipefail" in RUN_SH
    assert "trap finish EXIT" in RUN_SH
    assert re.search(r"down -v", RUN_SH)
    assert "up -d --wait" in RUN_SH


def test_paths_used_by_run_script_exist():
    generated = {".env", "out", ".venv"}
    used = re.findall(r"\$(ROOT|STAND_DIR|REFERENCE)/([\w./-]+)", RUN_SH)
    bases = {"ROOT": ROOT, "STAND_DIR": STAND, "REFERENCE": ROOT / "docs/runs/2026-10-31-synthetic"}
    checked = 0
    for base, rel in used:
        if rel.split("/")[0] in generated:
            continue
        assert (bases[base] / rel).exists(), f"${base}/{rel}"
        checked += 1
    assert checked >= 6


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="needs bash on Linux")
def test_run_script_parses_with_bash():
    result = subprocess.run(["bash", "-n", str(STAND / "run.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stand_client_starts_without_datalens():
    result = subprocess.run(
        [sys.executable, str(STAND / "datalens_stand.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--report" in result.stdout


def python_commands(script):
    """Commands of run.sh that start project Python code, continuation lines joined."""
    joined = script.replace("\\\n", " ")
    return [
        line.strip()
        for line in joined.splitlines()
        if '"$PYTHON"' in line and ('"$ROOT/scripts/' in line or "-m marketing_funnel" in line)
    ]


def test_run_script_gives_the_package_path_to_every_project_command():
    commands = python_commands(RUN_SH)
    assert len(commands) >= 3, commands
    for command in commands:
        assert 'PYTHONPATH="$ROOT/src" "$PYTHON"' in command, command


def test_loader_imports_with_the_run_script_environment():
    # Same environment as run.sh: no installed package (-S skips site-packages), only PYTHONPATH.
    (command,) = [c for c in python_commands(RUN_SH) if "load_clickhouse.py" in c]
    prefix = re.search(r'PYTHONPATH="\$ROOT/([\w/]+)"\s+"\$PYTHON"', command)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if prefix:
        env["PYTHONPATH"] = str(ROOT / prefix[1])
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/load_clickhouse.py"), "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--database" in result.stdout


def load_stand_module(name):
    spec = importlib.util.spec_from_file_location(f"stand_{name}", STAND / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self, response):
        self.response = response

    def call(self, name, method, url, body=None, ok=(200,)):
        return self.response


def test_chart_run_without_table_rows_is_a_failure():
    stand = load_stand_module("datalens_stand")
    charts = {key: (key, f"id-{key}") for key in ("channels", "share", "compare", "funnel")}
    assert len(stand.run_charts(FakeApi({}), "wb", charts, "guid")) == 4
    assert len(stand.run_charts(FakeApi({"data": {"rows": []}}), "wb", charts, "guid")) == 4
    good = {"data": {"head": [{"id": "x"}], "rows": [{"cells": [{"value": 1}]}]}}
    assert stand.run_charts(FakeApi(good), "wb", charts, "guid") == []


def test_screenshot_page_needs_a_successful_http_status():
    shots = load_stand_module("screenshots")
    assert shots.page_problem(None) == "no HTTP response"
    assert shots.page_problem(404) == "HTTP 404"
    assert shots.page_problem(500) == "HTTP 500"
    assert shots.page_problem(200) is None


def test_summary_tells_full_success_from_skipped_screenshots():
    assert "overall:" in RUN_SH
    assert "numbers and charts only, screenshots skipped" in RUN_SH


def test_devcontainer_is_pinned_and_sized():
    config = json.loads((ROOT / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    assert re.search(r":ubuntu-24\.04@sha256:[0-9a-f]{64}$", config["image"])
    features = config["features"]
    for name in ("docker-in-docker", "python", "sshd"):
        (ref,) = [f for f in features if f"/features/{name}:" in f]
        assert re.search(r":\d+\.\d+\.\d+$", ref), ref
    assert any(f["version"] == "3.11" for f in features.values() if "version" in f)
    assert config["hostRequirements"] == {"cpus": 4, "memory": "16gb", "storage": "32gb"}


class RecordingApi:
    def __init__(self):
        self.bodies = {}

    def call(self, name, method, url, body=None, ok=(200,)):
        self.bodies[name] = body
        return {"entry": {"entryId": "dash-1"}}


def test_dashboard_connections_name_the_control_and_widget_tabs():
    # datalens-ui dash.ts accepts only control ids and widget tab ids in connections;
    # dashkit getMapItemsIgnores reads an ignore connection as "from ignores to".
    stand = load_stand_module("datalens_stand")
    api = RecordingApi()
    channel_ds = {"id": "ds-1", "fields": {"model": {"guid": "model"}}}
    charts = {key: (key, f"id-{key}") for key in ("channels", "share", "compare", "funnel")}
    assert stand.create_dashboard(api, "wb", channel_ds, charts) == ("dash-1", "model")
    (tab,) = api.bodies["dashboard"]["entry"]["data"]["tabs"]
    known = set()
    for item in tab["items"]:
        if item["type"] == "control":
            known.add(item["id"])
        else:
            known.update(widget_tab["id"] for widget_tab in item["data"]["tabs"])
    pairs = {(c["from"], c["to"]) for c in tab["connections"] if c["kind"] == "ignore"}
    # `from` ignores `to`: the widgets ignore the selector, not the other way round.
    assert pairs == {("w-compare-tab", "ctl-model"), ("w-funnel-tab", "ctl-model")}
    assert {end for pair in pairs for end in pair} <= known


def test_connection_secure_flag_is_an_on_off_string():
    # The DataLens connection API reads `secure` with an OnOffField: "on"/"off", not a boolean.
    source = (STAND / "datalens_stand.py").read_text(encoding="utf-8")
    assert '"secure": "off",' in source
    assert '"secure": False' not in source


def test_clickhouse_profile_allows_the_settings_datalens_sends():
    # With readonly=1 ClickHouse rejects a query setting that differs from the profile value.
    import xml.etree.ElementTree as ET

    root = ET.parse(STAND / "clickhouse/users.d/datalens.xml").getroot()
    profile_name = root.find("users/datalens/profile").text
    profile = root.find(f"profiles/{profile_name}")
    settings = {child.tag: child.text for child in profile}
    assert settings == {
        "readonly": "1",
        "join_use_nulls": "1",
        "send_progress_in_http_headers": "0",
        "output_format_json_quote_denormals": "1",
    }


def test_keep_stand_skips_only_the_teardown():
    assert '"${KEEP_STAND:-0}" = 1' in RUN_SH
    assert "KEEP_STAND=1 (debugging only" in RUN_SH


def test_dashboard_shot_needs_the_selected_model_and_every_compared_model():
    shots = load_stand_module("screenshots")
    compare = "\n".join(["Сравнение моделей", "model", "last_click", "linear", "time_decay"])
    assert shots.dashboard_problem("linear", " linear ", compare) is None
    assert "selector shows 'last_click'" in shots.dashboard_problem("linear", "last_click", compare)
    only_one = "\n".join(["Сравнение моделей", "model", "linear", "linear"])
    assert "lacks last_click, time_decay" in shots.dashboard_problem("linear", "linear", only_one)


def test_dashboard_is_shot_for_every_model_and_switched_by_a_click():
    shots = load_stand_module("screenshots")
    assert shots.dashboard_shots() == [
        ("04_dashboard_last_click", "last_click"),
        ("05_dashboard_linear", "linear"),
        ("05_dashboard_time_decay", "time_decay"),
    ]
    source = (STAND / "screenshots.py").read_text(encoding="utf-8")
    assert "?{" not in source and "quote(" not in source
    names = [name for name, _ in shots.pages({"workbook": "wb", "dashboard": "d", "charts": {}})]
    assert names == ["00_workbook"]
