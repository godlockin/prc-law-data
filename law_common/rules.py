from __future__ import annotations
import re
from datetime import date

DIGITS = "零一二三四五六七八九"
UNITS = {"十": 10, "百": 100, "千": 1000}


def cn_to_int(text: str) -> int | None:
    if not text:
        return None
    if re.fullmatch(r"[0-9]+", text):
        return int(text)
    total, digit, last_unit = 0, None, 10000
    for char in text:
        if char in DIGITS:
            number = DIGITS.index(char)
            if digit not in (None, 0):
                return None  # Reject malformed headings such as 八二十.
            digit = number
        elif char in UNITS:
            unit = UNITS[char]
            if unit >= last_unit or digit == 0 or (digit is None and not (unit == 10 and total == 0)):
                return None
            total += (digit if digit is not None else 1) * unit
            digit, last_unit = None, unit
        else:
            return None
    return total + (digit or 0)


def article_id(value: object) -> str:
    text = str(value or "").strip().removeprefix("第").replace("条", "")
    return "之".join(str(n) if (n := cn_to_int(part)) is not None else part for part in text.split("之"))


def law_id(value: object) -> str:
    return re.sub(r"[\s《》]", "", str(value or "")).removeprefix("中华人民共和国")


def valid_on(item: dict, as_of: str | None) -> bool:
    if item.get("retrieval_status") == "quarantined":
        return False
    if not as_of:
        return True
    day = date.fromisoformat(as_of)
    try:
        start, end = date.fromisoformat(item.get("effective_date", "")), item.get("expiry_date")
        return start <= day and (day < date.fromisoformat(end) if end else item.get("status") in ("现行有效", "有效", "effective"))
    except (ValueError, TypeError):
        return False


def choose_version(items: list[dict], as_of: str | None, article: str | None = None) -> dict | None:
    eligible = sorted((item for item in items if valid_on(item, as_of)),
                      key=lambda item: (item.get("effective_date", ""), item.get("publish_date", "")), reverse=True)
    if not eligible:
        return None
    top = eligible[0]
    peers = [item for item in eligible if item.get("effective_date") == top.get("effective_date")]
    if any(item.get("articles") != top.get("articles") for item in peers[1:]):
        if not article:
            return None
        number = article_id(article)
        values = [re.sub(r"\s+", "", item.get("articles_by_int", {}).get(number, "")) for item in peers]
        if not values[0] or len(set(values)) != 1:
            return None
        return {**top, "version_conflicts": True}
    return top
