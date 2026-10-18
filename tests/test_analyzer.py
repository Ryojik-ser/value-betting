import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import value_betting_analyzer as vba  # noqa: E402


def cfg(**kw):
    c = dict(vba.DEFAULTS)
    c["MODE"] = "valor"
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
    assert "Chelsea @ +320 (4.20)" in report
    assert "Empate @ +260 (3.60)" in report
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


def test_safe_mode_high_probability_picks():
    report = vba.strip_html(vba.run(cfg(MODE="seguro"), demo=True, telegram=False))
    assert "1X – Arsenal o empate" in report
    assert "momio mínimo -296 (1.34)" in report
    assert "1 – Inter" in report                                 # 1X pagaría < 1.20
    assert report.index("Arsenal") < report.index("Inter")       # más probable primero


def test_safe_mode_respects_min_prob():
    report = vba.run(cfg(MODE="seguro", SAFE_MIN_PROB=0.9), demo=True, telegram=False)
    assert "Picks: <b>0</b>" in report


def test_double_chance_settlement():
    rows = [{"event_id": "z", "kickoff": "2026-10-17T12:00:00+00:00", "home": "Arsenal", "away": "Chelsea",
             "outcome": "home_draw", "odds": "1.34", "stake": "20", "status": "pendiente", "profit": ""}]
    res = [{"id": "z", "home": "Arsenal", "away": "Chelsea", "home_goals": 1, "away_goals": 1, "date": "2026-10-17"}]
    assert vba.settle_rows(rows, res) == 1
    assert rows[0]["status"] == "ganada" and rows[0]["profit"] == "6.80"


def test_safe_mode_flags_market_value_first():
    events = vba.demo_events()
    # Inter paga 1.60 en una casa: por encima de su cuota justa (~1.51) -> "seguro + valor"
    events["soccer_italy_serie_a"][0]["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = 1.60
    orig = vba.demo_events
    vba.demo_events = lambda: events
    try:
        report = vba.strip_html(vba.run(cfg(MODE="seguro"), demo=True, telegram=False))
    finally:
        vba.demo_events = orig
    assert "Seguro + valor" in report
    assert report.index("Inter") < report.index("Arsenal")


def test_american_odds():
    assert vba.american(2.50) == "+150"
    assert vba.american(1.25) == "-400"
    assert vba.american(2.00) == "+100"


def _demo_reviews(**kw):
    c = cfg(MODE="seguro", **kw)
    return [vba.analyze_event(ev, "L", c, vba.timezone.utc) for evs in vba.demo_events().values() for ev in evs], c


def test_goal_model_matches_market():
    reviews, c = _demo_reviews()
    r = next(r for r in reviews if r.home == "Inter")
    h, d, a, o = vba._summary(vba.score_matrix(*vba.fit_goals(r.fair, r.p_over25)))
    assert abs(h - r.fair[0]) < 0.03 and abs(o - r.p_over25) < 0.03


def test_parlays_one_leg_per_match_and_limits():
    reviews, c = _demo_reviews(BANKROLL=50, PARLAY_STAKE=15)
    parlays = vba.build_parlays(reviews, c)
    assert parlays
    for par in parlays:
        assert len({lg.event_id for lg in par["legs"]}) == len(par["legs"])
        assert all(lg.prob >= c["LEG_MIN_PROB"] for lg in par["legs"])
    report = vba.strip_html(vba.run(c, demo=True, telegram=False))
    assert "PARLAYS SUGERIDAS" in report and "30% de tu banca" in report and "Notas del analista" in report


def test_underdog_picks_are_non_favorites_with_good_prob_and_payout():
    reviews, c = _demo_reviews()
    picks = vba.underdog_picks(reviews, c)
    assert picks
    for p in picks:
        lg = p["leg"]
        assert lg.prob >= c["DOG_MIN_PROB"] and lg.min_odds >= c["DOG_MIN_ODDS"]
        assert "Real Betis" not in lg.selection and "Inter " not in lg.selection  # nunca el favorito
    assert any(p["leg"].selection == "Sevilla o empate" for p in picks)
    # ⭐ cuando una casa paga por encima de la cuota justa
    events = vba.demo_events()
    for bk in events["soccer_spain_la_liga"][0]["bookmakers"]:
        if bk["key"] == "betsson":
            bk["markets"][0]["outcomes"][1]["price"] = 3.90  # Sevilla
    rv = [vba.analyze_event(ev, "L", c, vba.timezone.utc) for ev in events["soccer_spain_la_liga"]]
    star = vba.underdog_picks(rv, c)[0]
    assert star["ev"] is not None and star["ev"] >= 0.02
