import argparse
import json
import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_HUMAN = 2
EXIT_SUPERVISOR_ACTED = 3


def build_parser() -> argparse.ArgumentParser:
    from scavenger import __version__

    parser = argparse.ArgumentParser(prog="scavenger")
    parser.add_argument("--config", default="./scavenger.toml")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("brief")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("mission")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--once", action="store_true")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("mission", nargs="?")
    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("id")
    approve_parser.add_argument("--token", default=None)
    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("id")
    redraft_parser = sub.add_parser("redraft")
    redraft_parser.add_argument("id")
    mark_parser = sub.add_parser("mark-sent")
    mark_parser.add_argument("id")
    mark_parser.add_argument("--locator", required=True)
    guarantor_parser = sub.add_parser("guarantor")
    guarantor_parser.add_argument("strategy", type=int)
    guarantor_parser.add_argument("kind")
    guarantor_parser.add_argument("url")
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("strategy", type=int)
    stop_parser = sub.add_parser("stop")
    stop_parser.add_argument("mission")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("mission")
    supervise_parser = sub.add_parser("supervise")
    supervise_parser.add_argument("--once", action="store_true")
    sub.add_parser("liveness")
    unblock_parser = sub.add_parser("unblock")
    unblock_parser.add_argument("channel")
    return parser


def load_config_only(path: str):
    from scavenger.config import load

    return load(path)


def open_store(config):
    from scavenger.store import Store

    return Store.open(config.paths.db, run_dir=config.paths.run_dir)


def build_llm(config):
    from scavenger.llm import AnthropicLLM, NoLLM, OpenAICompatibleLLM

    provider = config.llm.provider
    if provider == "anthropic":
        key = os.environ.get(config.llm.api_key_env, "")
        return AnthropicLLM(key, config.llm.model, config.llm.max_tokens_per_call)
    if provider == "openai_compatible":
        key = os.environ.get(config.llm.api_key_env, "")
        return OpenAICompatibleLLM(
            key, config.llm.model, config.llm.max_tokens_per_call, config.llm.base_url
        )
    return NoLLM()


def build_outbox(store, config, clock):
    from scavenger.outbox import create_outbox

    return create_outbox(store, config, [], clock)


def ask_stdin(question: str):
    try:
        answer = input(f"{question} ")
    except EOFError:
        return None
    answer = answer.strip()
    return answer or None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except SystemExit as error:
        return int(error.code or 0)
    except Exception as error:  # noqa: BLE001 - top-level CLI error boundary
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


def dispatch(args) -> int:
    command = args.command.replace("-", "_")
    handler = COMMANDS[command]
    return handler(args)


def cmd_init(args) -> int:
    path = Path(args.config)
    if not path.exists():
        path.write_text(
            '[paths]\nmissions = "missions"\n'
            'db = "ledger.sqlite3"\nrun_dir = ".scavenger"\n',
            encoding="utf-8",
        )
    from scavenger.config import load

    config = load(path)
    config.paths.missions.mkdir(parents=True, exist_ok=True)
    config.paths.run_dir.mkdir(parents=True, exist_ok=True)
    print(f"initialized {path}")
    return EXIT_OK


def cmd_compile(args) -> int:
    from scavenger import clock
    from scavenger.compiler import MissingRequiredField, compile_brief

    config = load_config_only(args.config)
    store = open_store(config)
    llm = build_llm(config)
    try:
        goal = compile_brief(
            args.brief,
            ask_stdin,
            config,
            llm=llm,
            store=store,
            clock=clock,
        )
    except MissingRequiredField as error:
        from scavenger.compiler import QUESTIONS

        print(QUESTIONS.get(error.field, error.field))
        return EXIT_NEEDS_HUMAN
    print(f"compiled mission {goal.name}")
    return EXIT_OK


def cmd_validate(args) -> int:
    from scavenger import clock
    from scavenger.goal import parse as parse_goal
    from scavenger.goal import validate

    config = load_config_only(args.config)
    store = open_store(config)
    mission = store.get_mission(args.mission)
    if mission is None:
        print(f"unknown mission: {args.mission}", file=sys.stderr)
        return EXIT_ERROR
    path = Path(mission.goal_path)
    if not path.is_absolute():
        path = config.paths.missions.parent / path
    errors = validate(parse_goal(path), config, clock.now())
    for error in errors:
        print(f"{error.code}: {error.detail}")
    return EXIT_ERROR if errors else EXIT_OK


def cmd_run(args) -> int:
    import time

    from scavenger import clock
    from scavenger.loop import Loop

    config = load_config_only(args.config)
    stack = build_stack(config)
    loop = Loop(
        stack["store"],
        config,
        stack["verifier"],
        stack["executor"],
        stack["outbox"],
        stack["channels"],
        stack["senders"],
        stack["notifiers"],
        stack["research"],
        clock,
    )
    if args.once:
        loop.tick()
        return EXIT_OK
    run_dir = config.paths.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_file = run_dir / "daemon.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        while True:
            loop.tick()
            time.sleep(config.loop.tick_seconds)
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass
    return EXIT_OK


def build_stack(config):
    from scavenger import clock
    from scavenger.adapters import attested, github, inbox, payment_email
    from scavenger.channels import bounty_board, client_inbox, grants
    from scavenger.executor import Executor
    from scavenger.notifiers import email
    from scavenger.research import Research
    from scavenger.senders import email_smtp, github_comment, github_pr, manual
    from scavenger.verifier import Verifier

    store = open_store(config)
    outbox = build_outbox(store, config, clock)
    adapters = {
        "github": github.GitHubAdapter(
            os.environ.get(config.github.token_env, ""),
            timeout=config.http.timeout_seconds,
        ),
        "inbox": inbox.InboxAdapter(
            inbox.ImapClient(
                config.imap.host,
                os.environ.get(config.imap.username_env, ""),
                os.environ.get(config.imap.password_env, ""),
                config.imap.folder,
            ),
            build_llm(config),
        ),
        "payment_email": payment_email.PaymentEmailAdapter(
            inbox.ImapClient(
                config.imap.host,
                os.environ.get(config.imap.username_env, ""),
                os.environ.get(config.imap.password_env, ""),
                config.imap.folder,
            ),
            [],
        ),
        "attested": attested.AttestedAdapter(),
    }
    llm = build_llm(config)
    channels = {}
    if "bounty" in config.channels.enabled:
        from scavenger.clients.github import GithubRestClient

        github_client = GithubRestClient(
            os.environ.get(config.github.token_env, ""),
            timeout=config.http.timeout_seconds,
            username=config.github.username,
        )
        channels["bounty"] = bounty_board.BountyBoardChannel(
            github_client,
            [],
            config.github.username,
        )
    if "grants" in config.channels.enabled:
        import httpx

        channels["grants"] = grants.GrantsChannel(
            list(config.channels.grants_feeds),
            httpx,
            llm,
            [
                {
                    "name": r.name,
                    "url": r.url,
                    "amount": r.amount,
                    "currency": r.currency,
                    "deadline": r.deadline,
                    "requirements": r.requirements,
                }
                for r in config.channels.grants_rounds
            ],
        )
    if "client-inbox" in config.channels.enabled:
        import httpx

        channels["client-inbox"] = client_inbox.ClientInboxChannel(
            inbox.ImapClient(
                config.imap.host,
                os.environ.get(config.imap.username_env, ""),
                os.environ.get(config.imap.password_env, ""),
                config.imap.folder,
            ),
            list(config.channels.inbox_feeds),
            httpx,
            list(config.channels.inbox_filters),
            llm,
        )
    senders = {
        "email": email_smtp.EmailSender(
            email.SmtpClient(
                config.smtp.host,
                config.smtp.port,
                os.environ.get(config.smtp.username_env, ""),
                os.environ.get(config.smtp.password_env, ""),
                config.smtp.from_address,
            ),
            config.smtp.from_address,
        ),
        "manual": manual.ManualSender(),
    }
    if "bounty" in channels:
        from scavenger.clients.github import GithubRestClient

        github_client = GithubRestClient(
            os.environ.get(config.github.token_env, ""),
            timeout=config.http.timeout_seconds,
            username=config.github.username,
        )
        senders["github_pr"] = github_pr.GithubPrSender(
            github_client, config.github.username
        )
        senders["github_comment"] = github_comment.GithubCommentSender(
            github_client, config.github.username
        )
    notifiers = build_notifiers(config)
    research = Research(store, config, channels, llm, outbox, clock)
    verifier = Verifier(store, config, adapters, clock)
    executor = Executor(store, config, channels, outbox, clock)
    return {
        "store": store,
        "outbox": outbox,
        "adapters": adapters,
        "channels": channels,
        "senders": senders,
        "notifiers": notifiers,
        "research": research,
        "verifier": verifier,
        "executor": executor,
    }


def build_notifiers(config):
    from scavenger.notifiers import desktop, discord, email, ntfy, slack
    from scavenger.notifiers import telegram as telegram_mod

    enabled = set(config.channels.enabled)
    found = []
    if "desktop" in config.notifiers.enabled:
        found.append(desktop.DesktopNotifier())
    if "email" in config.notifiers.enabled and config.smtp.host:
        found.append(
            email.EmailNotifier(
                email.SmtpClient(
                    config.smtp.host,
                    config.smtp.port,
                    os.environ.get(config.smtp.username_env, ""),
                    os.environ.get(config.smtp.password_env, ""),
                    config.smtp.from_address,
                ),
                config.notifiers.email.to,
            )
        )
    telegram = config.notifiers.telegram
    if "telegram" in config.notifiers.enabled and telegram.chat_id:
        found.append(
            telegram_mod.TelegramNotifier(
                os.environ.get(telegram.bot_token_env, ""),
                telegram.chat_id,
                tuple(telegram.allowed_user_ids),
                str(config.paths.run_dir),
            )
        )
    discord_cfg = config.notifiers.discord
    if "discord" in config.notifiers.enabled:
        url = os.environ.get(discord_cfg.webhook_url_env, "")
        if url:
            found.append(discord.DiscordNotifier(url))
    slack_cfg = config.notifiers.slack
    if "slack" in config.notifiers.enabled:
        url = os.environ.get(slack_cfg.webhook_url_env, "")
        if url:
            found.append(slack.SlackNotifier(url))
    ntfy_cfg = config.notifiers.ntfy
    if "ntfy" in config.notifiers.enabled:
        topic = os.environ.get(ntfy_cfg.topic_env, "")
        if topic:
            found.append(ntfy.NtfyNotifier(ntfy_cfg.server, topic))
    _ = enabled
    return found


def cmd_status(args) -> int:
    config = load_config_only(args.config)
    store = open_store(config)
    names = (
        [args.mission]
        if args.mission
        else [mission.name for mission in store.list_missions()]
    )
    for name in names:
        mission = store.get_mission(name)
        if mission is None:
            print(f"unknown mission: {name}", file=sys.stderr)
            return EXIT_ERROR
        settled = store.settled_in_target(name)
        print(f"{name}: {mission.status} settled {settled}")
    return EXIT_OK


def cmd_approve(args) -> int:
    from scavenger import clock

    config = load_config_only(args.config)
    store = open_store(config)
    outbox = build_outbox(store, config, clock)
    from scavenger.outbox import OutboxRefused

    try:
        outbox.approve(args.id, args.token, via="cli", by="human")
    except OutboxRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"approved {args.id}")
    return EXIT_OK


def cmd_reject(args) -> int:
    from scavenger import clock

    config = load_config_only(args.config)
    store = open_store(config)
    outbox = build_outbox(store, config, clock)
    from scavenger.outbox import OutboxRefused

    try:
        outbox.reject(args.id, via="cli", by="human")
    except OutboxRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"rejected {args.id}")
    return EXIT_OK


def cmd_redraft(args) -> int:
    from scavenger import clock

    config = load_config_only(args.config)
    store = open_store(config)
    outbox = build_outbox(store, config, clock)
    from scavenger.outbox import OutboxRefused

    try:
        item = outbox.redraft(args.id)
    except OutboxRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"redrafted {item.id}")
    return EXIT_OK


def cmd_mark_sent(args) -> int:
    from scavenger import clock

    config = load_config_only(args.config)
    store = open_store(config)
    item = store.get_outbox(args.id)
    if item is None or item.status != "approved":
        print("needs an approved draft", file=sys.stderr)
        return EXIT_ERROR
    try:
        locator = json.loads(args.locator)
    except ValueError:
        print("locator is not JSON", file=sys.stderr)
        return EXIT_ERROR
    store.set_outbox_status(
        args.id,
        "sent",
        sent_at=clock.now().isoformat(),
        send_result_json=json.dumps(locator),
    )
    round_row = store.round_for_outbox(args.id)
    if round_row is not None:
        store.set_round_locator(round_row.id, json.dumps(locator, sort_keys=True))
        if round_row.ended_at is None:
            store.end_round(
                round_row.id,
                ended_at=clock.now().isoformat(),
                result_state=round_row.result_state or "pending",
            )
    print(f"marked sent {args.id}")
    return EXIT_OK


def cmd_guarantor(args) -> int:
    config = load_config_only(args.config)
    store = open_store(config)
    strategy = store.get_strategy(args.strategy)
    if strategy is None:
        print("unknown strategy", file=sys.stderr)
        return EXIT_ERROR
    if args.kind not in ("escrow", "grant", "signed_client"):
        print("kind must be escrow, grant, or signed_client", file=sys.stderr)
        return EXIT_ERROR
    store.update_strategy(
        args.strategy, guarantor=args.kind, guarantor_evidence=args.url
    )
    print(f"strategy {args.strategy} guarantor {args.kind}")
    return EXIT_OK


def cmd_resume(args) -> int:
    config = load_config_only(args.config)
    store = open_store(config)
    strategy = store.get_strategy(args.strategy)
    if strategy is None or strategy.status != "needs_human":
        print("strategy is not waiting", file=sys.stderr)
        return EXIT_ERROR
    store.update_strategy(args.strategy, status="active")
    print(f"resumed {args.strategy}")
    return EXIT_OK


def cmd_stop(args) -> int:
    from scavenger import clock
    from scavenger.report import write_report

    config = load_config_only(args.config)
    store = open_store(config)
    if store.get_mission(args.mission) is None:
        print("unknown mission", file=sys.stderr)
        return EXIT_ERROR
    store.set_mission_status(
        args.mission,
        "stopped_human",
        stopped_at=clock.now().isoformat(),
        stop_reason="stopped_human",
    )
    write_report(store, args.mission, config.paths.missions)
    print(f"stopped {args.mission}")
    return EXIT_OK


def cmd_report(args) -> int:
    config = load_config_only(args.config)
    store = open_store(config)
    if store.get_mission(args.mission) is None:
        print("unknown mission", file=sys.stderr)
        return EXIT_ERROR
    from scavenger.report import write_report

    path = write_report(store, args.mission, config.paths.missions)
    print(path)
    return EXIT_OK


def cmd_supervise(args) -> int:
    config = load_config_only(args.config)
    if not args.once:
        print("only --once is supported", file=sys.stderr)
        return EXIT_ERROR
    from scavenger.supervisor import run_once

    result = run_once(config)
    print(f"exit {result.exit_code}: {','.join(result.triggers)}")
    return result.exit_code


def cmd_liveness(args) -> int:
    from scavenger.channels import liveness_all

    config = load_config_only(args.config)
    store = open_store(config)
    stack = build_stack(config)
    live = liveness_all(stack["channels"], store)
    for name in stack["channels"]:
        print(f"{name}: {'alive' if name in live else 'dead'}")
    return EXIT_OK


def cmd_unblock(args) -> int:
    config = load_config_only(args.config)
    store = open_store(config)
    store.unblock_channel(args.channel)
    print(f"unblocked {args.channel}")
    return EXIT_OK


COMMANDS = {
    "init": cmd_init,
    "compile": cmd_compile,
    "validate": cmd_validate,
    "run": cmd_run,
    "status": cmd_status,
    "approve": cmd_approve,
    "reject": cmd_reject,
    "redraft": cmd_redraft,
    "mark_sent": cmd_mark_sent,
    "guarantor": cmd_guarantor,
    "resume": cmd_resume,
    "stop": cmd_stop,
    "report": cmd_report,
    "supervise": cmd_supervise,
    "liveness": cmd_liveness,
    "unblock": cmd_unblock,
}


if __name__ == "__main__":
    raise SystemExit(main())
