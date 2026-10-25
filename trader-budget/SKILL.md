---
name: trader-budget
description: >-
  Budget Manager for NSE day trades. Sizes every position to your budget and your
  max loss per trade, in rupees. Reads the trade plan from trader-exit (entry, stop,
  target), finds the whole shares that fit inside four limits (max loss per trade,
  position cap, max order, cash after your buffer), says which limit decided it, adds
  the intraday charges (brokerage, STT, exchange fees, stamp duty, GST) and the price
  the trade needs just to break even, and writes ONE order with a stop-loss and a
  target to trader/orders.json. A stop too far away for your max loss is a plain
  refusal, not a smaller order. Nothing is placed. Use when the user says "how many
  shares", "size this trade", "how much should I buy", "I have Rs 1 lakh", "I only
  want to risk Rs 1,000 a trade", "set my rules", "keep 10% in cash", "what will the
  charges be", or after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget (India)

The Budget Manager turns a plan ("buy ONGC at 225.74, stop 225.10") into an exact
order ("buy 332 shares"), inside rules the user sets once. The stop decides the size:
the further away the stop, the fewer shares. It writes a proposal only. trader-risk
checks it and trader-broker prepares a ticket for the user to place. Day trading loses
most people money. Sizing to a max loss keeps each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 200000        # override the budget
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit                # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 225.70 --write
python3 "${CLAUDE_SKILL_DIR}/size.py" --lot-size 1700        # round down to whole lots (stock futures)
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=40,TCS=30,INFY=20,ITC=10 --budget 500000 --whole-shares
```

Other flags: `--prices FILE` (default `trader/prices.json` if it exists), `--portfolio FILE`
(your cash caps the size), `--now "2026-10-23 10:06"` (IST), `--json`.

## The rules file

`trader/config.json` (copy `config.example.json`). Amounts are in rupees:

| key | meaning | example |
| --- | --- | --- |
| `budget` | the money you trade with | 100000 |
| `cash_buffer_pct` | stays in cash, never invested | 10 |
| `max_position_pct` | one position's cap, percent of the budget | 75 |
| `max_order_inr`, `min_order_inr` | biggest and smallest order | 75000, 500 |
| `max_loss_per_trade_inr` | the most one trade may lose at its stop | 1000 |
| `max_daily_loss_inr` | stop trading for the day at this loss (trader-risk enforces it) | 3000 |
| `max_trades_per_day`, `max_orders_per_day` | counts (trader-risk) | 3, 10 |
| `min_reward_risk` | below this, no order | 1.0 |
| `no_entries_before`, `no_entries_after`, `flat_by` | IST times | 09:30, 14:30, 15:15 |
| `tick_size` | the NSE price tick | 0.05 |
| `exchange`, `product` | NSE and INTRADAY (delivery is DELIVERY) | NSE, INTRADAY |
| `broker`, `paper` | dhan or your own; paper stays true | dhan, true |
| `allow_options`, `allow_margin`, `allow_short` | off unless you switch them on | false |
| `charges` | the intraday charge rates, see below | |

## How many shares

Whole shares only (the cash market has no fractions). The size is the smallest of four:

1. **max loss**: `max_loss_per_trade_inr` divided by the risk a share (entry minus stop).
2. **position cap**: `budget x max_position_pct / entry`.
3. **max order**: `max_order_inr / entry`.
4. **cash**: `(budget - cash buffer) / entry`, and no more than the cash in `trader/portfolio.json` if there is one.

It says which limit decided ("<- binds"). A market order is sized at the plan's entry, or at the
price in `trader/prices.json` if that is fresh (under 15 minutes old) and higher: a price that ran up
since the plan means a bigger loss at the same stop. A limit order is sized at its limit, on the price tick.
Zero shares (the stop is too far for your max loss) and a reward:risk under your minimum are plain
refusals: no order is written. `--lot-size N` rounds the quantity down to whole lots.

## Charges

Costs are real in India and sit on top of the plan. For an intraday equity trade the report uses:

| charge | rate |
| --- | --- |
| brokerage | Rs 20 or 0.03 % of the order, whichever is lower, each way |
| STT | 0.025 % of the sale |
| NSE transaction charge | 0.00297 % of both sides |
| SEBI fee | 0.0001 % of both sides |
| stamp duty | 0.003 % of the purchase |
| GST | 18 % of brokerage, transaction charge and SEBI fee |

These are the usual rates at a discount broker and they change: set your own under `"charges"` in the
config, or check a contract note. The report adds the charges to the loss at the stop, takes them off the
gain at the target, and prints the price the trade must reach just to break even. When the charges are
15 % or more of the loss at the stop it warns: a small stop on a small order is mostly cost.

## What it writes

`trader/orders.json`: `created_by`, `status: proposed`, `paper`, `broker`, `orders[]`. One order:
`symbol`, `exchange`, `side`, `qty`, `type` (market or limit), `product`, `time_in_force`, `limit_price`
(limit only), `order_class: bracket` (entry with a stop-loss and a target), `take_profit_limit_price`,
`stop_loss_stop_price`, `why`, and a `plan` block (entry, stop, target, risk a share, max loss, charges at the
stop, break-even price). trader-risk reads it, then trader-broker turns it into a ticket.

## Weights mode

An investing helper, not the headline: `size.py --weights RELIANCE=40,TCS=30,INFY=20,ITC=10 --budget 500000
--whole-shares` splits a budget across stocks by weight (cash buffer first, a cap per stock, small slices
dropped, whole shares, the rounding rupee to the biggest uncapped stock) and writes delivery orders. They
carry no stop, so trader-risk blocks them while `require_stop` is true.

## Steps

1. Make sure `trader/config.json` exists. If not, ask the user three things (budget, max loss per
   trade, max daily loss), write it, and say it is theirs to change.
2. Run `size.py --write`. Tell the user the size, which limit decided it, the cost, the max loss at the
   stop, the charges and the break-even price.
3. If it refuses, say why in one line and offer the fix (a closer stop, a bigger budget, skip the trade).
4. Hand off to `/trader-risk`, then `/trader-broker`.

## Example: ONGC, Friday 2026-10-23

Budget Rs 1,00,000, max loss Rs 1,000 a trade, the plan from `/trader-exit` (entry 225.74, stop 225.10, target 227.00):

```
Limits (whole shares; the smallest one wins):
  max loss      Rs 1,000 max loss / 0.64 risk a share                      1562 shares
  position cap  75% of Rs 1,00,000 = Rs 75,000 / 225.74                    332 shares  <- binds
  max order     Rs 75,000 / 225.74                                         332 shares
  cash          budget Rs 1,00,000 minus the Rs 10,000 cash buffer = Rs 90,000 / 225.74 398 shares
Size: 332 shares of ONGC = Rs 74,945.68 at 225.74. Max loss at the stop: 332 x 0.64 = Rs 212.48.
Charges (intraday round trip): Rs 73.55 if it stops out, Rs 73.73 if it hits the target ...
After charges: lose Rs 286.03 at the stop, make Rs 344.59 at the target (reward:risk 1.20). It needs to reach 225.96 (+0.10%) just to break even.
Warning: charges are 35% of the loss at the stop. A small stop on a small order is mostly cost.
```

The Rs 1,000 max loss would allow 1,562 shares, but the position cap holds it to 332, so the real risk is
Rs 212 plus Rs 74 of charges. The charges eat a third of that loss and 18 % of the gain: tight stops on
quiet stocks are expensive here.

## Rules

- Say the size in shares and rupees, the limit that decided it, and the money at risk.
- Always show the charges and the break-even price with the size.
- A refusal is an answer. Never shrink an order to fit a stop that is too far away.
- Never raise the user's max loss to make an order fit. Offer a closer stop instead.
- Never place anything. The order is a proposal.

## What it never does

It never places an order, never picks a stock, never raises a limit by itself, and never turns a
refusal into a smaller order.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
