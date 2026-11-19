import copy
import json
from pathlib import Path

from marketing_funnel.stand_check import (
    compare,
    expected_channel_cards,
    expected_funnel,
    expected_unattributed_share,
    format_table,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT = json.loads(
    (ROOT / "docs/runs/2026-10-31-synthetic/report.json").read_text(encoding="utf-8")
)


def test_expected_cards_follow_dashboard_check():
    cards = expected_channel_cards(REPORT)
    assert len(cards) == 15
    assert cards[("last_click", "search")]["conversions"] == 14
    assert cards[("last_click", "social")]["conversions"] == 14
    assert cards[("last_click", "display")]["conversions"] == 9
    assert cards[("last_click", "unattributed")]["conversions"] == 7
    email = cards[("last_click", "email")]
    assert email["conversions"] == 0 and email["cpa_rub"] is None and email["margin_rub"] == -100
    funnel = expected_funnel(REPORT)
    assert [v["users"] for _, v in sorted(funnel.items())] == [120, 99, 75, 58, 37]
    share = expected_unattributed_share(REPORT)[("last_click",)]["unattributed_share"]
    assert share == 7 / (9 + 0 + 14 + 14 + 7)


def test_identical_tables_have_no_mismatch():
    cards = expected_channel_cards(REPORT)
    assert compare(cards, copy.deepcopy(cards)) == []


def test_broken_number_is_reported_with_expected_and_actual():
    cards = expected_channel_cards(REPORT)
    actual = copy.deepcopy(cards)
    actual[("last_click", "search")]["conversions"] = 13
    actual[("last_click", "email")]["cpa_rub"] = 0.0
    del actual[("linear", "social")]
    found = compare(cards, actual)
    assert {(m.key, m.field) for m in found} == {
        ("last_click/search", "conversions"),
        ("last_click/email", "cpa_rub"),
        ("linear/social", "(row)"),
    }
    table = format_table(found)
    header = [cell.strip() for cell in table.splitlines()[0].split(" | ")]
    assert header == ["ключ", "поле", "ожидалось", "получено"]
    assert "last_click/search | conversions | 14.0" in table and "| 13" in table


def test_tiny_float_noise_is_tolerated_but_a_kopeck_is_not():
    cards = expected_channel_cards(REPORT)
    actual = copy.deepcopy(cards)
    actual[("last_click", "display")]["cpa_rub"] += 1e-9
    assert compare(cards, actual) == []
    actual[("last_click", "display")]["margin_rub"] += 0.01
    assert [m.field for m in compare(cards, actual)] == ["margin_rub"]


def test_snapshot_is_checked_against_the_reference_hashes(tmp_path):
    from marketing_funnel.stand_check import snapshot_differences

    reference = ROOT / "docs/runs/2026-10-31-synthetic"
    assert snapshot_differences(reference, reference) == []
    copy_dir = tmp_path / "snapshot"
    copy_dir.mkdir()
    for name in ("inputs.json", "report.json"):
        (copy_dir / name).write_bytes((reference / name).read_bytes())
    assert snapshot_differences(copy_dir, reference) == []
    # Move every event to another month: the input bytes change and must be rejected.
    data = json.loads((reference / "inputs.json").read_text(encoding="utf-8"))
    for event in data["events"]:
        event["timestamp"] = event["timestamp"].replace("2026-01-", "2026-02-")
    (copy_dir / "inputs.json").write_text(json.dumps(data), encoding="utf-8")
    assert snapshot_differences(copy_dir, reference) == ["inputs.json"]
    (copy_dir / "report.json").unlink()
    assert snapshot_differences(copy_dir, reference) == ["inputs.json", "report.json"]
