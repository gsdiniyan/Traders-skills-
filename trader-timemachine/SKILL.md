---
name: trader-timemachine
description: >-
  Time Machine for NSE stocks. Before you risk a rupee it replays a planned day
  trade 10,000 times: each replay walks the rest of the session from your entry
  using real 5-minute bars the stock itself printed over its last 50 or so full
  sessions (Yahoo, free), and reports how often the target is hit before the
  stop, how often neither, the price at your flat-by time, and an honest verdict
  (coin flip, leans toward the stop or the target). It also re-runs the plan's own
  opening-range rule on every one of those past sessions. Reads trader/plan.json
  from /trader-exit, writes trader/timemachine.json, and draws an ASCII fan chart.
  Same plan, same data and same seed give the same numbers. Use when the user
  says "replay this trade", "what are the odds", "backtest my plan", "simulate the
  rest of the day", "would this work", "time machine", or after /trader-exit and
  before /trader-budget.
argument-hint: "[run | chart] [--seed N] [--until HH:MM]"
---

# trader-timemachine (India)

The Time Machine answers one question before you trade: if this plan were played
many times over days like the recent ones, what usually happens first, the target
or the stop? It does not predict. It re-draws the stock's own recent five-minute
bars, 10,000 times, and counts. Most day traders lose money, and a plan that looks
fine here can still lose; the replay's job is to catch the plans that look bad.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                   # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write           # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 14:45     # end each replay earlier than the plan's flat-by
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --seed 7 --replays 20000 --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol ONGC --date 2026-10-23 --entry 225.74 \
    --stop 225.10 --target 227.00 --entry-time 10:05
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart                 # ASCII fan chart of the replays
```

Other flags: `--window N` (draw from the same time of day plus or minus N bars,
default 2), `--tick 0.05` (the price tick for the history replay), `--offline FILE`
(a saved Yahoo chart, `.json` or `.json.gz`, or a folder holding
`<SYMBOL>-60d5m.json.gz`), `--now "2026-10-25 12:00"` (IST), `--json`.

`--vol-scale 0.5` rescales each past session to today's volatility so far before drawing from it
(a calm past day is stretched, a wild one is shrunk, never beyond x0.5 to x2). It is off by default. On 393
scanner signals of 5-23 Oct 2026 it cut the 3-way Brier score from 0.598 to 0.555 on days not used to pick the
setting, and brought the share of "neither" closer to what happened. One sample of 15 days: treat it as a tested
default, not a proof.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | from `/trader-exit`: symbol, date, entry, entry_time or entry_bar, stop, target, opening range, `flat_by`, `tick_size` |
| Yahoo chart API | `range=60d&interval=5m`, one request, NSE stocks as `SYMBOL.NS`: free, unofficial, can lag |
| `trader/timemachine.json` | with `run --write`: the plan, the odds, the bands for the chart, the history replay, the verdict |

## How it works

1. **The data.** 60 days of 5-minute bars. Only FULL sessions before the plan's day
   count. Yahoo's NSE history leaves the 15:20 and 15:25 bars empty on almost every past
   day, so a session counts as full when every bar from 09:15 to the 15:15 bar (73 bars)
   is there; a session with a gap, a Muhurat session and today are left out and named.
   About 50 sessions usually qualify; fewer than 20 and it refuses.
2. **The pool.** For every bar of the day, each past session's bar as ratios to the previous
   bar's close (open, high, low, close).
3. **A replay** starts at your entry and walks the rest of the session: for each bar it draws
   one past bar from the same time of day (plus or minus `--window` bars) and scales it to the
   current price. The stop is checked before the target in every bar, so a bar that touches
   both counts as the stop (the conservative call).
4. **Outcomes:** target first, stop first, or neither (still open at `--until`, which defaults
   to the plan's flat-by, 15:15). Average R: the plan's R at the target, minus one R at the stop,
   otherwise the move to `--until` in units of risk. A gap through the stop still counts minus
   one R here; a real fill can be worse.
5. **History.** The plan's own opening-range rule (same range, stop rule, target) re-run on
   every one of those sessions: the first close beyond the range is the entry, with stops and
   targets on the price tick.
6. **The verdict** compares the target's share of the replays that hit one side with the share
   that breaks even at the plan's R: 50 % at 1R, 33 % at 2R. Under 5 points apart is a coin flip
   (1R) or break-even; 5 to 15 leans; 15 or more is strongly one way.

The numbers leave out brokerage, STT and the other charges: `/trader-budget` prints them.

## Steps

1. Run after `/trader-exit` has written `trader/plan.json`: `timemachine.py run --write`.
2. Tell the user, in plain words: target first %, stop first %, neither %, the price range at the
   end, the history line (how many times the rule fired and how it ended), and the verdict line.
3. Say what it is not: not a prediction; results, news, circuit limits and halts can do what the
   last 60 days never did.
4. If the verdict leans toward the stop, say so first. Offer a wider stop, a smaller target, or
   passing on the trade. Never talk the user into the trade.
5. Hand off to `/trader-budget`.

## Example: ONGC, Friday 2026-10-23

Plan: entry Rs 225.74 at 10:05, stop Rs 225.10, target Rs 227.00 (1.97R). Real ONGC bars, 51 full
sessions from 2026-07-29 to 2026-10-22:

```
  target first   21.3 %
  stop first     76.0 %
  neither        2.6 %    still open at 15:15
  Price at 15:15: median 225.29, 90 % of replays between 222.50 and 228.20.
History: ... fired 19 times: 3 target, 11 stop, 5 neither. Average R -0.228.
Leans toward the stop: at 1.97R the target has to come first in 34 % of the replays that hit one side; it did in 22 %.
```

A tight stop of Rs 0.64 on a stock that moves that far in a few bars gets hit far more often than the
target. That is the replay doing its job: this plan needed a wider stop or a different trade.

## Rules

- Say the odds as odds: "target first in 21 of 100 replays", never "this will win" or "this will lose".
- Always give the verdict line and the history line together.
- Say the sample size (how many full sessions) and the data's last bar.
- Never print a profit figure or a promise. Never hide the stop-first number.

## What it never does

It never places an order, never sizes a position, never changes the plan, and never calls a simulation
a prediction.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
