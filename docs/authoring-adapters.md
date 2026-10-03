# Authoring adapters

An adapter proves one rung by fetching its own evidence. It never
takes executor output; the verifier hands it a rung name, a locator
merged from the rung template plus the round, and a `since`
timestamp.

## Protocol (`daemon/scavenger/adapters/__init__.py`)

```python
class MyAdapter:
    name = "mine"

    def check(self, rung, locator, since):
        ...
        return ProofResult(
            state=ProofState.PASS, evidence_raw=fetched_text,
            detail="one human line",
        )
```

Return `UNVERIFIED` (never `FAIL`) on network errors, rate limits,
and timeouts. `FAIL` means the world answered no. Truncate evidence
to 64 KiB; the store hashes it.

Settlements: only a payment adapter sets `settlement`, and only with
the amount, currency, and message id actually observed.

## Minimal example

See `daemon/scavenger/adapters/attested.py`: pass on a proof URL in
the locator, pending otherwise. Twelve lines including the docstring.

## Required tests

- One test per state your adapter can return.
- `respx` or fakes for HTTP; a `FakeImap`-style seam for mail.
- Register the name in `goal.KNOWN_ADAPTERS` or GOAL validation
  rejects it.
