+++
name = "demo-mission"           # [a-z0-9-]{3,48}, equals directory name
created = 2026-10-03T05:00:00Z
statement = "Freelance developer has no income this month; rent of 1500 USD is due 2026-11-30."

[target]
amount = "1500.00"
currency = "USD"
deadline = 2026-11-30T16:59:59Z

[budget]
money = "50.00"                 # in [budget].currency of scavenger.toml
tokens = 2000000
rounds = 200                    # total failed rounds across all strategies

[settle]                        # the final proof: money arrived
adapter = "payment_email"
rules = ["example-bank"]        # names from [[payment_rules]]

[[ladder]]                      # 3-5 rungs, last one must be "settled"
rung = "submitted"
expected_wait_hours = 2
[[ladder]]
rung = "replied"
expected_wait_hours = 72
max_pending_hours = 168
[[ladder]]
rung = "settled"
expected_wait_hours = 336

[strategy_defaults]
max_rounds = 3                  # failed rounds before a strategy dies
max_loss = "10.00"              # money spent on one strategy before it dies
max_pending_hours = 168

[[assumptions]]
text = "Target currency is USD because no currency was given."
source = "silence"              # "silence" | "human"
+++

## Statement

Free text.

## Assumptions log

Free text mirror of [[assumptions]], appended over time.
