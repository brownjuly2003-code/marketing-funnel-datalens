"""Build a new immutable marketing snapshot on a ClickHouse server."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from marketing_funnel.core import validate

SQL = Path(__file__).resolve().parents[1] / "sql" / "clickhouse"


def load(source, database, container):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", database):
        raise ValueError("Use a database name containing letters, digits and underscores")
    data = json.loads(source.read_text(encoding="utf-8"))
    events, _ = validate(data["events"], data["spend"])
    client = (
        ["docker", "exec", "-i", container, "clickhouse-client"]
        if container
        else ["clickhouse-client"]
    )
    stage = "marketing_stage_" + uuid4().hex

    def query(statement, *, db="default", payload=None):
        run = subprocess.run(
            [*client, "--database", db, "--multiquery", "--query", statement],
            input=payload,
            capture_output=True,
            encoding="utf-8",
        )
        if run.returncode:
            raise RuntimeError(run.stderr.strip() or f"Client exited with {run.returncode}")

    def insert(table, records):
        payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        if payload:
            query(f"INSERT INTO {table} FORMAT JSONEachRow", db=stage, payload=payload)

    query(f"CREATE DATABASE {stage} ENGINE = Atomic")
    try:
        query((SQL / "inputs.sql").read_text(encoding="utf-8"), db=stage)
        insert(
            "events",
            (
                dict(
                    event_id=key,
                    user_id=event["user_id"],
                    at=at.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    name=event["name"],
                    channel=event.get("channel") or "",
                    campaign=event.get("campaign") or "",
                    revenue_cents=event.get("revenue_cents", 0),
                    cost_cents=event.get("cost_cents", 0),
                )
                for at, key, event in events
            ),
        )
        insert(
            "spend",
            (
                {"channel": row["channel"], "spend_cents": row["spend_cents"]}
                for row in data["spend"]
            ),
        )
        for filename in ("attribution.sql", "channel_metrics.sql", "user_funnel.sql"):
            query((SQL / filename).read_text(encoding="utf-8"), db=stage)
        query(f"RENAME DATABASE {stage} TO `{database}`")
    except BaseException:
        query(f"DROP DATABASE IF EXISTS {stage} SYNC")
        raise
    print(f"{database}: loaded {len(events)} events and {len(data['spend'])} spend rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", required=True, help="New database name for this snapshot")
    parser.add_argument("--container", help="Run clickhouse-client inside this Docker container")
    args = parser.parse_args()
    try:
        load(args.input, args.database, args.container)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        sys.exit(str(exc))
