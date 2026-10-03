import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "daemon" / "scavenger"
# senders, notifiers, clients, adapters, and channels are outward by
# design; everything else must never send. llm.py only calls model APIs.
EXEMPT_DIRS = {"senders", "notifiers", "clients", "adapters", "channels"}
EXEMPT_FILES = {"llm.py"}
# Channels needing POST for search endpoints list themselves here.
GRAPHQL_POST_EXCEPTIONS = set()

WRITE_METHODS = {"post", "put", "patch", "delete"}


def iter_modules():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if len(relative.parts) > 1 and relative.parts[0] in EXEMPT_DIRS:
            continue
        if len(relative.parts) == 1 and relative.parts[0] in EXEMPT_FILES:
            continue
        yield path


def check_module(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "smtplib":
                    problems.append(f"{path}: imports smtplib")
        elif isinstance(node, ast.Call):
            target = node.func
            if (
                isinstance(target, ast.Attribute)
                and target.attr in WRITE_METHODS
                and isinstance(target.value, ast.Name)
                and target.value.id == "httpx"
                and str(path) not in GRAPHQL_POST_EXCEPTIONS
            ):
                problems.append(f"{path}: calls httpx.{target.attr}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "run"
                and isinstance(target.value, ast.Name)
                and target.value.id == "subprocess"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.List):
                        words = [
                            element.value
                            for element in arg.elts
                            if isinstance(element, ast.Constant)
                        ]
                        if "git" in words and "push" in words:
                            problems.append(f"{path}: runs git push")
    return problems


def test_outward_isolation():
    problems = []
    for path in iter_modules():
        problems.extend(check_module(path))
    assert not problems, "\n".join(problems)


def test_adapters_and_channels_read_only():
    problems = []
    for part in ("adapters", "channels"):
        base = ROOT / part
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                target = getattr(node, "func", None)
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in WRITE_METHODS
                    and isinstance(getattr(target, "value", None), ast.Name)
                    and target.value.id == "httpx"
                    and str(path) not in GRAPHQL_POST_EXCEPTIONS
                ):
                    problems.append(f"{path}: calls httpx.{target.attr}")
    assert not problems, "\n".join(problems)
