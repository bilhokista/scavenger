import pytest
from scavenger.llm import LLMError, LLMFormatError, LLMResult, NoLLM, complete_json

from tests.conftest import FakeLLM


def test_fake_llm_returns_scripted():
    fake = FakeLLM([LLMResult(text="hi", tokens_in=3, tokens_out=5)])
    result = fake.complete("sys", "hello")
    assert result.text == "hi"


def test_json_schema_invalid_retries_once():
    bad = LLMResult(text="not json", tokens_in=1, tokens_out=1)
    good = LLMResult(text='{"a": 1}', tokens_in=1, tokens_out=1)
    fake = FakeLLM([bad, good])
    assert complete_json(fake, "sys", "give json", {"type": "object"}) == {"a": 1}
    assert len(fake.calls) == 2


def test_json_schema_invalid_twice_raises():
    bad = LLMResult(text="not json", tokens_in=1, tokens_out=1)
    fake = FakeLLM([bad, bad])
    with pytest.raises(LLMFormatError):
        complete_json(fake, "sys", "give json", {"type": "object"})
    assert len(fake.calls) == 2


def test_token_counts_reported():
    fake = FakeLLM([LLMResult(text="x", tokens_in=11, tokens_out=7)])
    result = fake.complete("sys", "hi")
    assert (result.tokens_in, result.tokens_out) == (11, 7)


def test_no_llm_raises():
    with pytest.raises(LLMError):
        NoLLM().complete("sys", "hi")


def test_json_schema_strips_fences():
    fenced = LLMResult(text='```json\n{"a": 1}\n```', tokens_in=1, tokens_out=1)
    fake = FakeLLM([fenced])
    assert complete_json(fake, "sys", "give json", {"type": "object"}) == {"a": 1}
