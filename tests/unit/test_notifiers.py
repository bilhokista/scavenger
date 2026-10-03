from scavenger.notifiers import Notice, fan_out


class FakeNotifier:
    def __init__(self, name="fake", supports_replies=False, fail=False):
        self.name = name
        self.supports_replies = supports_replies
        self._fail = fail
        self.received = []

    def notify(self, notice):
        if self._fail:
            raise RuntimeError("delivery down")
        self.received.append(notice)


def make_notice(**overrides):
    fields = {
        "kind": "approval_request",
        "title": "approve this",
        "body": "please review",
        "outbox_id": "abc123",
        "token": "fanout-test-token-0001",
    }
    fields.update(overrides)
    return Notice(**fields)


def test_one_failure_does_not_block_others():
    bad = FakeNotifier(name="bad", fail=True)
    good = FakeNotifier(name="good")
    fan_out(make_notice(), [bad, good])
    assert len(good.received) == 1


def test_all_failures_raise():
    from scavenger.notifiers import AllNotifiersFailed

    try:
        fan_out(
            make_notice(),
            [FakeNotifier(name="a", fail=True), FakeNotifier(name="b", fail=True)],
        )
    except AllNotifiersFailed:
        return
    raise AssertionError("expected AllNotifiersFailed")


def test_token_only_sent_to_reply_capable():
    plain = FakeNotifier(name="plain", supports_replies=False)
    interactive = FakeNotifier(name="interactive", supports_replies=True)
    fan_out(make_notice(), [plain, interactive])
    assert plain.received[0].token is None
    assert "scavenger approve abc123" in plain.received[0].body
    assert interactive.received[0].token == "fanout-test-token-0001"
