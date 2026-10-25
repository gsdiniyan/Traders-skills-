---
name: trader-risk
description: >-
  Risk Manager for NSE day trades. Checks every proposed order in
  trader/orders.json against the user's own limits and stops anything over them:
  max loss per trade and per day, trades and orders a day, position cap, cash
  buffer, min and max order size, stop required, reward-to-risk, the entry window
  (09:30 to 14:30 IST by default), the NSE holiday calendar, price ticks, and
  derivatives and short selling switched off by default. Each order gets PASS or
  BLOCK with the reasons, recorded with a fingerprint so trader-broker only
  prepares a ticket for an order that passed today. Exits are never blocked by the
  entry rules. Use when the user says "check my risk", "is this trade allowed", "am
  I within my limits", "did I hit my daily loss", "what is blocking this order",
  or after /trader-budget and before /trader-broker.
argument-hint: "[--now HH:MM] [--json]"
---

# trader-risk (India)

The Risk Manager is the last check before a trade goes anywhere. It does not judge whether the
trade is good. It checks that the trade is inside the limits the user wrote down when they were
calm, and says PASS or BLOCK with the reasons. Day trading loses most people money; the limits
are what keep one bad day from becoming a bad month.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                          # the trader/ files, writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-23 10:15" # pin the clock (IST)
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write               # check without recording the verdicts
```

Exit code 0 when every order passes, 1 when any is blocked.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders from `/trader-budget` |
| `trader/config.json` | the limits, in rupees (see `/trader-budget`) |
| `trader/portfolio.json` | your account: `cash` and `positions`. Without it, a fresh account funded with the budget |
| `trader/prices.json` | latest prices (from `/trader-alerts`), used only when under 15 minutes old |
| `trader/state/broker.json` | what `/trader-broker` recorded as placed today |
| `trader/holidays.json` | NSE's holiday dates, merged with the built-in 2026 list (from `scan.py holidays`) |
| `trader/state/risk.json` | written: every verdict with a fingerprint of the exact order |

## What blocks an order

Every order: **format** (no symbol or side, no qty, a limit order without a limit price, an unknown type,
validity or product, a price that is not on the price tick, a bracket that is not whole shares or misses a
leg), **asset** (futures and options contracts unless `allow_options`; anything that is not a listed NSE
share or ETF symbol), **short** (selling more than you hold unless `allow_short`).

An entry, on top: **blocked** symbols, **size** (under `min_order_inr`, over `max_order_inr`), **position** (over
`max_position_pct` of the account), **cash** (into the buffer, or needing margin), **duplicate**, **daily limit**
(`max_orders_per_day`), **stop** (none while `require_stop`, or on the wrong side), **loss** (quantity times risk a
share over `max_loss_per_trade_inr`), **reward:risk** (under `min_reward_risk`), **daily loss** (today's entries' losses at
their stops plus this one over `max_daily_loss_inr`), **trades** (`max_trades_per_day`), **window** (before
`no_entries_before`, after `no_entries_after`, or the market is closed: weekend, NSE holiday, outside 09:15 to 15:30 IST).

Prices must sit on the NSE tick (Rs 0.05 by default, `tick_size` in the config): the exchange rejects anything else,
so the check says so first and names the nearest price.

Orders are checked in file order as if each PASS had filled, so buys that are fine one by one but break a limit
together are caught. The charges at the stop (from `/trader-budget`) are shown next to the max loss, but the limits
count the loss without them.

## Entries and exits

An ENTRY opens or adds to a position. An EXIT only reduces what you hold. Getting out must always be possible, so an
exit is checked for format, asset and short sales only; every other rule becomes a warning for it.

## What it cannot see

The pack has no pattern-day-trader rule: that is a US rule and India has none. Things this check cannot see, and the
user should look at in their broker app before placing an order: whether the stock is in the trade-for-trade segment
(series BE), under ASM or GSM surveillance (no intraday leverage), or at a circuit limit (an order may not fill).

## Steps

1. Run `check.py` after `/trader-budget` has written `trader/orders.json`.
2. Say the verdict for each order in one line, and the reasons for a BLOCK in plain words. Never argue with a block.
3. A BLOCK is fixed by changing the order (a closer stop, a smaller size, a later or earlier time), or by the user
   changing their own rule on purpose. Never edit the user's limits for them to make an order pass.
4. When everything passes, hand off to `/trader-broker`.

## Example

The ONGC order from `/trader-budget` at 10:06 IST on Friday 2026-10-23:

```
PASS  #1 buy ONGC 332 shares, entry
          charges at the stop about Rs 73.55 on top of the Rs 212.48 max loss (not counted in your limits)
          entry 225.74 (the plan's entry), stop 225.10, target 227.00: risk 0.64 a share
          max loss Rs 212.48 (limit Rs 1,000), reward:risk 1.97 (min 1)
          today before this: 0 of 3 trades, Rs 0 of Rs 3,000 daily loss
```

The same order at 15:00 is blocked: `window: no new entries after 14:30 IST (it is 15:00)`. On Tuesday 20 October:
`window: the market is closed today (NSE holiday: Dussehra)`.

## Rules

- Say PASS or BLOCK first, then the reasons. A block is an answer, not a problem to route around.
- Never change a limit, a stop or a size to turn a BLOCK into a PASS.
- Say when a check could not run (no price, no portfolio) instead of passing silently.
- A PASS is a rules check, never a recommendation to trade.

## What it never does

It never places an order, never edits an order or the user's limits, and never calls a trade good.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
