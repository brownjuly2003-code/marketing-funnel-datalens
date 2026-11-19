"""Expected DataLens card values from the reference report and a strict comparison.

Run as `python -m marketing_funnel.stand_check NEW_DIR REFERENCE_DIR` to compare a generated
snapshot with the SHA-256 recorded in the reference manifest (exit 1 on any difference).
"""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

SNAPSHOT_FILES = ("inputs.json", "report.json")


def snapshot_differences(new_dir, reference_dir):
    """Names of snapshot files whose SHA-256 differs from the reference manifest (or missing)."""
    manifest = json.loads((Path(reference_dir) / "manifest.json").read_text(encoding="utf-8"))
    differ = []
    for name in SNAPSHOT_FILES:
        path = Path(new_dir) / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if digest != manifest["sha256"][name]:
            differ.append(name)
    return differ


CHANNEL_FIELDS = ("conversions", "revenue_rub", "cost_rub", "spend_rub", "margin_rub", "cpa_rub")


@dataclass(frozen=True)
class Mismatch:
    key: str
    field: str
    expected: object
    actual: object


def _rub(cents):
    return None if cents is None else cents / 100


def expected_channel_cards(report):
    """Channel card per (model, channel): money in rubles, CPA left empty without conversions."""
    return {
        (row["model"], row["channel"]): {
            "conversions": row["conversions"],
            "revenue_rub": _rub(row["revenue_cents"]),
            "cost_rub": _rub(row["cost_cents"]),
            "spend_rub": _rub(row["spend_cents"]),
            "margin_rub": _rub(row["margin_cents"]),
            "cpa_rub": _rub(row["cpa_cents"]),
        }
        for row in report["channels"]
    }


def expected_unattributed_share(report):
    """Share of subscriptions without a paid channel, per model."""
    totals, unattributed = {}, {}
    for row in report["channels"]:
        model = row["model"]
        totals[model] = totals.get(model, 0) + row["conversions"]
        if row["channel"] == "unattributed":
            unattributed[model] = unattributed.get(model, 0) + row["conversions"]
    return {
        (model,): {"unattributed_share": unattributed.get(model, 0) / total if total else None}
        for model, total in totals.items()
    }


def expected_funnel(report):
    """Users per funnel step, keyed by step position as DataLens sorts by step_number."""
    return {
        (number, step): {"users": users}
        for number, (step, users) in enumerate(report["funnel"].items(), start=1)
    }


def _same(expected, actual, abs_tol):
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, str) or isinstance(actual, str):
        return expected == actual
    return math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=abs_tol)


def compare(expected, actual, abs_tol=1e-6):
    """Return every difference between two {key: {field: value}} tables; empty means equal."""
    found = []
    for key in sorted(set(expected) | set(actual), key=repr):
        label = "/".join(str(part) for part in key)
        if key not in actual:
            found.append(Mismatch(label, "(row)", "present", "missing"))
            continue
        if key not in expected:
            found.append(Mismatch(label, "(row)", "absent", "unexpected row"))
            continue
        for field, value in expected[key].items():
            got = actual[key].get(field)
            if not _same(value, got, abs_tol):
                found.append(Mismatch(label, field, value, got))
    return found


def format_table(mismatches):
    """Plain-text table with the columns «ключ | поле | ожидалось | получено»."""
    rows = [("ключ", "поле", "ожидалось", "получено")]
    rows += [(m.key, m.field, repr(m.expected), repr(m.actual)) for m in mismatches]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "\n".join(
        " | ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m marketing_funnel.stand_check NEW_DIR REFERENCE_DIR")
    found = snapshot_differences(sys.argv[1], sys.argv[2])
    for name in found:
        print(f"differs from the reference manifest: {name}", file=sys.stderr)
    sys.exit(1 if found else 0)
