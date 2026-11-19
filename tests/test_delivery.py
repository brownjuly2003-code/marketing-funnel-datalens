import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_cli_snapshot_and_independent_audit(tmp_path):
    target = tmp_path / "demo"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketing_funnel",
            "--out",
            str(target),
            "--size",
            "20",
            "--seed",
            "7",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit.py"), str(target)],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 0, audit.stderr
    report_path = target / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["channels"][0]["revenue_cents"] += 1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["report.json"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit.py"), str(target)],
        capture_output=True,
        text=True,
    )
    assert tampered.returncode != 0


def clicks(channel, count, hour):
    return [
        dict(
            event_id=f"{channel}{i}",
            user_id="u",
            timestamp=f"2026-01-01T{hour:02d}:{i:02d}:00Z",
            name="click",
            channel=channel,
            campaign="demo",
        )
        for i in range(count)
    ]


def snapshot(tmp_path, events, name="snap"):
    """CLI --input on a valid input, then the audit; returns (target, audit result)."""
    source = tmp_path / f"{name}.json"
    source.write_text(json.dumps({"events": events, "spend": []}), encoding="utf-8")
    target = tmp_path / name
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "marketing_funnel", "--input", str(source), "--out", str(target)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return target, audit(target)


def audit(target):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit.py"), str(target)],
        capture_output=True,
        text=True,
    )


def test_audit_accepts_a_subscription_without_channel_field(tmp_path):
    # The validator needs a channel only on impressions and clicks.
    subscription = dict(
        event_id="s",
        user_id="u",
        timestamp="2026-01-01T12:00:00Z",
        name="subscription",
        revenue_cents=10,
        cost_cents=1,
    )
    _, result = snapshot(tmp_path, [*clicks("a", 6, 1), subscription])
    assert result.returncode == 0, result.stderr


def test_audit_checks_linear_cents_with_integer_quotas(tmp_path):
    subscription = dict(
        event_id="s",
        user_id="u",
        timestamp="2026-01-01T12:00:00Z",
        name="subscription",
        revenue_cents=9,
        cost_cents=0,
    )
    target, result = snapshot(tmp_path, [*clicks("a", 5, 1), *clicks("b", 1, 2), subscription])
    assert result.returncode == 0, result.stderr
    report_path = target / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    linear = {r["channel"]: r for r in report["credits"] if r["model"] == "linear"}
    assert (linear["a"]["revenue_cents"], linear["b"]["revenue_cents"]) == (8, 1)
    # Move the spare cent to b, as float rounding did, and keep the totals consistent.
    linear["a"]["revenue_cents"], linear["b"]["revenue_cents"] = 7, 2
    for row in report["channels"]:
        if row["model"] == "linear" and row["channel"] in ("a", "b"):
            delta = -1 if row["channel"] == "a" else 1
            row["revenue_cents"] += delta
            row["margin_cents"] += delta
    report_path.write_text(json.dumps(report), encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["report.json"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered = audit(target)
    assert tampered.returncode != 0
    assert "allocation.s.linear.revenue_cents" in tampered.stderr
