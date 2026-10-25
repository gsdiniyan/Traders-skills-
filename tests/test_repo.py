"""Repo-level checks: the eleven skills, the manifests, the README, the safety rules and what must not be in the files."""

import ast
import io
import json
import os
import re
import unittest
from contextlib import redirect_stdout

from _load import FIX, ROOT, load

SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-insider"]
ABOUT = ("11 Claude skills for day trading on the NSE: scan hundreds of charts, track announcements and news, "
         "time every entry and exit, and replay every outcome before you risk a rupee.")
# words from the US original that must not survive in the skills' files
US_WORDS = ["NYSE", "Nasdaq", "Alpaca", "EDGAR", "New York", "Pelosi", "Congress", "Allow once", "place_stock_order",
            "sec_user_agent", "pdt_guard", "financial advice", "Interactive Brokers"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f in ("LICENSE", "NOTICE"):
                yield os.path.join(base, f)


def skill_files(*suffixes):
    for s in SKILLS:
        folder = os.path.join(ROOT, s)
        for f in sorted(os.listdir(folder)):
            if f.endswith(suffixes):
                yield s, f, os.path.join(folder, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_listing(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git"))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "NOTICE", "README.md", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 300, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_every_skill_carries_the_safety_block(self):
        for s in SKILLS:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            for phrase in ("not investment advice", "not a sebi-registered investment adviser",
                           "paper trading by default", "nothing in this pack places an order",
                           "user places themselves in their own broker app"):
                self.assertIn(phrase, low, "%s: %s" % (s, phrase))

    def test_the_ticket_desk_needs_a_yes_and_places_nothing(self):
        _, text = frontmatter(os.path.join(ROOT, "trader-broker", "SKILL.md"))
        low = " ".join(text.lower().split())
        self.assertIn("never places an order", low)
        self.assertIn("explicit yes for this one order", low)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "traders-skills")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        self.assertEqual(plugin["license"], "MIT")
        self.assertEqual(plugin["repository"], "https://github.com/gsdiniyan/Traders-skills-")
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "traders-skills")
        self.assertEqual(market["plugins"][0]["name"], "traders-skills")
        self.assertEqual(market["plugins"][0]["source"], "./")

    def test_readme_carries_the_install_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# Traders Skills (India)"))
        for line in (ABOUT,
                     "https://github.com/gsdiniyan/Traders-skills- install skill",
                     "/plugin marketplace add gsdiniyan/Traders-skills-",
                     "/plugin install traders-skills@traders-skills",
                     "## Fine print", "Not investment advice",
                     "day-trader-skill", "Jake Schincariol"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)
        self.assertNotIn("/trader-pelosi", readme)

    def test_the_license_keeps_both_copyright_lines(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Ganesh S Diniyan", text)
        self.assertIn("Copyright (c) 2026 Jake Schincariol", text)
        with open(os.path.join(ROOT, "NOTICE"), encoding="utf-8") as fh:
            self.assertIn("Jake Schincariol", fh.read())

    def test_no_em_dashes_and_no_personal_details(self):
        mine = "gs.diniyan" + "@" + "gmail.com"          # built, so this file passes too
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):   # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
                self.assertNotIn("@" + "gmail.com", text, rel)
                self.assertNotIn("C:" + "\\Users", text, rel)
                self.assertNotIn("gan" + "es", text.lower().replace("ganesh", ""), rel)
            self.assertNotIn(mine, text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)

    def test_no_secrets_in_any_file(self):
        patterns = [r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"ghp_[A-Za-z0-9]{20,}",
                    r"xox[abp]-[A-Za-z0-9-]{10,}", r"\b\d{9,10}:[A-Za-z0-9_-]{30,}\b",
                    r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", r"sk-[A-Za-z0-9]{32,}"]
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for p in patterns:
                self.assertIsNone(re.search(p, text), "%s matches %s" % (os.path.relpath(path, ROOT), p))

    def test_no_us_leftovers_in_the_skills(self):
        for s, f, path in skill_files(".md", ".py", ".json"):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for word in US_WORDS:
                self.assertNotIn(word, text, "%s/%s: %s" % (s, f, word))
            self.assertIsNone(re.search(r"(?<![\w{])\$\d", text), "%s/%s has a dollar amount" % (s, f))

    def test_skill_md_commands_name_files_that_exist(self):
        for s in SKILLS:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            for name in set(re.findall(r"\$\{CLAUDE_SKILL_DIR\}/([\w.\-]+)", text)):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, s, name)), "%s/SKILL.md names %s" % (s, name))

    def test_every_tool_runs_on_python_3_8(self):
        # the README promises 3.8: parse each tool with 3.8 grammar and keep out 3.9+ library calls
        for s, f, path in skill_files(".py"):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            ast.parse(src, filename=path, feature_version=(3, 8))
            for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(", "datetime.UTC"):
                self.assertNotIn(bad, src, "%s/%s" % (s, f))
            self.assertNotRegex(src, r"(?<![\w.])(list|dict|tuple|set)\[", "%s/%s" % (s, f))

    def test_tools_use_only_the_standard_library(self):
        std = set(__import__("sys").stdlib_module_names) if hasattr(__import__("sys"), "stdlib_module_names") else None
        for s, f, path in skill_files(".py"):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if std is not None:
                        self.assertIn(m, std, "%s/%s imports %s" % (s, f, m))
                    else:
                        self.assertNotIn(m, ("requests", "numpy", "pandas", "yfinance", "bs4", "lxml"), "%s/%s" % (s, f))

    def test_the_example_config_is_in_rupees_with_safe_defaults(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day", "min_reward_risk",
                  "require_stop", "no_entries_before", "no_entries_after", "flat_by", "tick_size"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg["paper"], True)
        self.assertEqual((cfg["exchange"], cfg["product"], cfg["tick_size"], cfg["flat_by"]), ("NSE", "INTRADAY", 0.05, "15:15"))
        for k in ("allow_short", "allow_margin", "allow_options"):
            self.assertIs(cfg.get(k, False), False, k)
        self.assertFalse(any(k.endswith("_usd") for k in cfg))

    def test_every_fixture_folder_belongs_to_a_skill(self):
        folders = sorted(os.listdir(FIX))
        self.assertTrue(set(folders) <= set(SKILLS), folders)


class ReadmeExample(unittest.TestCase):
    """The README's ONGC walk-through, reproduced from the saved data."""

    def test_the_time_machine_numbers(self):
        tm = load("trader-timemachine", "timemachine")
        out = io.StringIO()
        with redirect_stdout(out):
            code = tm.main(["run", "--symbol", "ONGC", "--date", "2026-10-23", "--entry", "225.74", "--stop", "225.10",
                            "--target", "227.00", "--entry-time", "10:05", "--now", "2026-10-23 10:07",
                            "--offline", os.path.join(FIX, "trader-timemachine", "ONGC-60d5m.json.gz")])
        text = out.getvalue()
        self.assertEqual(code, 0)
        for line in ("target first   22.9 %", "stop first     75.2 %", "neither        2.0 %",
                     "over its last 51 full sessions", "fired 20 times: 3 target, 11 stop, 6 neither",
                     "Leans toward the stop"):
            self.assertIn(line, text)

    def test_the_stop_would_have_been_hit_at_1010(self):
        import datetime
        with open(os.path.join(FIX, "trader-entry", "ONGC-5d-5m-2026-10-23.json")) as fh:
            res = json.load(fh)["chart"]["result"][0]
        ist = datetime.timedelta(hours=5, minutes=30)
        first = None
        for t, lo, hi in zip(res["timestamp"], res["indicators"]["quote"][0]["low"], res["indicators"]["quote"][0]["high"]):
            when = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).replace(tzinfo=None) + ist
            if when.date() != datetime.date(2026, 10, 23) or when.time() < datetime.time(10, 5) or lo is None:
                continue
            if lo <= 225.10:
                first = ("stop", when.strftime("%H:%M"), round(lo, 2))
                break
            if hi >= 227.0:
                first = ("target", when.strftime("%H:%M"), round(hi, 2))
                break
        self.assertEqual(first, ("stop", "10:10", 224.95))
        self.assertEqual(res["indicators"]["quote"][0]["close"][-1], 227.5)


if __name__ == "__main__":
    unittest.main()
