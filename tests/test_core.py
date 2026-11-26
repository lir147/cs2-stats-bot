import json
import unittest

from core.analysis import analyze
from core.console_import import ImportError_, parse_any, parse_console, replay_gsi
from core.gsi import GsiTracker
from core.models import MatchRecord
from core.report import rounds_report, stat_report
from core.stats import compute
from tests.sim import RoundPlan, simulate


def good_match():
    plans = []
    for i in range(12):
        plans.append(RoundPlan(side="CT", kills=2 if i % 3 == 0 else 1, hs=1, damage=150,
                               assists=1 if i % 4 == 0 else 0,
                               die_at=None if i % 2 == 0 else 70, win=i % 3 != 2,
                               mvp=i % 3 == 0, money=5000, spend=4700))
    for i in range(4):
        plans.append(RoundPlan(side="T", kills=1, hs=0, damage=90, die_at=60, win=True))
    return plans


def bad_match():
    plans = []
    for i in range(16):
        plans.append(RoundPlan(
            side="CT" if i < 8 else "T", kills=0 if i % 3 else 1, hs=0,
            damage=0 if i % 2 else 120, die_at=12, win=False,
            flashed_on_death=i % 4 == 0, grenades=2,
            money=6000 if i in (4, 5, 6) else 4500, spend=800 if i in (4, 5, 6) else 4300))
    return plans


class GsiTrackerTest(unittest.TestCase):
    def run_plans(self, plans):
        tr = GsiTracker()
        events = []
        for p in simulate(plans):
            events += tr.process(p)
        events += tr.flush()
        done = [e for e in events if e.kind == "match_finished"]
        self.assertEqual(len(done), 1, "должен завершиться ровно один матч")
        return done[0].match, events

    def test_rounds_counted(self):
        m, events = self.run_plans(good_match())
        self.assertEqual(len(m.rounds), 16)
        self.assertEqual(len([e for e in events if e.kind == "round_finished"]), 16)
        self.assertEqual(m.map_name, "de_mirage")
        self.assertEqual(m.player_name, "lir147")

    def test_per_round_values(self):
        m, _ = self.run_plans(good_match())
        r1, r2 = m.rounds[0], m.rounds[1]
        self.assertEqual((r1.kills, r1.headshot_kills, r1.damage), (2, 1, 150))
        self.assertTrue(r1.survived)
        self.assertTrue(r1.mvp)
        self.assertEqual(r1.assists, 1)
        self.assertTrue(r2.died)
        self.assertAlmostEqual(r2.death_time, 70, delta=1)
        self.assertEqual(r1.money_start, 5000)
        self.assertEqual(r1.money_after_buy, 300)
        self.assertEqual(r1.side, "CT")
        self.assertTrue(r1.won)
        self.assertFalse(m.rounds[2].won)
        self.assertEqual(m.rounds[-1].side, "T")

    def test_totals(self):
        m, _ = self.run_plans(good_match())
        s = compute(m)
        exp_kills = sum(p.kills for p in good_match())
        exp_deaths = sum(1 for p in good_match() if p.die_at is not None)
        self.assertEqual(s.kills, exp_kills)
        self.assertEqual(s.deaths, exp_deaths)
        self.assertEqual(s.mvps, 4)
        self.assertEqual(s.adr, round((12 * 150 + 4 * 90) / 16, 1))
        self.assertEqual(s.kast, 100.0)  # каждый раунд есть фраг
        self.assertEqual(s.kd, round(exp_kills / exp_deaths, 2))

    def test_death_flags(self):
        m, _ = self.run_plans(bad_match())
        r = m.rounds[0]
        self.assertTrue(r.died)
        self.assertTrue(r.flashed_on_death)
        self.assertEqual(r.grenades_unused_on_death, 2)

    def test_warmup_resets(self):
        tr = GsiTracker()
        payloads = simulate(good_match()[:3])
        for p in payloads[:-2]:
            tr.process(p)
        self.assertIsNotNone(tr.match)
        warm = json.loads(json.dumps(payloads[0]))
        tr.process(warm)
        self.assertIsNone(tr.match)


class AnalysisTest(unittest.TestCase):
    def test_bad_match_has_findings_in_all_categories(self):
        m = replay_gsi(simulate(bad_match()))
        a = analyze(m)
        cats = {f.category for f in a.findings}
        for c in ("positioning", "timing", "damage", "economy", "utility"):
            self.assertIn(c, cats, f"нет категории {c}: {cats}")
        self.assertEqual(len(a.tips), 3)
        self.assertEqual(len(set(a.tips)), 3)

    def test_good_match_still_three_tips(self):
        m = replay_gsi(simulate(good_match()))
        a = analyze(m)
        self.assertEqual(len(a.tips), 3)
        self.assertTrue(a.strengths)

    def test_report_renders_and_fits(self):
        for plans in (good_match(), bad_match()):
            m = replay_gsi(simulate(plans))
            txt = stat_report(m)
            self.assertIn("K/D", txt)
            self.assertIn("3 совета", txt)
            self.assertLess(len(txt), 4096)
            rr = rounds_report(m)
            self.assertLess(len(rr), 4096)
            self.assertEqual(rr.count("\n"), len(m.rounds) + 4)


CONSOLE = """
Map: de_inferno
Player: lir147 - Damage Given
-------------------------
Damage Given to "BOT Rock" - 100 in 4 hits
Damage Given to "BOT Pat" - 45 in 1 hit
Player: lir147 - Damage Taken
-------------------------
Damage Taken from "BOT Pat" - 100 in 3 hits
Player: lir147 - Damage Given
-------------------------
Damage Given to "BOT Rock" - 120 in 2 hits
Damage Given to "BOT Pat" - 100 in 1 hit
Player: lir147 - Damage Taken
-------------------------
Damage Taken from "BOT Pat" - 30 in 1 hit
"""


class ImportTest(unittest.TestCase):
    def test_console(self):
        m = parse_console(CONSOLE)
        self.assertEqual(m.map_name, "de_inferno")
        self.assertEqual(m.player_name, "lir147")
        self.assertEqual(len(m.rounds), 2)
        self.assertEqual((m.rounds[0].kills, m.rounds[0].damage, m.rounds[0].died), (1, 145, True))
        self.assertEqual((m.rounds[1].kills, m.rounds[1].damage, m.rounds[1].died), (2, 200, False))
        self.assertIn("Импорт из консоли", stat_report(m))

    def test_parse_any_formats(self):
        payloads = simulate(good_match())
        m1 = parse_any(json.dumps(payloads).encode())
        m2 = parse_any("\n".join(json.dumps(p) for p in payloads))
        self.assertEqual(len(m1.rounds), 16)
        self.assertEqual(len(m2.rounds), 16)
        m3 = parse_any(json.dumps(m1.to_dict()))
        self.assertEqual(MatchRecord.from_dict(m1.to_dict()).to_dict()["rounds"], m3.to_dict()["rounds"])
        self.assertEqual(len(parse_any(CONSOLE).rounds), 2)
        with self.assertRaises(ImportError_):
            parse_any(b"hello")


if __name__ == "__main__":
    unittest.main()
