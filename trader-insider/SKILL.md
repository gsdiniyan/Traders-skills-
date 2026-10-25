---
name: trader-insider
description: >-
  The Big-Money and Insider Tracker for NSE stocks, the Indian counterpart of a
  politician-trades tracker. Reads the NSE bulk and block deals (who bought or sold a big
  block of a stock, named, the same evening), and the SEBI insider-trading disclosures that
  promoters, directors, key managers and their relatives must file (who, category, buy or
  sell, mode, shares, rupee value, trade date, how many days it took to file, holding
  before and after). Alerts once per new item, filters by a fund or investor name, flags
  stocks you hold, and says when a client is on both sides of the same stock the same day
  (a trading firm, not a view). Use when the user says "who is buying X", "any bulk
  deals", "did promoters buy", "insider buying", "what did the mutual funds buy today",
  "track Mirae", "alert me when a fund buys my stock", or "run the insider skill".
argument-hint: "[symbols] [--who NAME]"
---

# trader-insider (India)

Two kinds of public filings show who is trading a stock. A bulk deal (one client buying or selling more than 0.5%
of a company's shares in a day) is named in the exchange's list the same evening. An insider filing (a promoter,
director, key manager or designated employee, or their relative) comes days later, because the insider has two
trading days to tell the company and the company two more to tell the exchange. This skill reads both and tells
the user only what is new.

It is fast. It is not early. By the time anyone reads a bulk deal the trade is done, and an insider filing is days
old. There is no inside information here: only public filings, read quickly. Say that every time. A named fund
buying is a fact about the fund, not a reason to buy.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" --help
```

## What it reads and writes

- Reads `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv`: the latest trading day's deals only.
- Reads `nseindia.com/api/corporates-pit?index=equities&symbol=RELIANCE&from_date=..&to_date=..`: the insider
  disclosures for one company over a date range, with the XBRL filing link for each.
- Writes `trader/state/insider.json` (the deals and filings already shown, kept 60 days, so each alerts once).
- Reads `trader/portfolio.json`, `trader/plan.json` and `trader/watchlist.json` for `--holdings`, and marks trades in
  what you hold.

All of this is free and unofficial. It answered a plain `Mozilla/5.0` User-Agent with no cookies and no keys on
2026-10-25. NSE changes or blocks these files and endpoints without notice: the tool prints a `note:` for a source
that fails and carries on with the other. The deal files hold one day, so `new` has to run once a day after about
19:00 IST on weekdays to keep a history. NSE's insider answer is patchy: on 2026-10-25 it stopped in February for
RELIANCE and in April for HCLTECH, and was empty for ONGC, TCS and HDFCBANK. An empty answer is not proof that nobody
traded: say that, and send the user to the company's page on nseindia.com (Corporate Filings, Insider Trading).

## The rules

- **Quote the filing.** Every insider line carries the XBRL link NSE gives for it. Third-party trackers and
  newspaper columns can be wrong about dates and names. Bulk deals come from NSE's own file.
- **Say what a bulk deal is and is not.** One line is one client on one side of one stock for one day, with the
  weighted average price when the client traded in several rows. When the same client is on both sides the same day
  (Graviton and Jump Trading in the saved run), it is a trading firm and the deal carries no view. The tool marks it.
- **Names are what NSE prints.** Funds appear under the fund house's name ("HDFC MUTUAL FUND"), not the scheme or the
  fund manager. Individuals appear under their own name. Do not guess who stands behind a name.
- **Categories matter.** Promoter and Promoter Group trades are the owners. Directors and KMP are the management.
  "Immediate relative" is a family member of an insider. "Employees/Designated Employees" and employee welfare
  trusts (ESOP exercises and sales) are usually pay, not a view.
- **Pledges are not trades.** A pledge, a revoke or an invocation is a loan against shares. The tool hides them
  unless `--all-types` is given.
- **Say the delay.** Each insider row shows how many weekdays passed between the trade and the filing, and flags
  anything past four (two for the insider, two for the company). It counts weekdays only, not NSE holidays.
- **Rupees, not dollars.** Values print as Rs lakh and Rs crore. A lakh is 100,000, a crore 10 million.

## Step 1: what happened today

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals                                  # the day's bulk and block deals, biggest first
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --symbols ONGC RELIANCE          # only those stocks
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --who "mutual fund" --min-cr 50  # any fund, Rs 50 crore or more
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --holdings                       # your portfolio, plan and watchlist
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --rows --symbols BONFIG          # every trade row, not grouped
```

`--who` takes words of a name ("hdfc mutual"), repeats, and with `--symbols` both must match. Exit code 1 means
nothing matched.

## Step 2: what insiders filed

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" insiders RELIANCE --days 90
python3 "${CLAUDE_SKILL_DIR}/insider.py" insiders LALPATHLAB --who "rahul sharma" --days 400
python3 "${CLAUDE_SKILL_DIR}/insider.py" insiders --holdings --days 30
```

## Step 3: watch for new ones

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" new --holdings --who "mirae" --who "nippon"
```

Shows only deals and filings not seen at the last check, then records them. `new` needs something to watch:
`--who` (a fund or investor, matched against deals anywhere), `--symbols`, or `--holdings`. With both, it shows a
deal if either matches. Insider filings are checked for the symbols only. Run it after 19:00 IST with `/loop 1d` or a
scheduled task. Exit code 1 means nothing new.

## Example: Friday 2026-10-23

```
NSE bulk and block deals for 2026-10-23.
  BULK  2026-10-23  SWIGGY     SELL GRAVITON RESEARCH CAPITAL LLP: 1,44,04,126 shares at Rs 184.21 = Rs 265.34 crore  (same client on both sides that day: a trading firm, not a view)
  BULK  2026-10-23  TONBO      BUY  HDFC MUTUAL FUND: 10,00,000 shares at Rs 1177.32 = Rs 117.73 crore, 2 rows
```

The Swiggy line is not a view: the same firm bought 1,42,30,798 shares the same day. HDFC Mutual Fund buying Rs
117.73 crore of Tonbo is a named fund buying a block in one day, the type of line worth a look at the chart and the
news (`/trader-news`, `/trader-analyst`) and nothing more. The same day Invesco Mutual Fund bought Rs 134.27 crore
of one stock and Bajaj Life Insurance Rs 102.15 crore of Tonbo.

An insider line, for Reliance:

```
  RELIANCE   BUY   ASHWINI DARSHAN NAPHADE (Immediate relative): Others 960 shares, Rs 13.35 lakh, 2026-02-02, filed 5 weekdays after the trade (LATE: the rules allow 2 + 2 trading days)
      https://nsearchives.nseindia.com/corporate/xbrl/IT_1193547_1622072_09022026090545_WEB.xml
```

Then: "This is a public filing made 5 weekdays after the trade, by a relative of an insider, for Rs 13 lakh in a company
worth lakhs of crores. It says what happened in February, not what anyone will do next."

If the user wants to act on it, it goes the long way: a disclosure is a reason to look at a chart, not a setup.
`/trader-scanner` and `/trader-entry` decide if there is a trade today; then shares only (the config has
`allow_options: false`), sized by `/trader-budget`, checked by `/trader-risk`, and placed by the user themselves from
a `/trader-broker` ticket after their yes.

## Other disclosures not read by this tool

- Quarterly shareholding patterns (who owns what, filed within 21 days of each quarter end) are on the company's page
  on nseindia.com and bseindia.com. They show named holders above 1% and are the place to see a famous investor's
  stake. Open them in the browser and paste the table if the user wants a write-up.
- SAST disclosures (anyone crossing 5% or moving by 2% under the takeover rules) are in the same corporate filings
  section.

## What it never does

- Never says or implies a person has inside information, or that copying a fund or an insider beats the market. No
  return claims.
- Never presents a weighted average as a single price or a name as the person behind it.
- Never places an order.

## Safety

- Education and tooling, not investment advice. The author is not a SEBI-registered
  investment adviser or research analyst. Nothing in this pack is a recommendation to buy or sell anything.
- Paper trading by default (`"paper": true` in `trader/config.json`).
- Nothing in this pack places an order. trader-broker only prepares order tickets that the
  user places themselves in their own broker app.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Bulk deals and insider filings are PUBLIC disclosures made after the trade. This skill reads them fast; it has no
  inside information, and it says so.
