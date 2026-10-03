#!/usr/bin/env python3
"""Stop hook: no outcome claim without evidence.

Reads the hook JSON from stdin. Looks at the last assistant message
(explicit `last_message`, else the last assistant text in the file at
`transcript_path`). If it claims an outcome and cites no evidence
(URL, outbox id, proof check id, or file path), the stop is blocked
once with a reason asking for the evidence. A second consecutive block
(`stop_hook_active` true) is never issued.
"""

import json
import re
import sys

CLAIM_RE = re.compile(
    r"\b(done|sent|merged|paid|settled|submitted|selesai|terkirim|"
    r"sudah dibayar|berhasil)\b",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(
    r"https?://|[0-9a-f]{32}|proof.{0,5}check.?\d+|\.\w+/"
    r"|[A-Za-z0-9_.-]+\.(md|py|rs|toml|json|txt|diff|patch)",
)


def last_message_text(payload: dict) -> str:
    if payload.get("last_message"):
        return str(payload["last_message"])
    path = payload.get("transcript_path")
    if not path:
        return ""
    texts = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                message = entry.get("message", entry)
                if message.get("role") == "assistant":
                    content = message.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict)
                        )
                    texts.append(str(content))
    except OSError:
        return ""
    return texts[-1] if texts else ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return 0
    if payload.get("stop_hook_active"):
        return 0
    text = last_message_text(payload)
    if not text or not CLAIM_RE.search(text):
        return 0
    if EVIDENCE_RE.search(text):
        return 0
    reason = (
        "Outcome claimed without evidence. Cite a URL, outbox id, "
        "proof check id, or file path, or keep working."
    )
    print(json.dumps({"reason": reason}))
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
