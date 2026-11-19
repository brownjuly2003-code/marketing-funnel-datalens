from copy import deepcopy

import pytest

from marketing_funnel.core import analyze


def event(key, user, timestamp, name, channel="", **extra):
    return dict(
        event_id=key,
        user_id=user,
        timestamp=timestamp,
        name=name,
        channel=channel,
        campaign="demo" if channel else "",
        **extra,
    )


EVENTS = [
    event("a", "u", "2026-01-01T12:00:00Z", "click", "search"),
    event("b", "u", "2026-01-02T12:00:00Z", "click", "social"),
    event("c", "u", "2026-01-03T11:00:00Z", "impression", "display"),
    event("d", "u", "2026-01-03T12:00:00Z", "subscription", revenue_cents=10001, cost_cents=4001),
    event("e", "v", "2026-01-02T12:00:00Z", "impression", "display"),
    event("f", "v", "2026-01-03T12:00:00Z", "subscription", revenue_cents=2000, cost_cents=500),
    event("g", "w", "2026-01-01T00:00:00Z", "impression", "display"),
    event("h", "w", "2026-01-03T12:00:00Z", "subscription", revenue_cents=3000, cost_cents=1000),
]
SPEND = [
    dict(channel=c, spend_cents=s)
    for c, s in [("search", 1000), ("social", 2000), ("display", 500), ("unused", 700)]
]


def rows(result, model):
    return {r["channel"]: r for r in result["channels"] if r["model"] == model}


def test_last_click_and_post_view_fallback():
    result = analyze(EVENTS, SPEND)
    actual = rows(result, "last_click")
    assert actual["social"]["conversions"] == 1
    assert actual["display"]["conversions"] == 1
    assert actual["unattributed"]["conversions"] == 1
    assert actual["social"]["margin_cents"] == 4000
    assert actual["unused"]["cpa_cents"] is None
    assert actual["unused"]["margin_cents"] == -700


def test_fractional_models_conserve_conversions_and_integer_money():
    result = analyze(EVENTS, SPEND)
    linear = rows(result, "linear")
    assert linear["search"]["conversions"] == 0.5
    assert linear["social"]["conversions"] == 0.5
    decay = rows(result, "time_decay")
    assert decay["search"]["conversions"] == pytest.approx(1 / 3)
    assert decay["social"]["conversions"] == pytest.approx(2 / 3)
    for model in ("last_click", "linear", "time_decay"):
        actual = list(rows(result, model).values())
        assert sum(r["conversions"] for r in actual) == pytest.approx(3)
        assert sum(r["revenue_cents"] for r in actual) == 15001
        assert sum(r["cost_cents"] for r in actual) == 5501
        assert sum(r["margin_cents"] for r in actual) == 5300


def test_future_touch_and_outside_click_window_are_ignored():
    data = [
        event("a", "u", "2026-01-01T11:59:59Z", "click", "old"),
        event("b", "u", "2026-01-08T12:00:01Z", "click", "future"),
        event("c", "u", "2026-01-08T12:00:00Z", "subscription", revenue_cents=10, cost_cents=0),
    ]
    assert rows(analyze(data, []), "last_click")["unattributed"]["conversions"] == 1


def test_ordered_user_funnel_and_input_order():
    data = [
        event("1", "u", "2026-01-01T00:00:00Z", "impression", "s"),
        event("2", "u", "2026-01-01T01:00:00Z", "click", "s"),
        event("3", "u", "2026-01-01T02:00:00Z", "install"),
        event("4", "u", "2026-01-01T03:00:00Z", "registration"),
        event("5", "u", "2026-01-01T04:00:00Z", "subscription", revenue_cents=10, cost_cents=0),
        event("6", "v", "2026-01-01T04:00:00Z", "subscription", revenue_cents=10, cost_cents=0),
    ]
    result = analyze(data, [])
    assert result["funnel"] == dict(
        impression=1, click=1, install=1, registration=1, subscription=1
    )
    assert analyze(list(reversed(data)), []) == result


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unknown", "negative", "float_money", "naive", "reserved", "spend"]
)
def test_reject_invalid_input(mutation):
    events, spend = deepcopy(EVENTS), deepcopy(SPEND)
    if mutation == "duplicate":
        events.append(events[0])
    elif mutation == "unknown":
        events[0]["name"] = "purchase"
    elif mutation == "negative":
        events[3]["revenue_cents"] = -1
    elif mutation == "float_money":
        events[3]["cost_cents"] = 1.1
    elif mutation == "naive":
        events[0]["timestamp"] = "2026-01-01T12:00:00"
    elif mutation == "reserved":
        events[0]["channel"] = "unattributed"
    else:
        spend[0]["spend_cents"] = -1
    with pytest.raises(ValueError):
        analyze(events, spend)


def test_click_window_boundary_and_same_instant_are_unambiguous():
    data = [
        event("a", "u", "2026-01-01T12:00:00Z", "click", "boundary"),
        event("b", "u", "2026-01-08T12:00:00Z", "click", "simultaneous"),
        event("c", "u", "2026-01-08T12:00:00Z", "subscription", revenue_cents=11, cost_cents=3),
    ]
    actual = rows(analyze(data, []), "last_click")
    assert actual["boundary"]["conversions"] == 1
    assert actual["simultaneous"]["conversions"] == 0


def test_channel_quota_uses_all_touches_and_stable_cent_tie_break():
    data = [
        event("a", "u", "2026-01-01T10:00:00Z", "click", "a"),
        event("b", "u", "2026-01-01T11:00:00Z", "click", "b"),
        event("c", "u", "2026-01-01T12:00:00Z", "subscription", revenue_cents=1, cost_cents=0),
    ]
    actual = rows(analyze(data, []), "linear")
    assert actual["a"]["revenue_cents"] == 1
    assert actual["b"]["revenue_cents"] == 0


def test_linear_cents_follow_exact_quotas():
    # Five clicks of a and one of b: exact quotas 7.5 and 1.5 of 9 cents, 12.5 and 2.5 of 15.
    # Equal remainders, so the spare cent goes to the first channel by name: a.
    data = [event(f"a{i}", "u", f"2026-01-01T0{i}:00:00Z", "click", "a") for i in range(5)]
    data += [
        event("b0", "u", "2026-01-01T06:00:00Z", "click", "b"),
        event("s", "u", "2026-01-01T12:00:00Z", "subscription", revenue_cents=9, cost_cents=15),
    ]
    actual = rows(analyze(data, []), "linear")
    assert (actual["a"]["revenue_cents"], actual["b"]["revenue_cents"]) == (8, 1)
    assert (actual["a"]["cost_cents"], actual["b"]["cost_cents"]) == (13, 2)


def exact_linear(cents, counts):
    """Hand reference: floor of cents * count / total, spare cents by remainder, then name."""
    total = sum(counts.values())
    parts = {channel: divmod(cents * count, total) for channel, count in counts.items()}
    result = {channel: whole for channel, (whole, _) in parts.items()}
    order = sorted(parts, key=lambda channel: (-parts[channel][1], channel))
    for channel in order[: cents - sum(result.values())]:
        result[channel] += 1
    return result


@pytest.mark.parametrize("counts", [(5, 1), (1, 11), (2, 1), (3, 3), (7, 2), (1, 2)])
def test_linear_cents_match_the_integer_rule(counts):
    data = [
        event(f"{channel}{i}", "u", f"2026-01-01T{hour:02d}:{i:02d}:00Z", "click", channel)
        for hour, (channel, count) in enumerate(zip("ab", counts))
        for i in range(count)
    ]
    for cents in range(1, 61):
        sub = event("s", "u", "2026-01-02T00:00:00Z", "subscription", revenue_cents=cents)
        sub.update(cost_cents=0)
        actual = rows(analyze([*data, sub], []), "linear")
        expected = exact_linear(cents, dict(zip("ab", counts)))
        assert {c: actual[c]["revenue_cents"] for c in "ab"} == expected, (counts, cents)


def test_last_click_tie_is_broken_by_event_id_not_by_channel():
    # Same instant: event "b" wins by id although its channel sorts first by name.
    data = [
        event("a", "u", "2026-01-01T12:00:00Z", "click", "zeta"),
        event("b", "u", "2026-01-01T12:00:00Z", "click", "alpha"),
        event("c", "u", "2026-01-02T12:00:00Z", "subscription", revenue_cents=5, cost_cents=1),
    ]
    actual = rows(analyze(data, []), "last_click")
    assert actual["alpha"]["conversions"] == 1
    assert actual["zeta"]["conversions"] == 0


def test_no_conversions_keep_spend_and_null_cpa():
    actual = rows(analyze([], SPEND), "time_decay")
    assert actual["search"]["margin_cents"] == -1000
    assert actual["search"]["cpa_cents"] is None
