from datetime import UTC, datetime

from scavenger.adapters import ProofState
from scavenger.adapters.inbox import ImapMessage, InboxAdapter
from scavenger.llm import LLMResult

from tests.conftest import FakeLLM

SINCE = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)

LOCATOR = {
    "thread_message_id": "<thread-1@host>",
    "counterparty": "alice@example.com",
    "require": "any_reply",
}

POSITIVE_LOCATOR = {
    "thread_message_id": "<thread-1@host>",
    "counterparty": "alice@example.com",
    "require": "positive_reply",
}


def message(**overrides):
    base = {
        "message_id": "<reply-1@host>",
        "from_addr": "alice@example.com",
        "date": datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        "in_reply_to": "<thread-1@host>",
        "references": "",
        "subject": "Re: deal",
        "text": "Yes, let's proceed.",
    }
    base.update(overrides)
    return ImapMessage(**base)


class FakeImap:
    def __init__(self, messages=None, error=None):
        self._messages = messages or []
        self._error = error

    def list_since(self, since):
        if self._error is not None:
            raise self._error
        return [message for message in self._messages if message.date >= since]


def test_reply_from_counterparty_passes():
    adapter = InboxAdapter(FakeImap([message()]), FakeLLM([]))
    result = adapter.check("replied", LOCATOR, SINCE)
    assert result.state == ProofState.PASS


def test_reply_from_other_sender_ignored():
    adapter = InboxAdapter(
        FakeImap([message(from_addr="bob@example.com")]), FakeLLM([])
    )
    result = adapter.check("replied", LOCATOR, SINCE)
    assert result.state == ProofState.PENDING


def test_reply_before_since_ignored():
    adapter = InboxAdapter(
        FakeImap([message(date=datetime(2026, 10, 2, 5, 0, tzinfo=UTC))]),
        FakeLLM([]),
    )
    result = adapter.check("replied", LOCATOR, SINCE)
    assert result.state == ProofState.PENDING


def test_positive_reply_required_negative_fails():
    llm = FakeLLM([LLMResult(text='{"label": "negative"}', tokens_in=1, tokens_out=1)])
    adapter = InboxAdapter(FakeImap([message()]), llm)
    result = adapter.check("replied", POSITIVE_LOCATOR, SINCE)
    assert result.state == ProofState.FAIL


def test_unclear_classification_is_pending():
    llm = FakeLLM([LLMResult(text='{"label": "unclear"}', tokens_in=1, tokens_out=1)])
    adapter = InboxAdapter(FakeImap([message()]), llm)
    result = adapter.check("replied", POSITIVE_LOCATOR, SINCE)
    assert result.state == ProofState.PENDING


def test_imap_error_unverified():
    adapter = InboxAdapter(FakeImap(error=ConnectionError("down")), FakeLLM([]))
    result = adapter.check("replied", LOCATOR, SINCE)
    assert result.state == ProofState.UNVERIFIED


def test_classifier_input_excludes_executor_text():
    raw = "Sounds good.\n\nOn 1 Oct wrote:\n> old thread\n> quoted"
    llm = FakeLLM([LLMResult(text='{"label": "positive"}', tokens_in=1, tokens_out=1)])
    adapter = InboxAdapter(FakeImap([message(text=raw)]), llm)
    result = adapter.check("replied", POSITIVE_LOCATOR, SINCE)
    assert result.state == ProofState.PASS
    prompt = llm.calls[0][1]
    assert "Sounds good." in prompt
    assert "old thread" not in prompt
    assert "quoted" not in prompt
