import tempfile
from dataclasses import dataclass
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from scavenger import clock
from scavenger.adapters import ProofResult, ProofState, Settlement
from scavenger.adapters.attested import AttestedAdapter
from scavenger.channels import Action
from scavenger.config import Config, Paths
from scavenger.executor import Executor
from scavenger.llm import LLMResult
from scavenger.loop import Loop
from scavenger.outbox import DraftSpec, create_outbox
from scavenger.research import Research
from scavenger.senders import SendResult
from scavenger.store import Store
from scavenger.switcher import Candidate
from scavenger.verifier import Verifier

FAKE_GOAL = """+++
name = "demo"
statement = "demo mission needs 100 USD"

[target]
amount = "100.00"
currency = "USD"
deadline = 2030-01-01T00:00:00Z

[budget]
money = "50.00"
tokens = 2000000
rounds = 200

[settle]
adapter = "payment_email"
rules = ["example-bank"]

[[ladder]]
rung = "submitted"
expected_wait_hours = 1
[[ladder]]
rung = "settled"
expected_wait_hours = 2

[strategy_defaults]
max_rounds = 3
max_loss = "10.00"
+++

Demo mission.
"""


@dataclass(frozen=True)
class _ScriptedLLM:
    script: tuple

    def __post_init__(self):
        object.__setattr__(self, "_queue", list(self.script))

    def complete(self, system, prompt, *, json_schema=None):
        if not self._queue:
            raise AssertionError("fake LLM out of script")
        text, tokens_in, tokens_out = self._queue.pop(0)
        return LLMResult(text=text, tokens_in=tokens_in, tokens_out=tokens_out)


class _FakeChannel:
    name = "fake-demo"

    def __init__(self, candidate):
        self._candidate = candidate

    def liveness(self):
        from scavenger.channels import Liveness

        return Liveness(
            alive=True,
            evidence_urls=(),
            detail="demo channel",
            checked_at=clock.now(),
        )

    def discover(self, goal):
        return [self._candidate]

    def next_action(self, strategy, last_check):
        import json

        rung = json.loads(strategy.rungs_json)[strategy.rung_index]["rung"]
        if rung == "submitted":
            return Action(
                kind="draft",
                draft=DraftSpec(
                    kind="email",
                    target="demo@example.com",
                    body="demo proposal",
                ),
                description="demo draft",
                fingerprint=f"fake:{strategy.path_key}",
            )
        return Action(
            kind="wait",
            draft=None,
            description="demo wait",
            fingerprint=f"fake-wait:{strategy.path_key}",
        )


class _FakeAdapter:
    def __init__(self, name, results):
        self.name = name
        self._results = list(results)

    def check(self, rung, locator, since):
        return self._results.pop(0)


class _FakeSender:
    kind = "email"

    def send(self, item):
        return SendResult(
            ok=True,
            locator={"thread_message_id": "<fake-demo@host>"},
            detail="fake sent",
        )


def demo_candidate(evidence_url: str) -> Candidate:
    return Candidate(
        channel="fake-demo",
        path="demo opportunity",
        path_key="fake-demo:opportunity",
        outward_key="email:demo@example.com",
        payout_amount=Decimal("100.00"),
        payout_currency="USD",
        guarantor="escrow",
        guarantor_evidence=evidence_url,
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_note="demo",
        rungs=(
            {"rung": "submitted", "adapter": "attested", "locator": {}},
            {"rung": "settled", "adapter": "fake-settle", "locator": {}},
        ),
    )


class _EvidenceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_fake(max_ticks: int = 20) -> dict:
    server = HTTPServer(("127.0.0.1", 0), _EvidenceHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    evidence_url = f"http://127.0.0.1:{server.server_port}/evidence"
    tmp = Path(tempfile.mkdtemp(prefix="scavenger-fake-"))
    config = Config(
        paths=Paths(
            missions=tmp / "missions",
            db=tmp / "ledger.sqlite3",
            run_dir=tmp / ".scavenger",
        )
    )
    import os
    import secrets

    os.environ.setdefault(config.outbox.secret_env, secrets.token_hex(32))
    goal_dir = tmp / "missions" / "demo"
    goal_dir.mkdir(parents=True)
    (goal_dir / "GOAL.md").write_text(FAKE_GOAL, encoding="utf-8")
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    store.create_mission(
        name="demo",
        goal_path="missions/demo/GOAL.md",
        goal_sha256="x",
        status="active",
        target_amount=Decimal("100.00"),
        target_currency="USD",
        deadline="2030-01-01T00:00:00+00:00",
        created_at=clock.now().isoformat(),
    )
    outbox = create_outbox(store, config, [], clock)
    llm = _ScriptedLLM(
        [
            (
                (
                    '{"probabilities": [{"path_key":'
                    ' "fake-demo:opportunity", "probability": 0.8,'
                    ' "note": "demo"}]}'
                ),
                10,
                10,
            ),
        ]
    )
    channel = _FakeChannel(demo_candidate(evidence_url))
    adapters = {
        "attested": AttestedAdapter(),
        "fake-settle": _FakeAdapter(
            "fake-settle",
            [
                ProofResult(
                    state=ProofState.PASS,
                    evidence_raw="demo",
                    detail="demo settled",
                    settlement=Settlement(
                        amount=Decimal("100.00"),
                        currency="USD",
                        message_id="fake-1",
                    ),
                ),
            ],
        ),
    }
    research = Research(store, config, {"fake-demo": channel}, llm, outbox, clock)
    loop = Loop(
        store,
        config,
        Verifier(store, config, adapters, clock),
        Executor(store, config, {"fake-demo": channel}, outbox, clock),
        outbox,
        {"fake-demo": channel},
        {"email": _FakeSender()},
        [],
        research,
        clock,
    )
    ticks = 0
    for _ in range(max_ticks):
        loop.tick()
        ticks += 1
        for item in store.list_outbox("awaiting"):
            outbox.approve(item.id, None, via="cli", by="fake-human")
        if store.get_mission("demo").status != "active":
            break
    mission = store.get_mission("demo")
    return {
        "status": mission.status,
        "ticks": ticks,
        "settled": store.settled_in_target("demo"),
        "report": tmp / "missions" / "demo" / "REPORT.md",
    }
