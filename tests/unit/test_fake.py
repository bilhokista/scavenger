from decimal import Decimal

from scavenger.fake import run_fake


def test_fake_mission_completes():
    result = run_fake()
    assert result["status"] == "done"
    assert result["settled"] == Decimal("100.00")
    assert result["report"].exists()
    assert result["ticks"] <= 20
