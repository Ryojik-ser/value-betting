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
    assert report.index("Chelsea @") < report.index("Empate @")      # mayor EV primero
    assert "Getafe–Osasuna" in report                           # sin pick (pocas casas)


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
    assert "Arsenal o empate -296" in report
    assert "Gana Inter -197" in report                                # 1X pagaría < 1.20
    assert report.index("Arsenal") < report.index("Inter")       # más probable primero


def test_safe_mode_respects_min_prob():
    report = vba.run(cfg(MODE="seguro", SAFE_MIN_PROB=0.9), demo=True, telegram=False)
    assert "<b>0</b> sencillas" in report


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
    assert "⭐ Europa paga" in report
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
    assert "PARLAYS" in report and "30% de tu banca" in report and "Notas del analista" in report


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


def test_focus_team_section():
    report = vba.strip_html(vba.run(cfg(MODE="seguro"), demo=True, telegram=False))
    assert "🩵 MANCHESTER CITY" in report
    assert "Haaland anota -" in report
    assert "Gana Man City + Haaland anota" in report
    reviews, c = _demo_reviews()
    city = next(r for r in reviews if r.home == "Manchester City")
    fa = vba.focus_analysis(city, c)
    probs = {pk[1]: pk[2] for pk in fa["picks"]}
    # coherencia: ganar + anota nunca es más probable que cada parte por separado
    assert probs["Gana Man City + Haaland anota"] < probs["Haaland anota"]
    assert probs["Gana Man City + Haaland anota"] < probs["Gana Man City"]
    # con momios reales de goleador se mezcla con el mercado
    fa2 = vba.focus_analysis(city, c, props=[(1.50, "Bet365"), (1.55, "Unibet")])
    assert fa2["market"]["best"] == 1.55


def test_focus_season_share():
    reviews, c = _demo_reviews()
    city = next(r for r in reviews if r.home == "Manchester City")
    fa = vba.focus_analysis(city, c, season={"goals": 12, "played": 8, "penalties": 2,
                                              "team_goals": 24, "team_played": 8, "position": 1})
    assert abs(fa["share"] - 0.5) < 1e-9


def test_focus_section_only_on_match_day():
    report = vba.strip_html(vba.run(cfg(MODE="seguro", HOURS_AHEAD=3), demo=True, telegram=False))
    assert "🩵 MANCHESTER CITY" not in report   # el City juega dentro de 11 h, fuera de la ventana


def test_report_html_is_well_formed_and_split_by_sections():
    import re
    report = vba.run(cfg(MODE="seguro", BANKROLL=50), demo=True, telegram=False)
    for chunk in vba.split_message(report):
        stack = []
        for m in re.finditer(r"<(/?)(\w+)[^>]*>", chunk):
            if m.group(1):
                assert stack and stack[-1] == m.group(2)
                stack.pop()
            else:
                stack.append(m.group(2))
        assert not stack
    small = vba.split_message(report, limit=1200)
    assert len(small) > 1 and all(c.count("<blockquote") == c.count("</blockquote>") for c in small)


def test_leg_wins_codes():
    assert vba.leg_wins("home_dc", 1, 1) and not vba.leg_wins("away_win", 1, 1)
    assert vba.leg_wins("away_plus_1.5", 2, 1) and not vba.leg_wins("away_plus_1.5", 3, 1)
    assert vba.leg_wins("over_1.5", 1, 1) and vba.leg_wins("under_3.5", 2, 1)
    assert vba.leg_wins("btts_no", 2, 0) and vba.leg_wins("home_win_over_1.5", 2, 0)
    assert vba.leg_wins("player", 1, 0) is None


def test_bets_recorded_and_settled_end_to_end(tmp_path, monkeypatch):
    c = vba.load_config()
    c.update(MODE="seguro", ODDS_API_KEY="x", FOOTBALL_DATA_KEY="", HISTORY_FILE=str(tmp_path / "h.csv"),
             BANKROLL=50)
    data = vba.demo_events()
    monkeypatch.setattr(vba, "fetch_odds", lambda sk, cf: (data.get(sk, []), "400"))
    monkeypatch.setattr(vba, "has_events", lambda *a: True)
    monkeypatch.setattr(vba, "fetch_player_props", lambda *a: [])
    vba.run(c, telegram=False)
    rows = vba.read_bets(vba.bets_file(c))
    kinds = {r["kind"] for r in rows}
    assert {"parlay", "nofav", "city"} <= kinds
    # todos los partidos terminan 1-1 hace un día
    past = (vba.datetime.now(vba.timezone.utc) - vba.timedelta(days=1))
    for r in rows:
        legs = vba.json.loads(r["legs"])
        for lg in legs:
            lg["kickoff"] = past.isoformat()
        r["legs"] = vba.json.dumps(legs)
    vba.write_bets(vba.bets_file(c), rows)
    res = [{"id": e["id"], "home": e["home_team"], "away": e["away_team"], "home_goals": 1, "away_goals": 1,
            "date": past.date().isoformat()} for evs in data.values() for e in evs]
    monkeypatch.setattr(vba, "fetch_scores_odds_api", lambda sk, cf: res)
    assert vba.settle_pending(c) > 0
    st = vba.bets_stats(vba.bets_file(c))
    assert sum(k["n"] for k in st.values()) == len(rows)
    report = vba.strip_html(vba.history_section({"n": 0}, c, st))
    assert "Parlays" in report and "ROI" in report


def test_leagues_without_games_cost_nothing(tmp_path, monkeypatch):
    c = vba.load_config()
    c.update(MODE="seguro", ODDS_API_KEY="x", HISTORY_FILE=str(tmp_path / "h.csv"))
    called = []
    monkeypatch.setattr(vba, "has_events", lambda sk, *a: sk == "soccer_epl")
    monkeypatch.setattr(vba, "fetch_odds", lambda sk, cf: (called.append(sk), ([], "400"))[1])
    vba.run(c, telegram=False)
    assert called == ["soccer_epl"]


def test_afternoon_run_does_not_record(tmp_path, monkeypatch):
    c = vba.load_config()
    c.update(MODE="seguro", ODDS_API_KEY="x", HISTORY_FILE=str(tmp_path / "h.csv"), RUN_KIND="tarde")
    data = vba.demo_events()
    monkeypatch.setattr(vba, "fetch_odds", lambda sk, cf: (data.get(sk, []), "400"))
    monkeypatch.setattr(vba, "has_events", lambda *a: True)
    monkeypatch.setattr(vba, "fetch_player_props", lambda *a: [])
    report = vba.strip_html(vba.run(c, telegram=False))
    assert "ACTUALIZACIÓN DE LA TARDE" in report
    assert not vba.read_bets(vba.bets_file(c)) and not vba.read_history(c["HISTORY_FILE"])


def test_dixon_coles_matches_draws_better_than_plain_poisson():
    fair, po = [0.30, 0.32, 0.38], 0.40
    lh, la, rho = vba.fit_goals(fair, po)
    d_dc = vba._summary(vba.score_matrix(lh, la, rho))[1]
    assert rho < 0 and abs(d_dc - fair[1]) < 0.01
    m = vba.score_matrix(lh, la, rho)
    assert abs(sum(map(sum, m)) - 1) < 1e-9
