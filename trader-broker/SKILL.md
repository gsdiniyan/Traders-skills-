---
name: trader-broker
description: >-
  Order Ticket Desk for NSE day trades. Never places an order. Shows each order that
  trader-risk passed as a plain ticket (a bracket shows its stop-loss, target and the max
  loss at the stop), with the exact steps to type into your own broker app (Dhan or any
  other) and the same order as Dhan API v2 fields for your own script. Records your yes
  for ONE order word for word, then what you did (placed, skipped, failed), records fills,
  and builds the sell ticket that closes a day trade by your flat-by time. Logs each
  trade's max loss so trader-risk can hold the daily limit. Use when the user says "place
  the order", "buy it", "execute", "give me the ticket", "close my position", "flatten",
  "get me out", "paper trade it", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker (India)

The ticket desk is the last step before the user's own hands. This pack never connects to a
broker and never places an order: the user places it in their own broker app, or paper trades
it by writing it in the log. The skill makes sure that nothing reaches that point unless two
things hold, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.

No PASS, no ticket. No yes, no record that it was taken.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 332 --filled-price 225.75
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol ONGC --side sell --qty 332 --price 227.00
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol ONGC --qty 332
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` has no network code. It renders tickets, checks that a reply is a real yes for that
order, builds the flatten ticket, and keeps the log in `trader/state/broker.json`.
`--now "2026-10-23 10:15"` pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, skipped, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/config.json` | `broker` (dhan, zerodha, upstox, angel, groww: only changes the wording), `paper`, `exchange`, `product`, `flat_by` |

## The ticket

```
ORDER 1 of 1, PAPER trade (nothing is placed anywhere), Dhan
  BUY 332 ONGC, market, intraday (MIS), good for today
  stop-loss 225.10
  take-profit 227.00
  max loss about Rs 212.48 if the stop fills at 225.10 (it can fill worse in a fast market)
  READY: trader-risk PASS, waiting for your yes
  In the Dhan app (or your broker's): BUY | NSE | ONGC | intraday (MIS) | qty 332 | market | validity DAY
  Stop-loss 225.10 and target 227.00: use the broker's order that carries both with the entry ...
  The same order as fields (Dhan API v2 names, for your own script; securityId not included): { ... }
```

A bracket in India: Dhan calls it a Super Order, other brokers a bracket or OCO order. Check what your broker
offers for the stock, because some brokers have stopped bracket orders for equity. If yours does not have one,
place the entry, and place the stop-loss order the moment it fills. The fields block is a mapping for a user
who writes their own script. It is not tested against a live account and Dhan changes its API: read the
current docs. `securityId` comes from Dhan's scrip master and is not known here.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT READY or BLOCKED goes back to
   `/trader-risk`.
2. Show ONE ticket and ask: "Take this trade? Reply yes or no." One order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`. If it prints "Not recorded", the
   reply was not an explicit yes for this order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. After "Recorded your yes", tell the user to place it themselves from the ticket (live), or that it is logged as a
   paper trade. Do not do it for them and never ask for broker keys.
5. When the user says what happened, record it: `ticket.py result --index N --status placed --broker-id <id>
   --filled-qty Q --filled-price P` (they read the fill off their broker app), `--status skipped`, or
   `--status failed` with `--note`. A failed order is not retried without a new check and a new yes. The log keeps
   the entry's max loss for trader-risk's daily limit.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time (`alerts.py add --from-plan`). The stop-loss and
  target orders at the broker do the stopping.
- When the stop-loss or the target fills, the user tells you and you record it:
  `ticket.py fill --symbol ONGC --side sell --qty 332 --price 227.00`.
- By `flat_by` (15:15 IST), anything still open gets closed. Brokers square off intraday positions by themselves from
  about 15:15 to 15:25 and charge for it, so closing it first is cheaper and gives the user the price:
  1. The user reads the number of shares held off their broker's positions page.
  2. `ticket.py flatten --symbol ONGC --qty 332` writes a market sell for those shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. The entry's stop-loss and target orders still hold those shares, so the broker can reject a new sell until
     the user cancels them in the app.

## What it never does

- Never places an order, never connects to a broker, never reads an API key. There is no network code in it.
- Never marks an order taken without a yes for that one order, and never two orders on one yes.
- Never runs from a scheduled or background task: nobody is there to say yes.
- Never switches `"paper"` to live. The user does that in their own config.
- Never promises a fill price: a market order and a stop fill at whatever the market gives. A stock at its circuit
  limit may not fill at all.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
