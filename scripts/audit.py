"""Independent stdlib/SQLite audit. Does not import the application package."""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def check(actual, expected, label):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"{label}: keys differ")
        for key in expected:
            check(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(f"{label}: length differs")
        for i, (left, right) in enumerate(zip(actual, expected)):
            check(left, right, f"{label}[{i}]")
    elif isinstance(expected, float):
        if type(actual) not in (int, float) or not math.isclose(
            actual, expected, rel_tol=1e-10, abs_tol=1e-9
        ):
            raise ValueError(f"{label}: numeric mismatch")
    elif actual != expected:
        raise ValueError(f"{label}: mismatch {actual!r} != {expected!r}")


def exact_cents(amount, counts):
    """Integer largest remainder: floor of amount * count / total, then remainder, then name."""
    total = sum(counts.values())
    parts = {channel: divmod(amount * count, total) for channel, count in counts.items()}
    result = {channel: whole for channel, (whole, _) in parts.items()}
    order = sorted(parts, key=lambda channel: (-parts[channel][1], channel))
    for channel in order[: amount - sum(result.values())]:
        result[channel] += 1
    return result


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main():
    target = Path(sys.argv[1])
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    for name in ("inputs.json", "report.json"):
        check(
            hashlib.sha256((target / name).read_bytes()).hexdigest(), manifest["sha256"][name], name
        )
    data = json.loads((target / "inputs.json").read_text(encoding="utf-8"))
    report = json.loads((target / "report.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(event_id TEXT, user_id TEXT, name TEXT, at REAL, channel TEXT)")
    # Touches must carry a channel; install, registration and subscription may omit it.
    db.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        [
            (
                e["event_id"],
                e["user_id"],
                e["name"],
                at(e["timestamp"]).timestamp(),
                e["channel"] if e["name"] in ("impression", "click") else e.get("channel"),
            )
            for e in data["events"]
        ],
    )
    db.create_function("exp2", 1, lambda x: 2**x)
    weights = [
        dict(row) for row in db.execute((ROOT / "sql/attribution.sql").read_text(encoding="utf-8"))
    ]
    credits = sorted(
        report["credits"], key=lambda r: (r["conversion_id"], r["model"], r["channel"])
    )
    check(
        [
            {key: row[key] for key in ("conversion_id", "model", "channel", "weight")}
            for row in credits
        ],
        [
            {key: row[key] for key in ("conversion_id", "model", "channel", "weight")}
            for row in weights
        ],
        "credits",
    )
    # Last click gives the whole amount to one channel; linear splits it by touch counts.
    touches = {
        (row["conversion_id"], row["model"], row["channel"]): (
            1 if row["model"] == "last_click" else row["channel_touches"]
        )
        for row in weights
        if row["model"] != "time_decay"
    }
    conversions = {e["event_id"]: e for e in data["events"] if e["name"] == "subscription"}
    for key in conversions:
        for model in ("last_click", "linear", "time_decay"):
            group = [
                row for row in credits if row["conversion_id"] == key and row["model"] == model
            ]
            for field in ("revenue_cents", "cost_cents"):
                check(
                    sum(row[field] for row in group),
                    conversions[key][field],
                    f"{key}.{model}.{field}",
                )
                amount = conversions[key][field]
                if model == "time_decay":
                    # Each channel must receive its floor quota or one extra cent.
                    ranked = sorted(
                        group, key=lambda r: (-(amount * r["weight"] % 1), r["channel"])
                    )
                    remaining = amount - sum(math.floor(amount * r["weight"]) for r in group)
                    expected_cents = {
                        row["channel"]: math.floor(amount * row["weight"]) + (i < remaining)
                        for i, row in enumerate(ranked)
                    }
                else:
                    # Exact quotas from SQLite touch counts: amount * channel / path touches.
                    counts = {
                        channel: count
                        for (conv, mod, channel), count in touches.items()
                        if conv == key and mod == model
                    }
                    expected_cents = exact_cents(amount, counts)
                check(
                    {row["channel"]: row[field] for row in group},
                    expected_cents,
                    f"allocation.{key}.{model}.{field}",
                )
    costs = {}
    for row in data["spend"]:
        costs[row["channel"]] = costs.get(row["channel"], 0) + row["spend_cents"]
    channels = (
        set(costs)
        | {e["channel"] for e in data["events"] if e["name"] in ("impression", "click")}
        | {"unattributed"}
    )
    expected = []
    for model in ("last_click", "linear", "time_decay"):
        for channel in sorted(channels):
            group = [r for r in credits if r["model"] == model and r["channel"] == channel]
            count = sum(r["weight"] for r in group)
            revenue = sum(r["revenue_cents"] for r in group)
            cost = sum(r["cost_cents"] for r in group)
            spend = costs.get(channel, 0)
            expected.append(
                dict(
                    model=model,
                    channel=channel,
                    conversions=float(count),
                    revenue_cents=revenue,
                    cost_cents=cost,
                    spend_cents=spend,
                    cpa_cents=spend / count if count else None,
                    margin_cents=revenue - cost - spend,
                )
            )
    check(report["channels"], expected, "channels")
    check(report["events"], len(data["events"]), "events")
    # Independent SQL reachability, without requiring every conversion to enter the funnel.
    query = "SELECT count(DISTINCT i.user_id) AS impression, count(DISTINCT c.user_id) AS click, count(DISTINCT n.user_id) AS install, count(DISTINCT r.user_id) AS registration, count(DISTINCT s.user_id) AS subscription FROM events i LEFT JOIN events c ON c.user_id=i.user_id AND c.name='click' AND c.at>i.at LEFT JOIN events n ON n.user_id=c.user_id AND n.name='install' AND n.at>c.at LEFT JOIN events r ON r.user_id=n.user_id AND r.name='registration' AND r.at>n.at LEFT JOIN events s ON s.user_id=r.user_id AND s.name='subscription' AND s.at>r.at WHERE i.name='impression'"
    check(report["funnel"], dict(db.execute(query).fetchone()), "funnel")
    db.close()
    print("PASS: independent SQL audit and artifact hashes")


if __name__ == "__main__":
    main()
