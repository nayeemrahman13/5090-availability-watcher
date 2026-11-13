"""RTX 5090 stock watcher.

The package is a walk through TinyFish's layers, not a scraping script
with an HTTP call hidden at the bottom.

Search   find a URL you do not already have. This watcher does not use it.
Fetch    read a URL as markdown. Free inside the daily allowance. Tried first.
Agent    a read-only browser goal when Fetch is blocked or cannot decide.
Browser  a raw CDP session. Wrong layer: we refuse to click.
Monitor  TinyFish's hosted "tell me when this page changes". We schedule
         ourselves because the alert rule is "in stock and at or under $2,500",
         which a page-diff does not know.
"""

__version__ = "1.0.0"
