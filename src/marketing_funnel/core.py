"""Calculations for the offline lab."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import floor

STEPS = ("impression", "click", "install", "registration", "subscription")
MODELS = ("last_click", "linear", "time_decay")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return at.astimezone(timezone.utc)


def money(value):
    if type(value) is not int or value < 0:
        raise ValueError("Money must be nonnegative integer cents")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty string")
    return value


def allocate(cents, weights):
    """Largest remainder by channel, with channel-name tie break (float weights: time decay)."""
    raw = {channel: cents * weight for channel, weight in weights.items()}
    result = {channel: floor(value) for channel, value in raw.items()}
    order = sorted(raw, key=lambda channel: (-(raw[channel] - result[channel]), channel))
    for channel in order[: cents - sum(result.values())]:
        result[channel] += 1
    return result


def allocate_counts(cents, counts):
    """Largest remainder with exact integer quotas cents * count / total (linear, last click).

    divmod keeps equal remainders equal, so the channel-name tie break is never decided by
    float rounding."""
    total = sum(counts.values())
    parts = {channel: divmod(cents * count, total) for channel, count in counts.items()}
    result = {channel: whole for channel, (whole, _) in parts.items()}
    order = sorted(parts, key=lambda channel: (-parts[channel][1], channel))
    for channel in order[: cents - sum(result.values())]:
        result[channel] += 1
    return result


def validate(events, spend):
    seen, clean = set(), []
    for event in events:
        key = text(event.get("event_id"))
        text(event.get("user_id"))
        if key in seen or event.get("name") not in STEPS:
            raise ValueError("Duplicate id or unknown event")
        seen.add(key)
        at = timestamp(event.get("timestamp"))
        if event["name"] in ("impression", "click"):
            channel = text(event.get("channel"))
            text(event.get("campaign"))
            if channel == "unattributed":
                raise ValueError("Reserved channel name")
        if event["name"] == "subscription":
            money(event.get("revenue_cents"))
            money(event.get("cost_cents"))
        clean.append((at, key, event))
    costs = defaultdict(int)
    for row in spend:
        channel = text(row.get("channel"))
        if channel == "unattributed":
            raise ValueError("Reserved channel name")
        costs[channel] += money(row.get("spend_cents"))
    return sorted(clean), costs


def user_funnel(clean):
    progress = {}
    previous = {}
    for at, _, event in clean:
        user = event["user_id"]
        step = progress.get(user, 0)
        if step < len(STEPS) and event["name"] == STEPS[step]:
            if user not in previous or at > previous[user]:
                progress[user] = step + 1
                previous[user] = at
    return {name: sum(level > i for level in progress.values()) for i, name in enumerate(STEPS)}


def analyze(events, spend):
    clean, costs = validate(events, spend)
    touches = defaultdict(list)
    channels = set(costs) | {"unattributed"}
    for at, key, event in clean:
        if event["name"] in ("impression", "click"):
            touches[event["user_id"]].append((at, key, event))
            channels.add(event["channel"])
    totals = {
        (model, channel): {
            "model": model,
            "channel": channel,
            "conversions": 0.0,
            "revenue_cents": 0,
            "cost_cents": 0,
            "spend_cents": costs[channel],
        }
        for model in MODELS
        for channel in sorted(channels)
    }
    credits = []
    for at, key, event in clean:
        if event["name"] != "subscription":
            continue
        history = touches[event["user_id"]]
        clicks = [
            row
            for row in history
            if row[2]["name"] == "click" and at - timedelta(days=7) <= row[0] < at
        ]
        views = [
            row
            for row in history
            if row[2]["name"] == "impression" and at - timedelta(hours=24) <= row[0] < at
        ]
        eligible = clicks or views
        for model in MODELS:
            weights = defaultdict(float)
            # Touches per channel: exact money quotas for every model except time decay.
            counts = defaultdict(int)
            if not eligible:
                weights["unattributed"] = 1.0
                counts["unattributed"] = 1
            elif model == "last_click":
                weights[eligible[-1][2]["channel"]] = 1.0
                counts[eligible[-1][2]["channel"]] = 1
            else:
                if model == "linear":
                    for row in eligible:
                        counts[row[2]["channel"]] += 1
                raw = [
                    (
                        row[2]["channel"],
                        1.0 if model == "linear" else 2 ** (-(at - row[0]).total_seconds() / 86400),
                    )
                    for row in eligible
                ]
                denominator = sum(weight for _, weight in raw)
                for channel, weight in raw:
                    weights[channel] += weight / denominator
            if counts:
                revenues = allocate_counts(event["revenue_cents"], counts)
                product_costs = allocate_counts(event["cost_cents"], counts)
            else:
                revenues = allocate(event["revenue_cents"], weights)
                product_costs = allocate(event["cost_cents"], weights)
            for channel, weight in sorted(weights.items()):
                row = totals[model, channel]
                row["conversions"] += weight
                row["revenue_cents"] += revenues[channel]
                row["cost_cents"] += product_costs[channel]
                credits.append(
                    {
                        "conversion_id": key,
                        "model": model,
                        "channel": channel,
                        "weight": weight,
                        "revenue_cents": revenues[channel],
                        "cost_cents": product_costs[channel],
                    }
                )
    for row in totals.values():
        row["cpa_cents"] = row["spend_cents"] / row["conversions"] if row["conversions"] else None
        row["margin_cents"] = row["revenue_cents"] - row["cost_cents"] - row["spend_cents"]
    return {
        "events": len(clean),
        "funnel": user_funnel(clean),
        "channels": list(totals.values()),
        "credits": credits,
    }
