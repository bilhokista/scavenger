import email.utils
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class FeedEntry:
    title: str
    link: str
    body: str
    published: datetime | None


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _atom_text(node, tag: str) -> str:
    child = node.find(f"{_ATOM}{tag}")
    return child.text.strip() if child is not None and child.text else ""


def _parse_rfc822(value: str):
    if not value:
        return None
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _parse_iso(value: str):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def parse_feed(text: str) -> list:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    entries = []
    for item in root.iter("item"):
        entries.append(
            FeedEntry(
                title=_text(item, "title"),
                link=_text(item, "link"),
                body=_text(item, "description"),
                published=_parse_rfc822(_text(item, "pubDate")),
            )
        )
    for entry in root.iter(f"{_ATOM}entry"):
        link = ""
        for node in entry.iter(f"{_ATOM}link"):
            link = node.get("href", "")
            break
        entries.append(
            FeedEntry(
                title=_atom_text(entry, "title"),
                link=link,
                body=_atom_text(entry, "summary"),
                published=_parse_iso(_atom_text(entry, "updated")),
            )
        )
    return entries
