"""Turn a Fetch markdown body, or an Agent output_schema result, into one reading.

Fetch gives the page back as text. The Agent gives the schema we asked for.
Both end as an Observation so the alert rule does not care which layer ran.

Stock is `determined` only when the page actually said so. An undetermined
reading is the signal to escalate, not a guess of "out of stock".
"""

from __future__ import annotations

import json
import re

from rtx5090_watcher.models import Observation
from rtx5090_watcher.pricing import MAX_CARD_CENTS, MIN_CARD_CENTS

# $**7,999**.00 is how Newegg's fetched markdown bolds the dollar part.
_BOLD_PRICE = re.compile(r"\$\*\*([0-9][0-9,]*)\*\*\.(\d{2})")
_PRICE = re.compile(r"\$([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.(\d{2}))?")
_CARD = re.compile(r"rtx\s*5090", re.IGNORECASE)
_CARD_HINT = re.compile(r"\b(graphics\s+card|geforce|video\s+card)\b", re.IGNORECASE)
_NOT_A_CARD = re.compile(
    r"\b(gaming\s+pc|gaming\s+desktop|desktop|laptop|notebook)\b",
    re.IGNORECASE,
)

_OUT_PHRASES = (
    "out of stock",
    "sold out",
    "currently unavailable",
    "coming soon",
    "notify me when",
    "no longer available",
)
_IN_PHRASES = (
    "add to cart",
    "add to bag",
    "in stock",
    "available now",
    "buy now",
)
_CHALLENGE = (
    "verify you are human",
    "cf-browser-verification",
    "access denied",
    "just a moment",
    "enable javascript and cookies",
)
_EMPTY = (
    "no results found",
    "0 results",
    "we found 0",
    "did not match",
    "didn't match",
)
_BLOCKED_RESULT = ("captcha", "access denied", "bot detected", "verify you are human")


def normalize_markdown(text: str) -> str:
    return _BOLD_PRICE.sub(lambda m: f"${m.group(1)}.{m.group(2)}", text).replace("**", "")


def parse_price_cents(value: object) -> int | None:
    """Parse a retailer price. Rejects accessories and the 'you save $1,000' trap."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        cents = int(round(float(value) * 100))
        return cents if MIN_CARD_CENTS <= cents <= MAX_CARD_CENTS else None
    text = normalize_markdown(str(value))
    match = _PRICE.search(text)
    if match is None:
        return None
    dollars = int(match.group(1).replace(",", ""))
    cents = dollars * 100 + int(match.group(2) or "0")
    if not MIN_CARD_CENTS <= cents <= MAX_CARD_CENTS:
        return None
    return cents


def stock_from_text(text: str) -> bool | None:
    lowered = text.lower()
    says_out = any(phrase in lowered for phrase in _OUT_PHRASES)
    says_in = any(phrase in lowered for phrase in _IN_PHRASES)
    if says_out and says_in:
        return None
    if says_out:
        return False
    if says_in:
        return True
    return None


def parse_fetch_text(text: str, target) -> Observation:
    if target.page_kind == "search":
        return parse_search_markdown(text, in_stock_filter=target.in_stock_filter)
    return parse_product_markdown(text, product_name=target.product_name)


def parse_product_markdown(text: str, product_name: str) -> Observation:
    normalized = normalize_markdown(text)
    price = _first_price(normalized)
    if _is_challenge(normalized) and price is None:
        return _undetermined("Fetch returned a bot check instead of a product page")
    stock = stock_from_text(normalized)
    name = _name_from_text(normalized, product_name)
    if stock is False:
        return Observation(True, False, price, name, None, "Product page says out of stock", 1)
    if stock is True and price is None:
        return Observation(False, True, None, name, None, "In stock, but Fetch text had no card price", 1)
    if stock is True:
        return Observation(True, True, price, name, None, "Product page shows a buy button and a price", 1)
    return Observation(False, None, price, name, None, "Fetch text did not say whether the card can be bought", 0)


def parse_search_markdown(text: str, in_stock_filter: bool) -> Observation:
    normalized = normalize_markdown(text)
    listings = _extract_listings(normalized, in_stock_filter)
    if listings:
        return _from_listings(listings)
    if _is_empty(normalized):
        return Observation(True, False, None, None, None, "Search page reports no matching results", 0)
    if _is_challenge(normalized):
        return _undetermined("Fetch returned a bot check instead of listings")
    return _undetermined("No RTX 5090 graphics-card price found in the Fetch text")


def parse_agent_result(result: dict | None, error: str | None, status: str) -> Observation:
    if status != "COMPLETED" or not isinstance(result, dict):
        why = error or f"Agent run {status}"
        return _undetermined(why)
    blob = json.dumps(result).lower()
    if any(phrase in blob for phrase in _BLOCKED_RESULT):
        return _undetermined("Agent result still looks blocked")
    in_stock = result.get("in_stock")
    if not isinstance(in_stock, bool):
        in_stock = stock_from_button(result.get("button_state"))
    price = parse_price_cents(result.get("price"))
    product = result.get("product") or None
    if isinstance(product, str):
        product = product.strip() or None
    listing = result.get("listing_url")
    if not isinstance(listing, str) or not listing.startswith("http"):
        listing = None
    button = result.get("button_state") or ""
    if in_stock is True and price is None:
        return Observation(False, True, None, product, listing, "Agent says in stock but returned no price", 1)
    if isinstance(in_stock, bool):
        detail = f"Agent button_state={button!s}" if button else "Agent returned the stock schema"
        return Observation(True, in_stock, price, product, listing, detail, 1)
    return _undetermined("Agent result had no in_stock value")


def stock_from_button(value: object) -> bool | None:
    if not isinstance(value, str):
        return None
    return stock_from_text(value)


def _from_listings(listings: list[_Listing]) -> Observation:
    in_stock = [item for item in listings if item.stock is True]
    unknown = [item for item in listings if item.stock is None]
    if in_stock:
        best = min(in_stock, key=lambda item: item.price_cents)
        detail = (
            f"{len(in_stock)} in-stock RTX 5090 card(s); "
            f"cheapest is {best.title} at ${best.price_cents / 100:,.2f}"
        )
        return Observation(True, True, best.price_cents, best.title, None, detail, len(listings))
    if unknown:
        return _undetermined(f"{len(unknown)} card listing(s) had a price but no stock signal")
    best = min(listings, key=lambda item: item.price_cents)
    return Observation(
        True,
        False,
        best.price_cents,
        best.title,
        None,
        f"All {len(listings)} card listing(s) say out of stock",
        len(listings),
    )


class _Listing:
    def __init__(self, title: str, price_cents: int, stock: bool | None) -> None:
        self.title = title
        self.price_cents = price_cents
        self.stock = stock


def _extract_listings(text: str, in_stock_filter: bool) -> list[_Listing]:
    lines = text.splitlines()
    listings: list[_Listing] = []
    index = 0
    while index < len(lines):
        title = lines[index].strip()
        if not _is_card_title(title):
            index += 1
            continue
        window = [title]
        price: int | None = None
        cursor = index + 1
        while cursor < len(lines) and cursor <= index + 12:
            nxt = lines[cursor].strip()
            if nxt and (_is_card_title(nxt) or _is_other_product(nxt)):
                break
            window.append(nxt)
            if price is None and not _skip_price_line(nxt):
                price = _first_price(nxt)
            cursor += 1
        if price is not None:
            listings.append(_Listing(title, price, _listing_stock(window, in_stock_filter)))
        index = cursor
    return listings


def _listing_stock(window: list[str], in_stock_filter: bool) -> bool | None:
    blob = "\n".join(window).lower()
    if "out of stock" in blob or "sold out" in blob:
        return False
    if in_stock_filter:
        return True
    if "add to cart" in blob or "in stock" in blob:
        return True
    return None


def _is_card_title(line: str) -> bool:
    if len(line) < 20 or line.startswith("#") or line.lower().startswith("more options"):
        return False
    if _NOT_A_CARD.search(line):
        return False
    return _CARD.search(line) is not None and _CARD_HINT.search(line) is not None


def _is_other_product(line: str) -> bool:
    return _CARD.search(line) is not None and _NOT_A_CARD.search(line) is not None and len(line) >= 20


def _skip_price_line(line: str) -> bool:
    lowered = line.lower().strip()
    return lowered.startswith("save") or lowered.startswith("was ") or lowered.startswith("you save")


def _first_price(text: str) -> int | None:
    for line in text.splitlines():
        if _skip_price_line(line):
            continue
        match = _PRICE.search(line)
        if match is None:
            continue
        cents = parse_price_cents(match.group(0))
        if cents is not None:
            return cents
    return None


def _name_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if _CARD.search(stripped) and len(stripped) >= 20 and not _NOT_A_CARD.search(stripped):
            return stripped[:180]
    return fallback


def _is_challenge(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _CHALLENGE)


def _is_empty(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _EMPTY)


def _undetermined(detail: str) -> Observation:
    return Observation(False, None, None, None, None, detail, 0)
