from datetime import UTC, datetime

import pytest
from scavenger.channels import (
    Liveness,
    UnknownChannel,
    get,
    liveness_all,
    register,
)


class FakeChannel:
    def __init__(self, name, alive=True):
        self.name = name
        self._alive = alive

    def liveness(self):
        return Liveness(
            alive=self._alive,
            evidence_urls=(),
            detail="probe",
            checked_at=datetime(2026, 10, 4, tzinfo=UTC),
        )


class ExplodingChannel:
    name = "exploding"

    def liveness(self):
        raise ConnectionError("down")


def test_registry():
    channel = FakeChannel("registry-probe")
    register(channel)
    assert get("registry-probe") is channel
    with pytest.raises(UnknownChannel):
        get("no-such-channel-xyz")


def test_dead_channel_skipped_not_fatal():
    live = liveness_all(
        {
            "good": FakeChannel("good", alive=True),
            "bad": FakeChannel("bad", alive=False),
            "boom": ExplodingChannel(),
        }
    )
    assert list(live) == ["good"]
