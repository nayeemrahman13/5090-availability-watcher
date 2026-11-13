"""The Agent goal is a read-only contract.

TinyFish will click, type, and sign in if the goal asks it to. This one does
not. Stock is visible without a cart and without an account.
"""

from __future__ import annotations

from rtx5090_watcher.models import Target


def stock_goal(target: Target) -> str:
    if target.page_kind == "product":
        kind = "This URL is already the product page. Read the price and availability on this page."
    else:
        kind = (
            "This URL is already the search or marketplace page. "
            "Read the price and availability of the cheapest RTX 5090 graphics card visible on this page."
        )
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
    return f"""Read the price and availability, fill the schema, and stop immediately.

{kind}
Retailer: {target.retailer}
Product we care about: {target.product_name}

Return these fields and nothing else:
- product: the graphics card name shown on the page
- price: the on-screen price string, like "$1,999.99", or null if no card price is visible
- button_state: the purchase button label, like "Add to Cart" or "Out of Stock"
- in_stock: true only when that button means the card can be bought right now
- listing_url: null

Stop as soon as those fields are filled. Do not navigate. Do not search. Do not open another page.
Do not click Add to Cart, Buy, Checkout, or Sign in. Do not open the cart.
Do not type into any form. Do not change quantity. Do not open a chat widget.
Ignore full desktop PCs, laptops, and bundles. A graphics card only.

{stock_hint}
"""
