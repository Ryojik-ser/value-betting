import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import value_betting_analyzer as vba  # noqa: E402


def cfg(**kw):
    c = dict(vba.DEFAULTS)
    c.update(kw)
    return c


def test_remove_vig_sums_to_one():
    probs = vba.remove_vig([2.0, 3.5, 4.0])
    assert abs(sum(probs) - 1) < 1e-12
    assert probs[0] > probs[1] > probs[2]


def test_expected_value_and_kelly():
    assert abs(vba.expected_value(0.5, 2.2) - 0.10) < 1e-12
    assert vba.kelly_stake(0.5, 2.0, 1.0, 1.0) == 0.0          # sin ventaja
    assert abs(vba.kelly_stake(0.5, 2.2, 1.0, 1.0) - 0.1 / 1.2) < 1e-12
    assert vba.kelly_stake(0.9, 3.0, 1.0, 0.03) == 0.03         # tope


def test_demo_finds_value_and_ranks_by_ev():
    report = vba.strip_html(vba.run(cfg(), demo=True, telegram=False))
    assert "Chelsea @ 4.20" in report
    assert "Empate @ 3.60" in report
    assert report.index("Chelsea") < report.index("Empate")      # mayor EV primero
    assert "pocas casas" in report                              # Getafe-Osasuna


def test_bookmaker_filter_excludes_other_books():
    report = vba.run(cfg(BOOKMAKERS=["Bet365"]), demo=True, telegram=False)
    assert "William Hill" not in report and "Codere" not in report


def test_max_picks_and_threshold():
    assert "Picks con valor: <b>0</b>" in vba.run(cfg(MIN_EV=0.5), demo=True, telegram=False)
    assert "Picks con valor: <b>1</b>" in vba.run(cfg(MAX_PICKS=1), demo=True, telegram=False)


def test_settlement_by_team_name(tmp_path):
    ko = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = [{"event_id": "x", "kickoff": ko, "home": "Real Betis", "away": "Sevilla", "outcome": "draw",
             "odds": "3.60", "stake": "10", "status": "pendiente", "profit": ""},
            {"event_id": "y", "kickoff": ko, "home": "Arsenal", "away": "Chelsea", "outcome": "away",
             "odds": "4.20", "stake": "10", "status": "pendiente", "profit": ""}]
    results = [{"id": None, "home": "Real Betis Balompié", "away": "Sevilla FC", "home_goals": 1,
                "away_goals": 1, "date": ko[:10]},
               {"id": None, "home": "Arsenal FC", "away": "Chelsea FC", "home_goals": 2,
                "away_goals": 0, "date": ko[:10]}]
    assert vba.settle_rows(rows, results) == 2
    assert rows[0]["status"] == "ganada" and rows[0]["profit"] == "26.00"
    assert rows[1]["status"] == "perdida" and rows[1]["profit"] == "-10.00"


def test_split_message():
    chunks = vba.split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert all(len(c) <= 1000 for c in chunks) and len(chunks) > 1
