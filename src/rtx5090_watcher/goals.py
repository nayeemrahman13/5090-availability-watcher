"""The Agent goal is a read-only contract.

TinyFish will click, type, and sign in if the goal asks it to. This one does
not. Stock is visible without a cart and without an account.
"""

from __future__ import annotations

from rtx5090_watcher.models import Target


def stock_goal(target: Target) -> str:
    if target.page_kind == "product":
        kind = "This URL is a single product page."
    else:
        kind = "This URL is a search or marketplace page that lists many products."
    if target.in_stock_filter:
        stock_hint = (
            "The page is already filtered to in-stock items. Still set in_stock "
            "false when the card itself says out of stock, sold out, or coming soon."
        )
    else:
        stock_hint = (
            "Treat Add to Cart as in stock. Treat Out of Stock, Sold Out, "
            "Coming Soon, and Notify Me as not in stock."
        )
    return f"""Read this page and report whether a person can buy an NVIDIA GeForce RTX 5090 graphics card right now, and at what price.

{kind}
Retailer: {target.retailer}
Product we care about: {target.product_name}

Rules:
- Read only. Do not click Add to Cart, Buy, Checkout, or Sign in.
- Do not type into any form. Do not change quantity. Do not open a chat widget.
- Ignore full desktop PCs, laptops, and bundles. A graphics card only.
- On a search page, choose the cheapest in-stock RTX 5090 graphics card.
- If nothing is purchasable, set in_stock to false. Put the lowest visible card price in price if one is on the page, otherwise null.
- price is the on-screen price string, like "$1,999.99".
- button_state is the purchase button label, like "Add to Cart" or "Out of Stock".
- listing_url is the product page for the card you priced, or null if you are already on that page.

{stock_hint}
"""
