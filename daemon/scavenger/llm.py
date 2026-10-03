import json
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    pass


class LLMFormatError(LLMError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int


class LLM(Protocol):
    def complete(
        self, system: str, prompt: str, *, json_schema: dict | None = None
    ) -> LLMResult: ...


def complete_json(llm: LLM, system: str, prompt: str, schema: dict) -> Any:
    last_error: Exception | None = None
    for _ in range(2):
        result = llm.complete(system, prompt, json_schema=schema)
        try:
            return json.loads(_strip_fences(result.text))
        except json.JSONDecodeError as error:
            last_error = error
    raise LLMFormatError(f"invalid JSON after one retry: {last_error}")


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
        cleaned = cleaned.removeprefix("json\n")
    return cleaned.strip()


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    def complete(
        self, system: str, prompt: str, *, json_schema: dict | None = None
    ) -> LLMResult:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return LLMResult(
            text=text,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
        )


class OpenAICompatibleLLM:
    def __init__(
        self, api_key: str, model: str, max_tokens: int, base_url: str = ""
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url or None

    def complete(
        self, system: str, prompt: str, *, json_schema: dict | None = None
    ) -> LLMResult:
        import openai

        client = openai.OpenAI(api_key=self._api_key, base_url=self._base_url)
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        choice = response.choices[0].message.content or ""
        usage = response.usage
        return LLMResult(
            text=choice,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )


class NoLLM:
    def complete(
        self, system: str, prompt: str, *, json_schema: dict | None = None
    ) -> LLMResult:
        raise LLMError("no LLM configured")
