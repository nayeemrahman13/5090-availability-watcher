from rtx5090_watcher.parse import (
    normalize_markdown,
    parse_agent_result,
    parse_price_cents,
    parse_product_markdown,
    parse_search_markdown,
    stock_from_text,
)

NEWEGG = """
GIGABYTE Gaming GeForce RTX 5090 32GB GDDR7 PCI Express 5.0 ATX Graphics Card GV-N5090GAMING OC-32GD

$**7,999**.00

More options from $6,549.99 - $10,999.99

STORMCRAFT Gaming PC PHANTOM AMD RYZEN 7 9800X3D NVIDIA GeForce RTX 5090 - 32GB DDR5

$**8,999**.99

MSI Ventus GeForce RTX 5090 32GB GDDR7 PCI Express 5.0 Graphics Card RTX 5090 32G VENTUS 3X OC

$**1,999**.99
"""


def test_bold_newegg_price_normalizes():
    assert normalize_markdown("$**7,999**.00") == "$7,999.00"
    assert parse_price_cents("$**6,709**.99") == 670_999


def test_price_ignores_accessories_and_parses_dollar_strings():
    assert parse_price_cents("$49.99") is None
    assert parse_price_cents("$1,999.99") == 199_999
    assert parse_price_cents(1999.99) == 199_999
    assert parse_price_cents("$20,001.00") is None


def test_stock_phrases_and_the_conflict():
    assert stock_from_text("Out of Stock") is False
    assert stock_from_text("Add to Cart") is True
    assert stock_from_text("Add to Cart\nOut of Stock") is None
    assert stock_from_text("Shipping details") is None


def test_product_page_out_of_stock_is_determined():
    text = "NVIDIA GeForce RTX 5090 Founders Edition\n$1,999.99\nOut of Stock\n"
    obs = parse_product_markdown(text, "fallback")
    assert obs.determined is True
    assert obs.in_stock is False
    assert obs.price_cents == 199_999
    assert "Founders Edition" in obs.product


def test_product_page_in_stock_skips_the_was_price():
    text = "ASUS ROG Astral GeForce RTX 5090 Graphics Card\nWas $3,499.99\n$2,499.99\nAdd to Cart\n"
    obs = parse_product_markdown(text, "fallback")
    assert obs.determined is True
    assert obs.in_stock is True
    assert obs.price_cents == 249_999


def test_product_page_without_a_button_is_not_a_guess():
    text = "NVIDIA GeForce RTX 5090 Founders Edition\n$1,999.99\nOverview and specifications\n"
    obs = parse_product_markdown(text, "fallback")
    assert obs.determined is False
    assert obs.price_cents == 199_999


def test_search_keeps_cards_drops_pcs_and_uses_the_primary_price():
    obs = parse_search_markdown(NEWEGG, in_stock_filter=True)
    assert obs.determined is True
    assert obs.in_stock is True
    assert obs.price_cents == 199_999
    assert "VENTUS" in obs.product
    assert obs.listings == 2


def test_search_without_a_stock_signal_does_not_invent_one():
    text = "MSI Ventus GeForce RTX 5090 32GB Graphics Card RTX 5090 32G VENTUS\n\n$3,299.99\n"
    obs = parse_search_markdown(text, in_stock_filter=False)
    assert obs.determined is False


def test_in_stock_filter_counts_a_priced_card_as_in_stock():
    text = "MSI Ventus GeForce RTX 5090 32GB Graphics Card RTX 5090 32G VENTUS\n\n$3,299.99\n"
    obs = parse_search_markdown(text, in_stock_filter=True)
    assert obs.determined is True
    assert obs.in_stock is True
    assert obs.price_cents == 329_999


def test_explicit_out_of_stock_beats_the_in_stock_filter():
    text = "MSI Ventus GeForce RTX 5090 32GB Graphics Card\n\nOut of Stock\n$1,999.99\n"
    obs = parse_search_markdown(text, in_stock_filter=True)
    assert obs.determined is True
    assert obs.in_stock is False


def test_empty_search_is_out_of_stock():
    obs = parse_search_markdown("We found 0 results for rtx 5090", in_stock_filter=True)
    assert obs.determined is True
    assert obs.in_stock is False


def test_challenge_page_is_undetermined():
    obs = parse_search_markdown("Just a moment... verify you are human", in_stock_filter=False)
    assert obs.determined is False


def test_agent_schema_from_the_best_buy_reading():
    obs = parse_agent_result(
        {
            "product": "NVIDIA GeForce RTX 5090 Founders Edition",
            "price": "$1,999.99",
            "button_state": "Out of Stock",
            "in_stock": False,
            "listing_url": None,
        },
        error=None,
        status="COMPLETED",
    )
    assert obs.determined is True
    assert obs.in_stock is False
    assert obs.price_cents == 199_999


def test_agent_json_string_and_blocked_result():
    import json

    raw = json.dumps(
        {"product": "card", "price": "$2,100.00", "button_state": "Add to Cart", "in_stock": True, "listing_url": None}
    )
    # The client parses JSON strings before they get here. A blocked dict does not.
    blocked = parse_agent_result(
        {"product": "captcha", "price": None, "button_state": "verify you are human", "in_stock": False, "listing_url": None},
        None,
        "COMPLETED",
    )
    assert blocked.determined is False
    assert json.loads(raw)["in_stock"] is True
