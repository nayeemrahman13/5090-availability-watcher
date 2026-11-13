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
       │  stealth browser, US proxy, output_schema, 180s cap, strict mode off
       │  every escalation the budget has reserved starts together
       │  GET  /v1/runs/{id}  until the run finishes or that same 180s elapses
       │  POST /v1/runs/{id}/cancel  if that clock elapses
       ▼
in stock AND price <= the target's max (default $2,500)
  │
  └─ only when that flips from false to true
       POST https://ntfy.sh/<NTFY_TOPIC>
```

Each piece uses one TinyFish layer, for a reason.

**Fetch** is the reader. `POST https://api.fetch.tinyfish.ai` returns markdown (or an error code per URL on HTTP 200). `ttl: 0` asks for a live copy, because a cached product page would report yesterday's stock. The daily allowance is 1,000 URLs, and the published price inside it is $0. On 13 Nov 2026 Fetch read Newegg's in-stock search (`N=4131`): 13 graphics cards in stock, cheapest $6,709.99, which is over the $2,500 cap so it does not alert. The same call returned `bot_blocked` for the B&H PNY 5090 page, `target_unreachable` for the Best Buy Founders Edition page, and `bot_blocked` for the NVIDIA marketplace.

**Agent** is the next layer when Fetch cannot decide. The endpoint is `/v1/automation/run-async`, then `GET /v1/runs/{id}`. Synchronous `/run` blocks until the end and cannot be cancelled, so a budget guard would have nothing to stop. The request uses the documented body: `browser_profile: "stealth"`, `proxy_config` enabled with `type: "tinyfish"` and `country_code: "US"`, an `output_schema`, and `agent_config.max_duration_seconds` of 180. That stealth-plus-US-proxy combination is what read the Best Buy Founders Edition page on 13 Nov 2026 when the goal was "read this product page and stop" (about 4 steps, about $0.06, `$1,999.99`, button "Out of Stock"). The goal tells the agent to fill price, button, and `in_stock` from the page it was given and stop. It forbids navigation, search, and the cart, and it forbids Add to Cart, Buy, Checkout, and Sign in. `use_vault` and `use_profile` are left off so a run cannot log in. `prefer_strict_mode` defaults off. `mode: "strict"` is beta, and on 13 Nov 2026 this account got HTTP 403 for it on every call. Turning the option on still retries that one 403 without `mode`. `max_steps` is the same kind of beta flag and is never sent. The duration cap is what actually stops the browser, and the client's poll uses that same number of seconds.

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
- What the 13 Nov 2026 live pass actually did: Newegg stopped at Fetch (13 in stock, cheapest **$6,709.99**). The other four targets escalated and each ended `Cancelled: max_duration_seconds elapsed` at **8 steps**, about **119s**, about **$0.128**, and returned no stock data. `mode: strict` returned **403** on every one of those calls first. 4 × $0.128 = **$0.512** for a pass with nothing to alert on. A separate manual Best Buy FE run, same browser settings, finished in **4 steps** (about **$0.06**) at $1,999.99 / Out of Stock.
- Newegg stays readable by Fetch, so the forward sketch is still the other **4 targets escalating**, at the **4 steps** a finished read took. 4 × 4 × $0.016 = **$0.256 per run**.
- With no guard, 144 × $0.256 = **$36.864 per day**. That is why the guard exists.
- Each Agent call reserves 8 steps ($0.128) before it starts, then charges the real `num_of_steps` from `GET /v1/runs/{id}`. Holds for a concurrent wave count before any of those runs start. The per-run budget is **$0.60** (four reservations are $0.512, so one pass fits in one wave). The per-day budget is **$2.00**.
- At 4 actual steps the day budget allows **30 Agent calls**, then the rest of the UTC day is Fetch only. Capped Agent spend is 30 × $0.064 = **$1.92 per day**.
- The same five URLs on Monitor, same cadence, would be 5 × 144 × $0.005 = **$3.60 per day**, without the price rule.

A full day of Agent coverage for the 4 blocked targets is the $36.86 figure. Raise `agent_per_day_usd` in `targets.json`, or set `AGENT_BUDGET_PER_DAY_USD`, when you want that. Agent runs also refuse to start when the wallet is empty (HTTP 402). New accounts start with $8 in wallet funds, which is 500 steps: a few days of the capped schedule, and a few hours of the uncapped one.

## What I learned about TinyFish

TinyFish is several products behind one key. Search, Fetch, Agent, Browser, Research, and Monitor share the `X-API-Key` header and, for the paid ones, a prepaid wallet. Search and Fetch are the free layer. Agent and Browser draw the wallet down. Monitor and Research are priced per finished result and sit on top of those APIs.

"Cheapest layer that works" is a property of the site. The same Fetch call that returned Newegg's listings as markdown got a bot wall from B&H and, in the 13 Nov checks, could not open Best Buy or the NVIDIA marketplace. Those failures are structured (`bot_blocked`, `target_unreachable`) on an HTTP 200. Retrying Fetch is asking the same door again. The next door is a different API: a browser with a stealth profile and a US proxy, given a goal and a schema, which is what actually read the Best Buy button.

The schema is a contract, and it is narrower than JSON Schema. `additionalProperties`, `oneOf`, and a type array like `["string", "null"]` are rejected before the browser starts. Nullable fields use `nullable: true`. The top level has to be an object. When the prompt and the schema disagree, the schema wins, so the price is requested as the on-screen string `"$1,999.99"` and parsed here. `COMPLETED` means the run ended, not that the goal succeeded. A result that still says "captcha" is treated as unread.

Two fields that look ordinary are beta. `agent_config.mode` and `agent_config.max_steps` return 403 unless the account has enrolled, and the run does not start. On 13 Nov 2026 `mode: strict` returned 403 on every escalated call, before the real run, so the default is now off and that round trip is not spent. The option remains. `max_duration_seconds` is generally available, which is why the cap is a clock and a cancel call on `/run-async`, not a step limit. Synchronous `/run` cannot be cancelled at all.

The clock has to be the clock you think it is. The server does not cut a run off mid-step: a 60 second cap in the docs ran for about 70 while the agent wrote up what it had, and cancelling from the client drops that write-up. The 13 Nov watcher sent 75 seconds, then polled until 75+15 and only compared the clock after a GET whose timeout is 30 seconds. All four escalations (bestbuy-fe, nvidia-marketplace, bestbuy-astral, bh-pny-5090) ended `Cancelled: max_duration_seconds elapsed` at 8 steps, about 119 seconds, about $0.128 each, with no price and no availability. The poll deadline is now the same `max_duration_seconds` sent in the body (180, so a 4 to 6 step read can finish), it starts when the run id arrives, and the GET timeout cannot be longer than the time still left.

The bill is per step, $0.016, so a vague goal is expensive. The goal that wandered asked the agent to choose a cheapest card and come back with that card's URL, and the agent was still navigating when the client cancelled it. The manual run that finished in about 4 steps (about $0.06, $1,999.99, Out of Stock) was the same stealth browser and US proxy pointed at the Best Buy product page with an instruction to read the price and stop. The goal now says that: read price and availability on the page you were given, fill the schema, stop. No navigation, no search, no cart.

The $0.60 and $2.00 caps are the control TinyFish will not enforce: it will run every ten minutes for as long as the wallet has funds. Pay-as-you-go starts at 2 concurrent Agent runs and queues the rest, and queue time is part of the wall clock. The 13 Nov pass ran the four browsers one after another, so the pass was four times ~119 seconds. Those runs are async, so every escalation the budget has already reserved now starts together and the pass waits out the slowest one in the wave. The guard holds an 8-step reservation ($0.128) for each of those before the wave starts, which is what keeps four concurrent runs inside $0.60 (4 × $0.128 = $0.512) and inside the day's $2.00. When a run finishes, the hold is replaced by the real `num_of_steps`. Anything that did not fit is considered again after that, so a cheap finished run can still leave room for the next target. The ledger stores the charge, not the hold.

Monitor is the cheaper product and the wrong question. Five cents a check beats an Agent step, and a failed check is free, but a page-diff still cannot see a bot-walled Best Buy button and does not know that a $7,999 listing is in stock and also irrelevant.

## Tests

```bash
pip install pytest
PYTHONPATH=src pytest
```

The tests cover markdown parsing (including Newegg's bold prices and the desktop-PC filter), the $2,500 threshold, alert transitions and a failed push, Fetch-then-Agent escalation against mocked responses, concurrent waves under the per-run and per-day holds, the REST request body (stealth, US proxy, schema, no `max_steps`, strict mode off by default, the 403 retry when it is on, a poll deadline equal to `max_duration_seconds`), and the cost numbers above. They do not call TinyFish.

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
