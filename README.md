# RTX 5090 availability watcher

A stock checker for the NVIDIA GeForce RTX 5090. It pages Nayeem only when a watched card becomes buyable at or under **$2,500**. Founders Edition MSRP is $1,999.99. A partner card that is merely "in stock" at $6,500 does not alert.

The code is a walk through [TinyFish](https://tinyfish.ai): which layer reads a page, which layer is a browser, and what each one costs. The scheduled run lives on GitHub:

https://github.com/nayeemrahman13/5090-availability-watcher

## Run it

Python 3.11 or newer. The checker itself has no dependencies. pytest is only for the tests.

```bash
git clone https://github.com/nayeemrahman13/5090-availability-watcher.git
cd 5090-availability-watcher
python3 -m venv .venv && source .venv/bin/activate
pip install pytest
export PYTHONPATH=src
export TINYFISH_API_KEY=...    # https://agent.tinyfish.ai/api-keys
export NTFY_TOPIC=...          # an unguessable ntfy.sh topic; no account
python3 -m rtx5090_watcher run-once
```

`./run-once` is the same command, for cron:

```cron
*/10 * * * * cd /path/to/5090-availability-watcher && PYTHONPATH=src TINYFISH_API_KEY=... NTFY_TOPIC=... ./run-once
```

Other commands:

```bash
PYTHONPATH=src python3 -m rtx5090_watcher estimate
PYTHONPATH=src python3 -m rtx5090_watcher demo-alert --topic your-topic
PYTHONPATH=src python3 -m rtx5090_watcher run-once --target bestbuy-fe
```

One-line live check, once a key is in the environment:

```bash
PYTHONPATH=src TINYFISH_API_KEY=your_key NTFY_TOPIC=your_topic python3 -m rtx5090_watcher run-once
```

Copy `.env.example` to `.env` and export it yourself. The process does not read `.env` files, so a key cannot sneak in from a forgotten file.

## GitHub Actions

`.github/workflows/watch.yml` is the schedule: cron `*/10 * * * *`, plus **Run workflow** in the Actions tab. Pushes to `main` run the unit tests and do not call TinyFish.

The job restores and saves `state/` with `actions/cache`, so the last reading and the day's Agent spend survive between runs. A second run waits on the `rtx5090-watch` concurrency group instead of racing the state file. GitHub sometimes starts a 10-minute cron late. The daily budget still caps spend if several runs queue up.

Add these repository secrets (Settings → Secrets and variables → Actions). The workflow will not check stock until both exist:

| Secret | What it is |
| --- | --- |
| `TINYFISH_API_KEY` | API key from https://agent.tinyfish.ai/api-keys. Sent only as the `X-API-Key` header. |
| `NTFY_TOPIC` | ntfy.sh topic. Anyone who knows the topic can read the alerts, so make it long. |

Optional, locally or as variables: `AGENT_BUDGET_PER_RUN_USD`, `AGENT_BUDGET_PER_DAY_USD`, `NTFY_SERVER`.

## Architecture

```
run-once
  │
  │  POST https://api.fetch.tinyfish.ai
  │  one batch, format=markdown, ttl=0
  ▼
parse the markdown
  │
  ├─ price and stock are both decided ──► stop. This call costs $0.
  │
  └─ blocked, empty, or ambiguous, and the target's tier allows it
       │
       │  POST https://agent.tinyfish.ai/v1/automation/run-async
       │  stealth browser, US proxy, output_schema, 75s cap
       │  GET  /v1/runs/{id}  until the run finishes
       │  POST /v1/runs/{id}/cancel  if the cap passes
       ▼
in stock AND price <= the target's max (default $2,500)
  │
  └─ only when that flips from false to true
       POST https://ntfy.sh/<NTFY_TOPIC>
```

Each piece uses one TinyFish layer, for a reason.

**Fetch** is the reader. `POST https://api.fetch.tinyfish.ai` returns markdown (or an error code per URL on HTTP 200). `ttl: 0` asks for a live copy, because a cached product page would report yesterday's stock. The daily allowance is 1,000 URLs, and the published price inside it is $0. On 13 Nov 2026 Fetch read Newegg's in-stock search (`N=4131`) and returned `bot_blocked` for the B&H PNY 5090 page. The same day's checks returned `target_unreachable` for the Best Buy Founders Edition page and `bot_blocked` for the NVIDIA marketplace.

**Agent** is the next layer when Fetch cannot decide. The endpoint is `/v1/automation/run-async`, then `GET /v1/runs/{id}`. Synchronous `/run` blocks until the end and cannot be cancelled, so a budget guard would have nothing to stop. The request uses the documented body: `browser_profile: "stealth"`, `proxy_config` enabled with `type: "tinyfish"` and `country_code: "US"`, an `output_schema`, and `agent_config.max_duration_seconds`. That stealth-plus-US-proxy combination is what read the Best Buy Founders Edition page on 13 Nov 2026 (about 4 steps, `$1,999.99`, button "Out of Stock"). The goal forbids Add to Cart, Buy, Checkout, and Sign in, and forbids typing into a form. `use_vault` and `use_profile` are left off so a run cannot log in. `mode: "strict"` is sent first; it is a beta flag and a non-enrolled account gets HTTP 403, so the client retries the same goal once without `mode`. `max_steps` is the same kind of beta flag and is never sent. The duration cap is what actually stops the browser.

**Search** is the layer for finding a URL. These five URLs are already known, so Search is not called.

**Browser** is a CDP session at $0.002 per minute, for a caller that wants to drive the clicks. This watcher must not click, so it never opens one.

**Monitor** is TinyFish's hosted watcher: $0.005 per check, email or webhook, failed checks free. It answers "did this page change?" The question here is "is there a graphics card, not a whole PC, in stock at or under $2,500?" on sites where Fetch often cannot see the page. That decision stays in this program. Tiers are per target in `targets.json`: `fetch`, `agent`, or `fetch-then-agent`. A 404 (`page_not_found`) does not escalate. A bot wall, a timeout, or a page that never says whether you can buy the card does.

The parser treats Fetch markdown and the Agent's schema result as the same kind of reading. Newegg bolds prices as `$**7,999**.00` and mixes full desktops into an RTX 5090 search. Listings whose titles are a gaming PC or a laptop are dropped. On the Newegg URL the In Stock filter is already applied, so a priced graphics card counts as in stock unless that listing says out of stock. "More options from $6,549" is ignored; the primary price is the one on the listing. A `$49.99` figure is ignored so a shipping line cannot clear the threshold.

## State and alerts

`state/watcher.json` holds the last reading per target and the Agent spend for the UTC day. The alert is the rising edge of "in stock and at or under the max." The first time a run sees that, it counts as a transition. Staying in stock does not alert again. Selling out and coming back does. If ntfy refuses the push, or `NTFY_TOPIC` is unset, the flag stays false and the next run tries again.

The push is one HTTP POST. The body is product, price, retailer, and link. The `Title` header is the headline and `Click` is the product URL.

## Cost per day

Prices below are from https://www.tinyfish.ai/pricing as checked on 13 Nov 2026. `python3 -m rtx5090_watcher estimate` prints the same arithmetic the budget guard uses.

| Layer | Published price |
| --- | --- |
| Fetch | $0.00 per URL, 1,000 URLs/day, 150/min |
| Search | $0.00 per request, 30/min |
| Agent | $0.016 per step |
| Browser | $0.002 per minute |
| Monitor | $0.005 per check |

Every 10 minutes is 144 runs a day.

- 5 Fetch URLs × 144 = **720 URLs/day**, under the 1,000 URL allowance, so Fetch is **$0.00**. Headroom is 280 URLs. The Fetch reference also describes HTTP 402 once that allowance is gone, while the pricing FAQ says Fetch keeps working at a $0 wallet balance. This schedule stays under the allowance either way.
- Newegg was readable by Fetch, so the realistic sketch is the other **4 targets escalating**. The Best Buy reading took **4 steps**. 4 × 4 × $0.016 = **$0.256 per run**.
- With no guard, 144 × $0.256 = **$36.864 per day**. That is why the guard exists.
- Each Agent call reserves 8 steps ($0.128) before it starts, then charges the real `num_of_steps` from `GET /v1/runs/{id}`. The per-run budget is **$0.60** (one pass of those 4 targets fits). The per-day budget is **$2.00**.
- At 4 actual steps the day budget allows **30 Agent calls**, then the rest of the UTC day is Fetch only. Capped Agent spend is 30 × $0.064 = **$1.92 per day**.
- The same five URLs on Monitor, same cadence, would be 5 × 144 × $0.005 = **$3.60 per day**, without the price rule.

A full day of Agent coverage for the 4 blocked targets is the $36.86 figure. Raise `agent_per_day_usd` in `targets.json`, or set `AGENT_BUDGET_PER_DAY_USD`, when you want that. Agent runs also refuse to start when the wallet is empty (HTTP 402). New accounts start with $8 in wallet funds, which is 500 steps: a few days of the capped schedule, and a few hours of the uncapped one.

## What I learned about TinyFish

TinyFish is several products behind one key. Search, Fetch, Agent, Browser, Research, and Monitor share the `X-API-Key` header and, for the paid ones, a prepaid wallet. Search and Fetch are the free layer. Agent and Browser draw the wallet down. Monitor and Research are priced per finished result and sit on top of those APIs.

"Cheapest layer that works" is a property of the site. The same Fetch call that returned Newegg's listings as markdown got a bot wall from B&H and, in the 13 Nov checks, could not open Best Buy or the NVIDIA marketplace. Those failures are structured (`bot_blocked`, `target_unreachable`) on an HTTP 200. Retrying Fetch is asking the same door again. The next door is a different API: a browser with a stealth profile and a US proxy, given a goal and a schema, which is what actually read the Best Buy button.

The schema is a contract, and it is narrower than JSON Schema. `additionalProperties`, `oneOf`, and a type array like `["string", "null"]` are rejected before the browser starts. Nullable fields use `nullable: true`. The top level has to be an object. When the prompt and the schema disagree, the schema wins, so the price is requested as the on-screen string `"$1,999.99"` and parsed here. `COMPLETED` means the run ended, not that the goal succeeded. A result that still says "captcha" is treated as unread.

Two fields that look ordinary are beta. `agent_config.mode` and `agent_config.max_steps` return 403 unless the account has enrolled, and the run does not start. `max_duration_seconds` is generally available, which is why the cap is a clock and a cancel call on `/run-async`, not a step limit. Synchronous `/run` cannot be cancelled at all.

The bill is per step, $0.016, so a vague goal is expensive and a 75 second read-only goal is the control TinyFish will enforce. The $0.60 and $2.00 caps are the control TinyFish will not enforce: it will run every ten minutes for as long as the wallet has funds. Pay-as-you-go also starts at 2 concurrent Agent runs, which is another reason the checks go one target at a time. The budget guard sees each `num_of_steps` before it starts the next browser.

Monitor is the cheaper product and the wrong question. Five cents a check beats an Agent step, and a failed check is free, but a page-diff still cannot see a bot-walled Best Buy button and does not know that a $7,999 listing is in stock and also irrelevant.

## Tests

```bash
pip install pytest
PYTHONPATH=src pytest
```

The tests cover markdown parsing (including Newegg's bold prices and the desktop-PC filter), the $2,500 threshold, alert transitions and a failed push, Fetch-then-Agent escalation against mocked responses, the REST request body (stealth, US proxy, schema, no `max_steps`, strict-mode 403 retry, cancel), and the cost numbers above. They do not call TinyFish.

## Layout

```
targets.json                  retailers, tiers, the $2,500 cap, Agent budgets
run-once                      local cron entrypoint
src/rtx5090_watcher/
  tinyfish.py                 Fetch and Agent REST client
  parse.py                    markdown or schema result → one reading
  decide.py                   threshold, and whether to escalate
  goals.py                    the read-only Agent goal
  check.py                    one pass over the targets
  state.py                    last reading and the UTC-day ledger
  notify.py                   ntfy.sh push
  pricing.py                  published rates and the budget guard
  cli.py                      run-once, demo-alert, estimate
tests/
.github/workflows/watch.yml   cron and the Actions secrets
```
