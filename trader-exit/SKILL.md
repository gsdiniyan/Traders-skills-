---
name: trader-exit
description: >-
  Exit Planner for NSE stocks. Sets the stop and the target BEFORE you enter,
  so you know when to get out. Reads trader/entry.json (or your numbers) and
  writes trader/plan.json: a stop (the opening-range midpoint by default, or the
  range low, a price, a percent, or an ATR multiple), a target (2R by default,
  or 1R, 1.5R, a price), the risk a share, the reward-to-risk, and a flat-by time
  (15:15 IST) so nothing is held overnight. Stops and targets land on the NSE
  price tick (Rs 0.05). Long side only. It never places an order. Use when the
  user says "set my stop", "where is my target", "plan the exit", "stop loss",
  "risk reward", "when do I get out", or after /trader-entry triggers and before
  /trader-timemachine and /trader-budget.
argument-hint: "[plan | show] [--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R]"
---

# trader-exit (India)

The Exit Planner decides where you get out before you get in. A stop is the
price at which you admit the trade is wrong; a target is where you take the
profit; flat-by is the time you leave whatever happens. Write them down first
and the trade becomes a plan instead of a mood. It never places anything. Day
trading loses most people money, and a good plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # from trader/entry.json: or-mid stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol ONGC --entry 225.74 --or-high 225.62 --or-low 224.61
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --offline ONGC.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1450.50 --setup level --stop pct:1
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--from PATH` (an entry.json), `--time 10:05`, `--entry-bar N`,
`--date YYYY-MM-DD`, `--flat-by 14:45`, `--tick 0.05`, `--json`, `--now "2026-10-23
10:06"` (IST). `--write` saves `trader/plan.json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | from `/trader-entry`: symbol, price, time, bar, opening range. Must say `triggered` unless you give `--entry` |
| `trader/config.json` | `flat_by` (15:15), `min_reward_risk` (1.0, a warning here; `/trader-risk` enforces it) and `tick_size` (0.05). Optional |
| `trader/plan.json` | written by `plan --write`: entry, stop, target, risk a share, reward:risk, flat_by, tick_size |

## The rules

- **The stop** is a long's stop, so it sits under the entry and rounds DOWN to the
  price tick, away from the entry. Choices: `or-mid` (the opening-range midpoint,
  the default for an orb entry), `or-low`, `price:X`, `pct:X` (X percent under the
  entry), `atr:K` (K times the 14-bar ATR on the session's bars up to the entry; with
  fewer than 14 bars it averages all of them and says so). A level setup has no
  opening-range default: give a stop.
- **The target** is `2R` by default: entry plus two times the risk, where risk is
  entry minus stop. `1R`, `1.5R`, or `price:X`. It rounds half up to the tick.
- **The tick.** The NSE tick for most stocks is Rs 0.05, so every stop and target is a
  multiple of 0.05 and can be typed into an order as it stands. A few stocks trade in
  paise: set `tick_size` to 0.01 in the config (or pass `--tick 0.01`).
- **Refused, with the reason:** a stop at or above the entry; a target at or below it;
  a risk under 0.05% of the price or under one tick (too tight to survive the spread);
  an entry.json that has not triggered; a short.
- **Flat by.** 15:15 IST by default. Brokers square off intraday (MIS) positions from
  about 15:15 to 15:25 and the market closes at 15:30, so the plan leaves before that.
  An entry at or after the flat-by time is refused: there is no time to hold it.
- **Money.** Rupees with Indian grouping (Rs 1,23,456.50), exact to the paisa (Decimal,
  not floats). Raw Yahoo floats are rounded to the paisa first.

## Steps

1. Run after `/trader-entry` says TRIGGERED: `exit.py plan --write`. Show the user the
   stop, the target, the risk a share (and its percent of the entry), the reward:risk and
   the flat-by time.
2. If the user wants a different stop, rerun with `--stop` (and `--target`). Never move a
   stop further away after the entry: say that.
3. Mention any warning the tool prints (reward:risk under the config's minimum, a plan for
   another date).
4. Hand off: `/trader-timemachine` replays the plan, then `/trader-budget` sizes it to the
   user's max loss. Nothing is bought here.

## Example: ONGC, Friday 2026-10-23

The entry triggered at 10:05 at Rs 225.74; the opening range is Rs 224.61 to Rs 225.62.

```
Exit plan: ONGC long, Fri 2026-10-23, opening-range breakout on 5-minute bars
  Entry         Rs 225.74 at 10:05 (bar 9)
  Stop          Rs 225.10  opening-range midpoint: (Rs 225.62 + Rs 224.61) / 2 = Rs 225.115, rounded down to the tick
  Target        Rs 227.00  2R (two times the risk)
  Risk a share  Rs 0.64 (0.28% of the entry)
  Reward:risk   1.97
  Flat by       15:15 IST, win or lose: no overnight hold
Stop and target are on the Rs 0.05 price tick.
```

The midpoint is Rs 225.115, which is not a price the NSE trades at, so the stop rounds
down to Rs 225.10; the target 2R is Rs 227.02, which rounds to Rs 227.00. Reward:risk is
therefore 1.97, not exactly 2.

## Rules

- Say the plan in numbers: stop, target, risk a share, reward:risk, flat-by.
- A stop is a plan, not a guarantee: a gap or a fast market can fill past it. Say so.
- Long side only. If the user wants a short, say the pack plans longs only.
- Never move a stop further away from the entry.
- Never print a P&L or a promised profit.

## What it never does

It never places an order, never sizes a position (that is `/trader-budget`), never checks
the order against your limits (that is `/trader-risk`), and never moves a stop for you.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
