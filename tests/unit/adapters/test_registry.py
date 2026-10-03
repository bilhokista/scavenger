import pytest
from scavenger.adapters import UnknownAdapter, get, register


class Dummy:
    name = "dummy"


def test_register_and_get():
    adapter = Dummy()
    register(adapter)
    assert get("dummy") is adapter


def test_unknown_adapter_raises():
    with pytest.raises(UnknownAdapter):
        get("no-such-adapter-xyz")
