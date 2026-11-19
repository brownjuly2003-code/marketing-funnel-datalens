"""Synthetic multichannel journeys with a fixed observation period."""

from datetime import datetime, timedelta, timezone
from random import Random


def generate(size, seed):
    rng = Random(seed)
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    channels = ("search", "social", "display")
    events = []

    def emit(user, at, name, channel="", **extra):
        events.append(
            dict(
                event_id=f"e{len(events):06d}",
                user_id=user,
                timestamp=at.isoformat(),
                name=name,
                channel=channel,
                campaign=f"{channel}_jan" if channel else "",
                **extra,
            )
        )

    for i in range(size):
        user = f"u{i:04d}"
        at = origin + timedelta(days=rng.randrange(21), hours=rng.randrange(20))
        first, second = rng.sample(channels, 2)
        emit(user, at, "impression", first)
        if rng.random() < 0.8:
            emit(user, at + timedelta(hours=1), "click", first)
            if rng.random() < 0.6:
                emit(user, at + timedelta(hours=3), "click", second)
        if rng.random() < 0.7:
            emit(user, at + timedelta(hours=4), "install")
            if rng.random() < 0.75:
                emit(user, at + timedelta(hours=5), "registration")
                if rng.random() < 0.55:
                    delay = 240 if rng.random() < 0.15 else 8
                    revenue = rng.randint(1000, 8000) * 100
                    emit(
                        user,
                        at + timedelta(hours=delay),
                        "subscription",
                        revenue_cents=revenue,
                        cost_cents=revenue * 45 // 100,
                    )
    spend = [dict(channel=channel, spend_cents=rng.randint(40000, 90000)) for channel in channels]
    spend.append(dict(channel="email", spend_cents=10000))
    return dict(events=events, spend=spend)
