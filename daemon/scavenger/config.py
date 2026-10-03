import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path


class ConfigError(ValueError):
    pass


def _reject_unknown(table: str, data: Mapping, known: set) -> None:
    for key in data:
        if key not in known:
            raise ConfigError(f"unknown key {key!r} in [{table}]")


def _decimal(value: object, key: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ConfigError(f"bad decimal for {key!r}: {value!r}") from None


@dataclass(frozen=True)
class Paths:
    missions: Path
    db: Path
    run_dir: Path


@dataclass(frozen=True)
class Llm:
    provider: str = "none"
    model: str = ""
    api_key_env: str = ""
    base_url: str = ""
    max_tokens_per_call: int = 4000
    price_per_million_input: Decimal = Decimal(0)
    price_per_million_output: Decimal = Decimal(0)


@dataclass(frozen=True)
class Budget:
    currency: str = "USD"


@dataclass(frozen=True)
class Fx:
    rates: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Http:
    timeout_seconds: int = 20
    user_agent: str = "scavenger/0.1"


@dataclass(frozen=True)
class Loop:
    tick_seconds: int = 60
    max_active: int = 1


@dataclass(frozen=True)
class Verifier:
    unverified_backoff_minutes: tuple = ()
    check_interval_minutes: int = 30


@dataclass(frozen=True)
class Research:
    max_strategies: int = 5
    min_interval_minutes: int = 60
    max_tokens_per_run: int = 200000
    gap_floor_ratio: Decimal = Decimal("0.05")
    web_search: str = "none"


@dataclass(frozen=True)
class Ledger:
    min_history_for_probability: int = 3
    block_after_same_cause_deaths: int = 2
    block_days: int = 14


@dataclass(frozen=True)
class Outbox:
    token_ttl_hours: int = 24
    max_sends_per_day: int = 10
    max_open_prs_per_repo: int = 3
    duplicate_body_window_days: int = 7
    secret_env: str = "SCAVENGER_APPROVAL_SECRET"


@dataclass(frozen=True)
class Supervisor:
    max_hourly_money: Decimal = Decimal("5.00")
    max_hourly_tokens: int = 500000
    heartbeat_stale_factor: int = 3
    spin_rounds: int = 3


@dataclass(frozen=True)
class Executor:
    agent_command: tuple = ()
    agent_timeout_minutes: int = 60


@dataclass(frozen=True)
class Github:
    token_env: str = "GITHUB_TOKEN"
    username: str = ""


@dataclass(frozen=True)
class Imap:
    host: str = ""
    port: int = 993
    username_env: str = "SCAVENGER_IMAP_USER"
    password_env: str = "SCAVENGER_IMAP_PASSWORD"
    folder: str = "INBOX"


@dataclass(frozen=True)
class Smtp:
    host: str = ""
    port: int = 587
    username_env: str = "SCAVENGER_SMTP_USER"
    password_env: str = "SCAVENGER_SMTP_PASSWORD"
    from_address: str = ""


@dataclass(frozen=True)
class PaymentRule:
    name: str = ""
    from_regex: str = ""
    subject_regex: str = ""
    amount_regex: str = ""
    decimal_separator: str = "."
    currency: str = ""
    currency_regex: str = ""
    reference_regex: str = ""


@dataclass(frozen=True)
class EmailNotifier:
    to: str = ""


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token_env: str = "SCAVENGER_TELEGRAM_TOKEN"
    chat_id: str = ""
    allowed_user_ids: tuple = ()


@dataclass(frozen=True)
class DiscordNotifier:
    webhook_url_env: str = "SCAVENGER_DISCORD_WEBHOOK"


@dataclass(frozen=True)
class SlackNotifier:
    webhook_url_env: str = "SCAVENGER_SLACK_WEBHOOK"


@dataclass(frozen=True)
class NtfyNotifier:
    server: str = "https://ntfy.sh"
    topic_env: str = "SCAVENGER_NTFY_TOPIC"


@dataclass(frozen=True)
class Notifiers:
    enabled: tuple = ("desktop",)
    email: EmailNotifier = field(default_factory=EmailNotifier)
    telegram: TelegramNotifier = field(default_factory=TelegramNotifier)
    discord: DiscordNotifier = field(default_factory=DiscordNotifier)
    slack: SlackNotifier = field(default_factory=SlackNotifier)
    ntfy: NtfyNotifier = field(default_factory=NtfyNotifier)


@dataclass(frozen=True)
class GrantRound:
    name: str = ""
    url: str = ""
    amount: str = ""
    currency: str = "USD"
    deadline: str = ""
    requirements: str = ""


@dataclass(frozen=True)
class InboxFilter:
    subject_regex: str = ""
    body_regex: str = ""


@dataclass(frozen=True)
class Channels:
    enabled: tuple = ()
    grants_feeds: tuple = ()
    grants_rounds: tuple = ()
    inbox_feeds: tuple = ()
    inbox_filters: tuple = ()
    inbox_folder: str = "INBOX"


@dataclass(frozen=True)
class Config:
    paths: Paths
    llm: Llm = field(default_factory=Llm)
    budget: Budget = field(default_factory=Budget)
    fx: Fx = field(default_factory=Fx)
    http: Http = field(default_factory=Http)
    loop: Loop = field(default_factory=Loop)
    verifier: Verifier = field(default_factory=Verifier)
    research: Research = field(default_factory=Research)
    ledger: Ledger = field(default_factory=Ledger)
    outbox: Outbox = field(default_factory=Outbox)
    supervisor: Supervisor = field(default_factory=Supervisor)
    executor: Executor = field(default_factory=Executor)
    github: Github = field(default_factory=Github)
    imap: Imap = field(default_factory=Imap)
    smtp: Smtp = field(default_factory=Smtp)
    payment_rules: tuple = ()
    notifiers: Notifiers = field(default_factory=Notifiers)
    channels: Channels = field(default_factory=Channels)

    @staticmethod
    def require_env(name: str) -> str:
        try:
            return os.environ[name]
        except KeyError:
            raise ConfigError(
                f"missing required environment variable: {name}"
            ) from None


def _paths(data: Mapping, base: Path) -> Paths:
    _reject_unknown("paths", data, {"missions", "db", "run_dir"})
    resolve = lambda v, default: base / v if v else base / default
    return Paths(
        missions=resolve(data.get("missions"), "missions"),
        db=resolve(data.get("db"), "ledger.sqlite3"),
        run_dir=resolve(data.get("run_dir"), ".scavenger"),
    )


def _notifiers(data: Mapping) -> Notifiers:
    _reject_unknown(
        "notifiers",
        data,
        {"enabled", "email", "telegram", "discord", "slack", "ntfy"},
    )
    email = data.get("email", {})
    _reject_unknown("notifiers.email", email, {"to"})
    telegram = data.get("telegram", {})
    _reject_unknown(
        "notifiers.telegram", telegram, {"bot_token_env", "chat_id", "allowed_user_ids"}
    )
    discord = data.get("discord", {})
    _reject_unknown("notifiers.discord", discord, {"webhook_url_env"})
    slack = data.get("slack", {})
    _reject_unknown("notifiers.slack", slack, {"webhook_url_env"})
    ntfy = data.get("ntfy", {})
    _reject_unknown("notifiers.ntfy", ntfy, {"server", "topic_env"})
    return Notifiers(
        enabled=tuple(data.get("enabled", ("desktop",))),
        email=EmailNotifier(to=email.get("to", "")),
        telegram=TelegramNotifier(
            bot_token_env=telegram.get("bot_token_env", "SCAVENGER_TELEGRAM_TOKEN"),
            chat_id=telegram.get("chat_id", ""),
            allowed_user_ids=tuple(telegram.get("allowed_user_ids", ())),
        ),
        discord=DiscordNotifier(
            webhook_url_env=discord.get("webhook_url_env", "SCAVENGER_DISCORD_WEBHOOK")
        ),
        slack=SlackNotifier(
            webhook_url_env=slack.get("webhook_url_env", "SCAVENGER_SLACK_WEBHOOK")
        ),
        ntfy=NtfyNotifier(
            server=ntfy.get("server", "https://ntfy.sh"),
            topic_env=ntfy.get("topic_env", "SCAVENGER_NTFY_TOPIC"),
        ),
    )


def load(path: str | Path) -> Config:
    path = Path(path)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    base = path.parent
    _reject_unknown(
        "root",
        data,
        {
            "paths",
            "llm",
            "budget",
            "fx",
            "http",
            "loop",
            "verifier",
            "research",
            "ledger",
            "outbox",
            "supervisor",
            "executor",
            "github",
            "imap",
            "smtp",
            "payment_rules",
            "notifiers",
            "channels",
        },
    )
    llm = data.get("llm", {})
    _reject_unknown(
        "llm",
        llm,
        {
            "provider",
            "model",
            "api_key_env",
            "base_url",
            "max_tokens_per_call",
            "price_per_million_input",
            "price_per_million_output",
        },
    )
    budget = data.get("budget", {})
    _reject_unknown("budget", budget, {"currency"})
    fx = data.get("fx", {})
    rates = {}
    for key, value in fx.items():
        try:
            src, dst = (part.strip().upper() for part in key.split("->"))
        except ValueError:
            raise ConfigError(f"bad fx key {key!r}, want FROM->TO") from None
        rates[(src, dst)] = _decimal(value, f"fx.{key}")
    http = data.get("http", {})
    _reject_unknown("http", http, {"timeout_seconds", "user_agent"})
    loop = data.get("loop", {})
    _reject_unknown("loop", loop, {"tick_seconds", "max_active"})
    verifier = data.get("verifier", {})
    _reject_unknown(
        "verifier", verifier, {"unverified_backoff_minutes", "check_interval_minutes"}
    )
    research = data.get("research", {})
    _reject_unknown(
        "research",
        research,
        {
            "max_strategies",
            "min_interval_minutes",
            "max_tokens_per_run",
            "gap_floor_ratio",
            "web_search",
        },
    )
    ledger = data.get("ledger", {})
    _reject_unknown(
        "ledger",
        ledger,
        {"min_history_for_probability", "block_after_same_cause_deaths", "block_days"},
    )
    outbox = data.get("outbox", {})
    _reject_unknown(
        "outbox",
        outbox,
        {
            "token_ttl_hours",
            "max_sends_per_day",
            "max_open_prs_per_repo",
            "duplicate_body_window_days",
            "secret_env",
        },
    )
    supervisor = data.get("supervisor", {})
    _reject_unknown(
        "supervisor",
        supervisor,
        {
            "max_hourly_money",
            "max_hourly_tokens",
            "heartbeat_stale_factor",
            "spin_rounds",
        },
    )
    executor = data.get("executor", {})
    _reject_unknown("executor", executor, {"agent_command", "agent_timeout_minutes"})
    github = data.get("github", {})
    _reject_unknown("github", github, {"token_env", "username"})
    imap = data.get("imap", {})
    _reject_unknown(
        "imap", imap, {"host", "port", "username_env", "password_env", "folder"}
    )
    smtp = data.get("smtp", {})
    _reject_unknown(
        "smtp", smtp, {"host", "port", "username_env", "password_env", "from_address"}
    )
    rules = []
    for rule in data.get("payment_rules", []):
        _reject_unknown(
            "payment_rules",
            rule,
            {
                "name",
                "from_regex",
                "subject_regex",
                "amount_regex",
                "decimal_separator",
                "currency",
                "currency_regex",
                "reference_regex",
            },
        )
        rules.append(
            PaymentRule(
                **{
                    k: rule.get(k, "")
                    for k in (
                        "name",
                        "from_regex",
                        "subject_regex",
                        "amount_regex",
                        "decimal_separator",
                        "currency",
                        "currency_regex",
                        "reference_regex",
                    )
                }
            )
        )
    channels = data.get("channels", {})
    _reject_unknown(
        "channels",
        channels,
        {
            "enabled",
            "grants_feeds",
            "grants_rounds",
            "inbox_feeds",
            "inbox_filters",
            "inbox_folder",
        },
    )
    grant_rounds = []
    for entry in channels.get("grants_rounds", []):
        _reject_unknown(
            "channels.grants_rounds",
            entry,
            {"name", "url", "amount", "currency", "deadline", "requirements"},
        )
        grant_rounds.append(
            GrantRound(
                name=entry.get("name", ""),
                url=entry.get("url", ""),
                amount=entry.get("amount", ""),
                currency=entry.get("currency", "USD"),
                deadline=entry.get("deadline", ""),
                requirements=entry.get("requirements", ""),
            )
        )
    inbox_filters = []
    for entry in channels.get("inbox_filters", []):
        _reject_unknown(
            "channels.inbox_filters",
            entry,
            {"subject_regex", "body_regex"},
        )
        inbox_filters.append(
            InboxFilter(
                subject_regex=entry.get("subject_regex", ""),
                body_regex=entry.get("body_regex", ""),
            )
        )
    return Config(
        paths=_paths(data.get("paths", {}), base),
        llm=Llm(
            provider=llm.get("provider", "none"),
            model=llm.get("model", ""),
            api_key_env=llm.get("api_key_env", ""),
            base_url=llm.get("base_url", ""),
            max_tokens_per_call=llm.get("max_tokens_per_call", 4000),
            price_per_million_input=_decimal(
                llm.get("price_per_million_input", "0"), "llm.price_per_million_input"
            ),
            price_per_million_output=_decimal(
                llm.get("price_per_million_output", "0"),
                "llm.price_per_million_output",
            ),
        ),
        budget=Budget(currency=budget.get("currency", "USD")),
        fx=Fx(rates=rates),
        http=Http(
            timeout_seconds=http.get("timeout_seconds", 20),
            user_agent=http.get("user_agent", "scavenger/0.1"),
        ),
        loop=Loop(
            tick_seconds=loop.get("tick_seconds", 60),
            max_active=loop.get("max_active", 1),
        ),
        verifier=Verifier(
            unverified_backoff_minutes=tuple(
                verifier.get("unverified_backoff_minutes", ())
            ),
            check_interval_minutes=verifier.get("check_interval_minutes", 30),
        ),
        research=Research(
            max_strategies=research.get("max_strategies", 5),
            min_interval_minutes=research.get("min_interval_minutes", 60),
            max_tokens_per_run=research.get("max_tokens_per_run", 200000),
            gap_floor_ratio=_decimal(
                research.get("gap_floor_ratio", "0.05"), "research.gap_floor_ratio"
            ),
            web_search=research.get("web_search", "none"),
        ),
        ledger=Ledger(
            min_history_for_probability=ledger.get("min_history_for_probability", 3),
            block_after_same_cause_deaths=ledger.get(
                "block_after_same_cause_deaths", 2
            ),
            block_days=ledger.get("block_days", 14),
        ),
        outbox=Outbox(
            token_ttl_hours=outbox.get("token_ttl_hours", 24),
            max_sends_per_day=outbox.get("max_sends_per_day", 10),
            max_open_prs_per_repo=outbox.get("max_open_prs_per_repo", 3),
            duplicate_body_window_days=outbox.get("duplicate_body_window_days", 7),
            secret_env=outbox.get("secret_env", "SCAVENGER_APPROVAL_SECRET"),
        ),
        supervisor=Supervisor(
            max_hourly_money=_decimal(
                supervisor.get("max_hourly_money", "5.00"),
                "supervisor.max_hourly_money",
            ),
            max_hourly_tokens=supervisor.get("max_hourly_tokens", 500000),
            heartbeat_stale_factor=supervisor.get("heartbeat_stale_factor", 3),
            spin_rounds=supervisor.get("spin_rounds", 3),
        ),
        executor=Executor(
            agent_command=tuple(executor.get("agent_command", ())),
            agent_timeout_minutes=executor.get("agent_timeout_minutes", 60),
        ),
        github=Github(
            token_env=github.get("token_env", "GITHUB_TOKEN"),
            username=github.get("username", ""),
        ),
        imap=Imap(
            host=imap.get("host", ""),
            port=imap.get("port", 993),
            username_env=imap.get("username_env", "SCAVENGER_IMAP_USER"),
            password_env=imap.get("password_env", "SCAVENGER_IMAP_PASSWORD"),
            folder=imap.get("folder", "INBOX"),
        ),
        smtp=Smtp(
            host=smtp.get("host", ""),
            port=smtp.get("port", 587),
            username_env=smtp.get("username_env", "SCAVENGER_SMTP_USER"),
            password_env=smtp.get("password_env", "SCAVENGER_SMTP_PASSWORD"),
            from_address=smtp.get("from_address", ""),
        ),
        payment_rules=tuple(rules),
        notifiers=_notifiers(data.get("notifiers", {})),
        channels=Channels(
            enabled=tuple(channels.get("enabled", ())),
            grants_feeds=tuple(channels.get("grants_feeds", ())),
            grants_rounds=tuple(grant_rounds),
            inbox_feeds=tuple(channels.get("inbox_feeds", ())),
            inbox_filters=tuple(inbox_filters),
            inbox_folder=channels.get("inbox_folder", "INBOX"),
        ),
    )
