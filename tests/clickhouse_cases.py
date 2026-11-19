"""Integration checks for the ClickHouse server, invoked explicitly by CI."""

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_attribution import event

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/runs/2026-10-31-synthetic"
IMAGE = (
    "clickhouse/clickhouse-server:24.8.14.39@"
    "sha256:1ffa82edee000a42c09313bd9f1293d94c570aee74babc1b3ca9983a35fa597b"
)


def client(container, sql):
    return subprocess.check_output(
        ["docker", "exec", container, "clickhouse-client", "--query", sql],
        text=True,
        timeout=30,
    )


@pytest.fixture(scope="module")
def server():
    container = subprocess.check_output(
        ["docker", "run", "-d", "--rm", "--network", "none", IMAGE], text=True, timeout=120
    ).strip()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                print("ClickHouse", client(container, "SELECT version()").strip())
                print("server timezone", client(container, "SELECT timezone()").strip())
                break
            except subprocess.CalledProcessError:
                time.sleep(1)
        else:
            pytest.fail("ClickHouse did not become ready")
        yield container
    finally:
        subprocess.run(["docker", "stop", container], check=True, capture_output=True, timeout=40)


def load(server, snapshot, database):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/load_clickhouse.py"),
            "--input",
            str(snapshot),
            "--database",
            database,
            "--container",
            server,
        ],
        capture_output=True,
        text=True,
        timeout=80,
    )


def rows(server, sql):
    return [
        json.loads(line)
        for line in client(
            server, sql + " SETTINGS output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow"
        ).splitlines()
    ]


def test_agentflow_branch_and_period(server):
    fixtures = ROOT / "tests/fixtures/agentflow"
    daily = (ROOT / "sql/datalens/agentflow_orders_daily.sql").read_text(encoding="utf-8").strip()
    client(server, "CREATE DATABASE rv")
    client(server, (fixtures / "bv_order_canonical_mat.sql").read_text(encoding="utf-8"))
    client(
        server,
        """INSERT INTO rv.bv_order_canonical_mat
           (order_bk, branch, order_date, order_status, header_source) VALUES
           ('1', 'msk', '2026-01-30 12:00:00', 'delivered', 'bitrix__msk'),
           ('2', 'msk', '2026-01-31 08:30:00', 'cancelled', 'bitrix__msk'),
           ('3', 'msk', '2026-01-31 23:59:59.999', 'confirmed', 'bitrix__msk'),
           ('4', 'msk', '2026-02-01 00:00:00', 'delivered', 'bitrix__msk'),
           ('5', 'spb', '2026-01-31 23:59:59.999', 'confirmed', 'bitrix__spb'),
           ('6', 'spb', '2026-02-01 09:00:00', 'delivered', 'bitrix__spb'),
           ('7', 'msk', '1970-01-01 00:00:00', '', NULL)""",
    )
    actual = client(server, f"SELECT * FROM ({daily}) ORDER BY branch, order_day")
    assert actual.splitlines() == [
        "msk\t2026-01-30\t1",
        "msk\t2026-01-31\t2",
        "msk\t2026-02-01\t1",
        "spb\t2026-01-31\t1",
        "spb\t2026-02-01\t1",
    ]
    filtered = client(
        server,
        f"SELECT sum(orders) FROM ({daily}) WHERE branch = 'msk' "
        "AND order_day BETWEEN '2026-01-31' AND '2026-02-01'",
    )
    assert filtered.strip() == "3"

    # Run the pinned dbt SELECT, resolving its single source in this isolated database.
    model = (fixtures / "branch_pnl.sql").read_text(encoding="utf-8")
    monthly = model[model.index("SELECT\n") :].replace(
        "{{ source('rv', 'bv_order_canonical_mat') }}", "rv.bv_order_canonical_mat"
    )
    by_month = client(
        server,
        f"SELECT branch, toStartOfMonth(order_day) AS month, sum(orders) FROM ({daily}) "
        "GROUP BY branch, month ORDER BY branch, month",
    )
    reference = client(
        server,
        f"SELECT branch, month, orders FROM ({monthly}) WHERE month >= '2026-01-01' "
        "ORDER BY branch, month",
    )
    assert by_month == reference


def test_saved_snapshot_and_repeat(server):
    expected = json.loads((SNAPSHOT / "report.json").read_text(encoding="utf-8"))
    result = load(server, SNAPSHOT / "inputs.json", "sample")
    assert result.returncode == 0, result.stderr
    duplicate = load(server, SNAPSHOT / "inputs.json", "sample")
    assert duplicate.returncode != 0
    assert (
        client(
            server,
            "SELECT count() FROM system.databases WHERE startsWith(name, 'marketing_stage_')",
        ).strip()
        == "0"
    )
    assert client(server, "SELECT count() FROM sample.events").strip() == str(expected["events"])
    for view, fields, keys in [
        ("attribution", "credits", ("conversion_id", "model", "channel")),
        ("channel_metrics", "channels", ("model", "channel")),
    ]:
        actual = rows(
            server,
            f"SELECT * EXCEPT(converted_at) FROM sample.{view}"
            if view == "attribution"
            else f"SELECT * FROM sample.{view}",
        )
        by_key = {tuple(row[key] for key in keys): row for row in actual}
        assert len(actual) == len(by_key) == len(expected[fields])
        for reference in expected[fields]:
            row = by_key[tuple(reference[k] for k in keys)]
            assert row.keys() == reference.keys()
            for field, value in reference.items():
                if isinstance(value, float):
                    assert row[field] == pytest.approx(value, rel=1e-10, abs=1e-9), (row, field)
                else:
                    assert row[field] == value, (row, field)
        print(f"{view}: {len(actual)} rows match the saved {fields}")
    funnel = rows(server, "SELECT step, users FROM sample.user_funnel ORDER BY step_number")
    assert [(row["step"], row["users"]) for row in funnel] == list(expected["funnel"].items())
    print("user_funnel:", [row["users"] for row in funnel])


def test_boundaries_priority_and_cents(server, tmp_path):
    data = {
        "events": [
            # Same instant; ids and channels are crossed so that the event_id tie break
            # (b wins) and a channel-name tie break would pick different channels.
            event("a", "u", "2026-01-01T12:00:00Z", "click", "b"),
            event("b", "u", "2026-01-01T07:00:00-05:00", "click", "a"),
            event("c", "u", "2026-01-01T11:59:59.999999Z", "click", "outside"),
            event("v", "u", "2026-01-08T11:00:00Z", "impression", "display"),
            event("x", "u", "2026-01-08T12:00:00Z", "click", "simultaneous"),
            event("s", "u", "2026-01-08T12:00:00Z", "subscription", revenue_cents=3, cost_cents=1),
            event("i", "v", "2026-01-07T12:00:00Z", "impression", "display"),
            event("t", "v", "2026-01-08T12:00:00Z", "subscription", revenue_cents=7, cost_cents=2),
            event(
                "q",
                "v",
                "2026-01-08T12:00:00.000001Z",
                "subscription",
                revenue_cents=9,
                cost_cents=0,
            ),
        ],
        "spend": [{"channel": "email", "spend_cents": 50}],
    }
    source = tmp_path / "inputs.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    result = load(server, source, "edges")
    assert result.returncode == 0, result.stderr
    last = rows(
        server,
        "SELECT conversion_id, channel FROM edges.attribution "
        "WHERE model='last_click' ORDER BY conversion_id",
    )
    assert [(r["conversion_id"], r["channel"]) for r in last] == [
        ("q", "unattributed"),
        ("s", "a"),
        ("t", "display"),
    ]
    for model in ("linear", "time_decay"):
        split = rows(
            server,
            "SELECT channel, weight, revenue_cents, cost_cents FROM edges.attribution "
            f"WHERE model='{model}' AND conversion_id='s' ORDER BY channel",
        )
        assert split == [
            dict(channel="a", weight=0.5, revenue_cents=2, cost_cents=1),
            dict(channel="b", weight=0.5, revenue_cents=1, cost_cents=0),
        ]
    assert rows(
        server,
        "SELECT margin_cents, cpa_cents FROM edges.channel_metrics "
        "WHERE model='linear' AND channel='email'",
    ) == [{"margin_cents": -50, "cpa_cents": None}]


def test_linear_cents_use_exact_quotas(server, tmp_path):
    # Five clicks of a, one of b: exact quotas 7.5 / 1.5 of 9 cents and 12.5 / 2.5 of 15.
    data = {
        "events": [
            *(event(f"a{i}", "u", f"2026-01-01T0{i}:00:00Z", "click", "a") for i in range(5)),
            event("b0", "u", "2026-01-01T06:00:00Z", "click", "b"),
            event("s", "u", "2026-01-01T12:00:00Z", "subscription", revenue_cents=9, cost_cents=15),
        ],
        "spend": [],
    }
    source = tmp_path / "inputs.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    result = load(server, source, "exact")
    assert result.returncode == 0, result.stderr
    assert rows(
        server,
        "SELECT channel, revenue_cents, cost_cents FROM exact.attribution "
        "WHERE model='linear' ORDER BY channel",
    ) == [
        dict(channel="a", revenue_cents=8, cost_cents=13),
        dict(channel="b", revenue_cents=1, cost_cents=2),
    ]


def test_spend_without_events(server, tmp_path):
    source = tmp_path / "inputs.json"
    source.write_text(
        json.dumps(
            {
                "events": [],
                "spend": [
                    {"channel": "email", "spend_cents": 20},
                    {"channel": "email", "spend_cents": 30},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = load(server, source, "spend_only")
    assert result.returncode == 0, result.stderr
    assert client(server, "SELECT count() FROM spend_only.attribution").strip() == "0"
    assert (
        client(server, "SELECT users FROM spend_only.user_funnel ORDER BY step_number").split()
        == ["0"] * 5
    )
    assert (
        rows(
            server,
            "SELECT count() AS channels, sum(spend_cents) AS spend, sum(margin_cents) AS margin, "
            "count(cpa_cents) AS cpas FROM spend_only.channel_metrics GROUP BY model",
        )
        == [dict(channels=2, spend=50, margin=-50, cpas=0)] * 3
    )


def test_funnel_requires_strict_order(server, tmp_path):
    data = {
        "spend": [],
        "events": [
            event("i", "u", "2026-01-01T00:00:00Z", "impression", "a"),
            event("c", "u", "2026-01-01T00:00:00Z", "click", "a"),
            event("i2", "v", "2026-01-01T00:00:00Z", "impression", "a"),
            event("c2", "v", "2026-01-01T00:00:00.000001Z", "click", "a"),
            event("c3", "v", "2026-01-01T00:00:00.000002Z", "click", "a"),
            event("n", "v", "2026-01-01T00:00:00.000002Z", "install"),
            event("r", "v", "2026-02-01T00:00:00Z", "registration"),
            event("s", "v", "2026-03-01T00:00:00Z", "subscription", revenue_cents=1, cost_cents=0),
        ],
    }
    source = tmp_path / "inputs.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    result = load(server, source, "ordered")
    assert result.returncode == 0, result.stderr
    assert client(server, "SELECT users FROM ordered.user_funnel ORDER BY step_number").split() == [
        "2",
        "1",
        "1",
        "1",
        "1",
    ]
