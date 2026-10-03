class FakeLLM:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def complete(self, system, prompt, *, json_schema=None):
        self.calls.append((system, prompt, json_schema))
        if not self._script:
            raise AssertionError("FakeLLM out of scripted responses")
        return self._script.pop(0)
