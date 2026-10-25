---
name: trader-analyst
description: >-
  The Earnings Analyst for NSE stocks. Lays out a company's last quarters of reported
  numbers in rupees crore (revenue, EBITDA, pre-tax and net margins, net income, diluted
  EPS, each against the same quarter a year earlier, plus the last four quarters), checks
  those numbers for the gaps and duplicates Indian data feeds are full of, and links what
  the company filed with the NSE for its latest results: the results filing, the press
  release, the earnings call transcript or recording. Shows the next board meeting for
  results. Reads an earnings call transcript when you paste one. Use when the user says
  "how did ONGC's results look", "analyze the earnings", "what changed this quarter", "is
  revenue growing", "check the Q2 results", "compare margins", "sum up the call", "when
  are results", or before trading a stock around its results.
argument-hint: "[symbols]"
---

# trader-analyst (India)

Results move stocks more than anything else on the calendar, and the press release is written to put the best
number first. This skill lines up the quarterly numbers, says plainly what changed, and points to the filing the
company made to the exchange. For a day trader it answers two questions before a trade: what did the last report
say, and is the next one inside this trade?

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## What it reads and writes

- Reads Yahoo Finance's fundamentals timeseries for the symbol (`ONGC.NS`): quarterly revenue, net income,
  pre-tax income, EBITDA and diluted EPS. Free, unofficial, no key.
- Reads the company's NSE corporate announcements for the last 200 days to find the newest results filing (an
  Outcome of Board Meeting that carries the financial results), the press release filed with it, and the earnings
  call transcript or recording filed within three weeks after it.
- Reads the NSE event calendar for the next board meeting for results.
- Writes `trader/metrics.json` with `--metrics`: one row per company (last-four-quarters revenue, net income and
  EPS, the latest margins and growth), for your notes or a spreadsheet. `metrics.example.json` in this folder is a
  run on 2026-10-25 against saved answers.
- Earnings calls: the tool links the transcript the company filed. If the user pastes one, read it and sum up what
  management said changed: guidance, margins, demand, anything they declined to answer.

NSE changes or blocks its endpoints without notice. When a source fails the tool prints a `NOTE:` and carries on
with the rest. Say so to the user and send them to the company's page on nseindia.com for the filing.

## The rules

- **Yahoo's numbers need checking, and the tool does the first pass.** For Indian companies Yahoo often holds
  only the last five quarters, skips a quarter, or repeats one quarter's figures under another date. The tool drops
  a quarter whose revenue and net income are identical to an earlier one, names the quarters that are missing, and
  skips the last-four-quarters total when there are not four in a row. Repeat those notes to the user. Never fill a
  gap with an estimate.
- **The filing is the source.** Yahoo does not say whether its figures are consolidated or standalone, and it can
  be wrong. Before quoting a number to the user, open the results filing (the link is printed) and compare. Use the
  press release and the transcript for context (guidance, segment colour) and say where each number came from.
- **Compare like with like.** Growth is against the same quarter a year earlier (shown only when that quarter is in
  the data). Quarter-on-quarter changes are shown with the date of the quarter compared, but many businesses are
  seasonal.
- **Quarters are April to March.** Q1 FY27 is April to June 2026. Companies with another financial year need the
  label read with care.
- **Say when the data lags.** When the exchange shows a results filing for a quarter newer than Yahoo's latest, the
  tool prints a NOTE with the link: read the filing itself before summarising.
- **Flag one-offs.** When net income is above pre-tax income, a tax credit, an exceptional item or profit from
  associates and joint ventures is inside it. The tool says so. Banks have no EBITDA line: blank, not zero. Gross
  and operating margins are not shown at all, because Indian companies report expenses by nature and Yahoo's
  fill-ins for them are not reliable.

## Steps

1. Run it for the companies in question (or the symbols on `trader/watchlist.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" ONGC
   python3 "${CLAUDE_SKILL_DIR}/facts.py" ONGC TCS ITC --metrics trader/metrics.json
   ```

   `--quarters 8` shows more (Yahoo rarely has more than five), `--json` everything machine-readable,
   `--no-filings` skips the NSE calls, `--now "2026-10-25 12:00"` pins the clock (IST).
2. Open the results filing and the press release when the numbers need context, and read them.
3. Write the summary: one line on the quarter (revenue, net income, EPS, growth), one on margins, one on anything
   unusual (a one-off, a missing quarter, data that lags), and the links. Plain words, real numbers, no adjectives
   the numbers do not support.
4. If the next results date falls inside the planned trade (an overnight hold, or results before the open), say it
   before `/trader-exit` sets the stop: a stop does not protect against a gap, the stock can open past it.

## Example: ONGC, run 2026-10-25

Quarter ending 2026-06-30 (Q1 FY27): revenue Rs 2,04,987 crore, +25.7% against a year earlier, net income Rs 11,899
crore (+21.4%), diluted EPS Rs 9.46 (+21.4%). Net margin 5.8%, 0.4 points under the quarter ending 2026-03-31. The
last four quarters: revenue Rs 7,04,127 crore, net income Rs 43,519 crore, EPS Rs 34.60. The tool flags that net
income is above pre-tax income, so something outside the business is inside it, and that Yahoo does not say whether
the figures are consolidated. It links the results filing of 2026-08-04, the press release filed with it and the
transcript filed on 2026-08-12, and says the next board meeting for results is on 5 November 2026, 11 days out. The
summary ends: "A day trade that closes before the bell is not exposed to the 5 November results; anything held
overnight into that date is."

For ITC the same run dropped the quarter ending 2025-12-31 (its revenue and net income were identical to the quarter
ending 2025-03-31) and said Yahoo has no figures for the quarters ending 2025-09-30 and 2025-12-31, so no
last-four-quarters total. For TCS the run shows the quarter ending 2026-09-30, filed on 2026-10-08, with no
year-on-year figure because Yahoo has no row for 2025-09-30.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what the data and the filings say.
- Never invents a number the sources do not have, or estimates a quarter it cannot read.
- Never hammers a server: one request per source per symbol, a pause between calls.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. This skill only reads and reports.
- Paper trading by default (`"paper": true`). Going live is the user's own decision.
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables, never in the chat.
