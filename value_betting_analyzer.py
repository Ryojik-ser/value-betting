#!/usr/bin/env python3
"""
Value Betting Analyzer
======================

Analiza cada día los partidos 1X2 de las principales ligas europeas, calcula la
probabilidad "justa" de cada resultado a partir del mercado (casa de referencia
sin margen o consenso de casas), la compara con la mejor cuota disponible y
envía a Telegram un informe con los picks de valor esperado (EV) positivo,
ordenados de mayor a menor valor.

Además guarda cada pick en un historial CSV y, cuando hay resultados, lo liquida
para medir aciertos y ROI reales. Es la única forma honesta de saber si el
sistema funciona.

Uso:
    python value_betting_analyzer.py            # análisis normal + Telegram
    python value_betting_analyzer.py --demo     # datos de ejemplo, sin APIs
    python value_betting_analyzer.py --no-telegram   # solo imprime en pantalla
    python value_betting_analyzer.py --stats    # resumen del historial
"""

from __future__ import annotations

import argparse
import csv
import difflib
import html
import json
import os
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# ---------------------------------------------------------------------------
# CREDENCIALES (puedes ponerlas aquí, en config.json o como variables de entorno)
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = "PON_AQUI_TU_TOKEN_DE_TELEGRAM"
TELEGRAM_CHAT_ID = ""  # Lo obtendrás con setup.py
FOOTBALL_DATA_KEY = "PON_AQUI_TU_API_KEY_FOOTBALL_DATA"
ODDS_API_KEY = "PON_AQUI_TU_API_KEY_ODDS"

# ---------------------------------------------------------------------------
# PARÁMETROS DE ESTRATEGIA (valores por defecto, editables en config.json)
# ---------------------------------------------------------------------------
DEFAULTS = {
    # Ligas (claves de The Odds API)
    "LEAGUES": {
        "soccer_epl": "Premier League",
        "soccer_spain_la_liga": "La Liga",
        "soccer_italy_serie_a": "Serie A",
        "soccer_germany_bundesliga": "Bundesliga",
        "soccer_uefa_champs_league": "Champions League",
        "soccer_uefa_europa_league": "Europa League",
    },
    # Modo de trabajo:
    #   "seguro" -> picks de ALTA PROBABILIDAD (favoritos y doble oportunidad) con la
    #               cuota mínima que debes exigir en tu casa (BetMéxico).
    #   "valor"  -> picks de valor esperado usando las cuotas de casas europeas.
    "MODE": "seguro",
    "MAIN_BOOK": "BetMéxico",     # tu casa principal (solo para el texto del informe)
    "SAFE_MIN_PROB": 0.65,        # probabilidad mínima de acierto en modo seguro
    "SAFE_MIN_ODDS": 1.20,        # descarta picks que pagarían menos de 1.20
    "SAFE_STAKE_PCT": 0.02,       # stake fijo en modo seguro (2 % de la banca)
    "REGIONS": "eu",              # eu incluye Pinnacle; puedes usar "eu,uk"
    "HOURS_AHEAD": 24,            # ventana de partidos a analizar
    "MIN_EV": 0.02,               # EV mínimo (2 %). 0 = cualquier valor positivo
    "MAX_PICKS": 10,              # picks máximos por día
    "MIN_ODDS": 1.40,             # evita cuotas demasiado bajas
    "MAX_ODDS": 6.00,             # evita long shots (mucho ruido estadístico)
    "MIN_BOOKS": 4,               # casas mínimas para confiar en el consenso
    "SHARP_BOOK": "pinnacle",     # casa de referencia (la más eficiente)
    "SHARP_WEIGHT": 0.7,          # peso de Pinnacle vs. consenso cuando existe
    "BOOKMAKERS": [],             # casas donde TÚ puedes apostar ([] = todas)
    "BANKROLL": 1000.0,           # tu banca, para sugerir stake
    "KELLY_FRACTION": 0.25,       # Kelly fraccional (1/4 es lo prudente)
    "MAX_STAKE_PCT": 0.03,        # nunca más del 3 % de la banca por pick
    "TIMEZONE": "America/Mexico_City",
    "HISTORY_FILE": "picks_history.csv",
}

ODDS_API_URL = "https://api.the-odds-api.com/v4"
FOOTBALL_DATA_URL = "https://api.football-data.org/v4"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
PLACEHOLDER_PREFIX = "PON_AQUI"


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Prioridad: variables de entorno > config.json > constantes del archivo."""
    cfg = dict(DEFAULTS)
    cfg.update(
        TELEGRAM_TOKEN=TELEGRAM_TOKEN,
        TELEGRAM_CHAT_ID=TELEGRAM_CHAT_ID,
        FOOTBALL_DATA_KEY=FOOTBALL_DATA_KEY,
        ODDS_API_KEY=ODDS_API_KEY,
    )
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for key, default in list(cfg.items()):
        env = os.environ.get(key)
        if env in (None, ""):
            continue
        if isinstance(default, bool):
            cfg[key] = env.lower() in ("1", "true", "si", "sí", "yes")
        elif isinstance(default, int):
            cfg[key] = int(float(env))
        elif isinstance(default, float):
            cfg[key] = float(env)
        elif isinstance(default, (list, dict)):
            cfg[key] = json.loads(env) if env.strip()[:1] in "[{" else [s.strip() for s in env.split(",") if s.strip()]
        else:
            cfg[key] = env
    for k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "FOOTBALL_DATA_KEY", "ODDS_API_KEY"):
        if str(cfg.get(k, "")).startswith(PLACEHOLDER_PREFIX):
            cfg[k] = ""
    hist = Path(cfg["HISTORY_FILE"])
    cfg["HISTORY_FILE"] = str(hist if hist.is_absolute() else BASE_DIR / hist)
    return cfg


def get_tz(name: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"⚠️  Zona horaria '{name}' no disponible (en Windows instala 'tzdata'). Uso UTC.")
        return timezone.utc


# ---------------------------------------------------------------------------
# Matemáticas del valor
# ---------------------------------------------------------------------------
def remove_vig(odds: list[float]) -> list[float]:
    """Quita el margen de la casa (método proporcional). Devuelve probabilidades que suman 1."""
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    return [p / total for p in implied]


def overround(odds: list[float]) -> float:
    return sum(1.0 / o for o in odds) - 1.0


def expected_value(prob: float, odds: float) -> float:
    """EV por unidad apostada: p·cuota − 1."""
    return prob * odds - 1.0


def kelly_stake(prob: float, odds: float, fraction: float, cap: float) -> float:
    """Fracción de banca sugerida (Kelly fraccional, con tope)."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (prob * odds - 1.0) / b
    return max(0.0, min(f * fraction, cap))


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
OUTCOME_LABEL = {"home": "1", "draw": "X", "away": "2", "home_draw": "1X", "away_draw": "X2"}
# Resultados del partido que hacen ganar cada apuesta
WINNING_RESULTS = {"home": {"home"}, "draw": {"draw"}, "away": {"away"},
                   "home_draw": {"home", "draw"}, "away_draw": {"away", "draw"}}


@dataclass
class Pick:
    event_id: str
    sport_key: str
    league: str
    kickoff: datetime
    home: str
    away: str
    outcome: str        # home / draw / away
    selection: str      # nombre legible
    odds: float
    bookmaker: str
    fair_prob: float
    fair_odds: float
    ev: float
    stake_pct: float
    stake: float
    n_books: int
    source: str         # "Pinnacle+consenso" o "consenso"
    min_odds: float = 0.0   # cuota mínima a exigir en tu casa (BetMéxico)
    kind: str = "valor"     # "valor" o "seguro"


@dataclass
class MatchReview:
    league: str
    kickoff: datetime
    home: str
    away: str
    status: str              # "con pick", "sin pick", "datos insuficientes"
    best_ev: float | None = None
    best_prob: float | None = None
    picks: list[Pick] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Análisis de un evento
# ---------------------------------------------------------------------------
def _h2h_prices(bookmaker: dict, home: str, away: str) -> dict | None:
    for market in bookmaker.get("markets", []):
        if market.get("key") != "h2h":
            continue
        prices = {}
        for o in market.get("outcomes", []):
            name, price = o.get("name"), o.get("price")
            if not price or price <= 1.0:
                continue
            if name == home:
                prices["home"] = float(price)
            elif name == away:
                prices["away"] = float(price)
            elif str(name).lower() == "draw":
                prices["draw"] = float(price)
        if len(prices) == 3:
            return prices
    return None


def analyze_event(event: dict, league: str, cfg: dict, tz) -> MatchReview:
    home, away = event["home_team"], event["away_team"]
    kickoff = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(tz)
    review = MatchReview(league=league, kickoff=kickoff, home=home, away=away, status="datos insuficientes")

    all_prices: dict[str, dict] = {}
    titles: dict[str, str] = {}
    for bk in event.get("bookmakers", []):
        p = _h2h_prices(bk, home, away)
        if p:
            all_prices[bk["key"]] = p
            titles[bk["key"]] = bk.get("title", bk["key"])

    if len(all_prices) < cfg["MIN_BOOKS"]:
        return review

    order = ("home", "draw", "away")
    sharp = cfg["SHARP_BOOK"]
    # Consenso: mediana de probabilidades sin margen, excluyendo la casa de referencia.
    others = [remove_vig([p[o] for o in order]) for k, p in all_prices.items() if k != sharp]
    if not others:
        return review
    consensus = [statistics.median(col) for col in zip(*others)]
    s = sum(consensus)
    consensus = [c / s for c in consensus]

    if sharp in all_prices:
        sharp_probs = remove_vig([all_prices[sharp][o] for o in order])
        w = cfg["SHARP_WEIGHT"]
        fair = [w * a + (1 - w) * b for a, b in zip(sharp_probs, consensus)]
        source = "Pinnacle+consenso"
    else:
        fair = consensus
        source = "consenso"

    base = dict(event_id=event["id"], sport_key=event.get("sport_key", ""), league=league,
                kickoff=kickoff, home=home, away=away, n_books=len(all_prices), source=source)

    if cfg.get("MODE") == "seguro":
        pick = safe_pick(fair, all_prices, titles, sharp, base, cfg)
        if pick:
            review.picks.append(pick)
        review.best_prob = max(fair[0] + fair[1], fair[2] + fair[1])
        review.status = "con pick" if review.picks else "sin pick"
        return review

    allowed = {b.lower() for b in cfg.get("BOOKMAKERS") or []}
    names = {"home": home, "draw": "Empate", "away": away}
    best_ev = None
    for i, outcome in enumerate(order):
        candidates = [
            (p[outcome], k) for k, p in all_prices.items()
            if k != sharp and (not allowed or k.lower() in allowed or titles[k].lower() in allowed)
        ]
        if not candidates:
            continue
        price, book = max(candidates)
        ev = expected_value(fair[i], price)
        best_ev = ev if best_ev is None else max(best_ev, ev)
        if ev < max(cfg["MIN_EV"], 1e-9) or not (cfg["MIN_ODDS"] <= price <= cfg["MAX_ODDS"]):
            continue
        pct = kelly_stake(fair[i], price, cfg["KELLY_FRACTION"], cfg["MAX_STAKE_PCT"])
        review.picks.append(Pick(
            **base, outcome=outcome, selection=names[outcome],
            odds=price, bookmaker=titles[book], fair_prob=fair[i], fair_odds=1 / fair[i],
            ev=ev, stake_pct=pct, stake=round(pct * cfg["BANKROLL"], 2),
            min_odds=(1 + cfg["MIN_EV"]) / fair[i],
        ))

    review.best_ev = best_ev
    review.status = "con pick" if review.picks else "sin pick"
    return review


def safe_pick(fair: list[float], all_prices: dict, titles: dict, sharp: str, base: dict, cfg: dict) -> Pick | None:
    """Elige, para un partido, la apuesta con más probabilidad de acierto que aún pague
    al menos SAFE_MIN_ODDS: victoria simple (1 / 2) o doble oportunidad (1X / X2)."""
    home, away = base["home"], base["away"]
    p_home, p_draw, p_away = fair
    options = [
        ("home", p_home, home),
        ("away", p_away, away),
        ("home_draw", p_home + p_draw, f"{home} o empate"),
        ("away_draw", p_away + p_draw, f"{away} o empate"),
    ]
    valid = [o for o in options if o[1] >= cfg["SAFE_MIN_PROB"] and 1 / o[1] >= cfg["SAFE_MIN_ODDS"]]
    if not valid:
        return None
    outcome, prob, selection = max(valid, key=lambda o: o[1])
    fair_odds = 1 / prob
    ref_odds, ref_book = 0.0, ""
    if outcome in ("home", "away"):  # referencia: mejor cuota europea para ese resultado
        prices = [(p[outcome], k) for k, p in all_prices.items() if k != sharp]
        if prices:
            ref_odds, k = max(prices)
            ref_book = titles[k]
    pct_stake = cfg["SAFE_STAKE_PCT"]
    return Pick(
        **base, outcome=outcome, selection=selection, odds=ref_odds, bookmaker=ref_book,
        fair_prob=prob, fair_odds=fair_odds, ev=0.0, stake_pct=pct_stake,
        stake=round(pct_stake * cfg["BANKROLL"], 2), min_odds=fair_odds, kind="seguro",
    )


# ---------------------------------------------------------------------------
# Clientes HTTP
# ---------------------------------------------------------------------------
def _requests():
    try:
        import requests
        return requests
    except ImportError:
        sys.exit("Falta la librería 'requests'. Instálala con:  pip install -r requirements.txt")


def fetch_odds(sport_key: str, cfg: dict) -> tuple[list[dict], str | None]:
    """Devuelve (eventos, peticiones_restantes). 1 crédito por región y mercado."""
    r = _requests().get(
        f"{ODDS_API_URL}/sports/{sport_key}/odds",
        params={"apiKey": cfg["ODDS_API_KEY"], "regions": cfg["REGIONS"], "markets": "h2h",
                "oddsFormat": "decimal", "dateFormat": "iso"},
        timeout=30,
    )
    if r.status_code == 404:
        return [], r.headers.get("x-requests-remaining")
    if r.status_code == 401:
        raise RuntimeError("API Key de The Odds-API inválida.")
    if r.status_code == 429:
        raise RuntimeError("Cuota de The Odds-API agotada este mes.")
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining")


def fetch_scores_odds_api(sport_key: str, cfg: dict) -> list[dict]:
    """Resultados de los últimos 3 días (2 créditos). Solo si no hay football-data."""
    r = _requests().get(
        f"{ODDS_API_URL}/sports/{sport_key}/scores",
        params={"apiKey": cfg["ODDS_API_KEY"], "daysFrom": 3, "dateFormat": "iso"},
        timeout=30,
    )
    if not r.ok:
        return []
    out = []
    for ev in r.json():
        if not ev.get("completed") or not ev.get("scores"):
            continue
        sc = {s["name"]: int(s["score"]) for s in ev["scores"]}
        out.append({"id": ev["id"], "home": ev["home_team"], "away": ev["away_team"],
                    "home_goals": sc.get(ev["home_team"]), "away_goals": sc.get(ev["away_team"]),
                    "date": ev["commence_time"][:10]})
    return out


def fetch_results_football_data(date_from: str, date_to: str, cfg: dict) -> list[dict]:
    """Resultados finalizados (gratis: PL, LaLiga, Serie A, Bundesliga, Champions)."""
    r = _requests().get(
        f"{FOOTBALL_DATA_URL}/matches",
        headers={"X-Auth-Token": cfg["FOOTBALL_DATA_KEY"]},
        params={"dateFrom": date_from, "dateTo": date_to, "status": "FINISHED"},
        timeout=30,
    )
    if not r.ok:
        print(f"⚠️  football-data.org respondió {r.status_code}; no se liquidan picks hoy.")
        return []
    out = []
    for m in r.json().get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        if ft.get("home") is None:
            continue
        out.append({"id": None, "home": m["homeTeam"]["name"], "away": m["awayTeam"]["name"],
                    "home_goals": ft["home"], "away_goals": ft["away"], "date": m["utcDate"][:10]})
    return out


def discover_chat_id(token: str) -> str:
    """Si no hay Chat ID, lo saca del último mensaje que recibió el bot."""
    r = _requests().get(TELEGRAM_URL.format(token=token, method="getUpdates"), timeout=30)
    if r.ok:
        for upd in reversed(r.json().get("result", [])):
            chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
            if chat:
                return str(chat["id"])
    return ""


def send_telegram(text: str, cfg: dict) -> None:
    token, chat = cfg["TELEGRAM_TOKEN"], cfg["TELEGRAM_CHAT_ID"]
    if token and not chat:
        chat = discover_chat_id(token)
        if chat:
            print(f"ℹ️  Chat ID detectado: {chat} (guárdalo como TELEGRAM_CHAT_ID)")
    if not token or not chat:
        raise RuntimeError("Falta TELEGRAM_TOKEN o no hay Chat ID. Escríbele cualquier mensaje a tu bot "
                           "en Telegram y vuelve a ejecutar.")
    for chunk in split_message(text):
        r = _requests().post(
            TELEGRAM_URL.format(token=token, method="sendMessage"),
            json={"chat_id": chat, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"Telegram respondió {r.status_code}: {r.text[:200]}")


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Telegram admite 4096 caracteres por mensaje: corta por líneas."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Historial y liquidación
# ---------------------------------------------------------------------------
HISTORY_FIELDS = ["date", "event_id", "sport_key", "league", "kickoff", "home", "away", "outcome",
                  "selection", "odds", "bookmaker", "fair_prob", "ev", "stake", "status", "profit", "kind"]


def read_history(path: str) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_history(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def record_picks(picks: list[Pick], path: str) -> int:
    rows = read_history(path)
    seen = {(r["event_id"], r["outcome"]) for r in rows}
    added = 0
    for p in picks:
        if (p.event_id, p.outcome) in seen:
            continue
        rows.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "event_id": p.event_id,
            "sport_key": p.sport_key, "league": p.league, "kickoff": p.kickoff.isoformat(),
            "home": p.home, "away": p.away, "outcome": p.outcome, "selection": p.selection,
            # En modo seguro se registra la cuota mínima (estimación conservadora del beneficio).
            "odds": f"{(p.min_odds if p.kind == 'seguro' else p.odds):.2f}",
            "bookmaker": p.bookmaker if p.kind == "valor" else "BetMéxico (cuota mínima)", "fair_prob": f"{p.fair_prob:.4f}",
            "ev": f"{p.ev:.4f}", "stake": f"{p.stake:.2f}", "status": "pendiente", "profit": "", "kind": p.kind,
        })
        added += 1
    write_history(path, rows)
    return added


def normalize_team(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\b(fc|cf|afc|ac|as|ss|sc|ssc|cd|ud|rc|rcd|sv|vfb|vfl|tsg|1\.|fk|bv|de|calcio|club|futbol)\b", " ", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return " ".join(name.split())


def _same_team(a: str, b: str) -> bool:
    na, nb = normalize_team(a), normalize_team(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.75


def settle_rows(rows: list[dict], results: list[dict]) -> int:
    settled = 0
    for r in rows:
        if r["status"] != "pendiente":
            continue
        kickoff_date = r["kickoff"][:10]
        match = None
        for res in results:
            if res.get("id") and res["id"] == r["event_id"]:
                match = res
                break
            if abs((datetime.fromisoformat(res["date"]) - datetime.fromisoformat(kickoff_date)).days) <= 1 \
                    and _same_team(res["home"], r["home"]) and _same_team(res["away"], r["away"]):
                match = res
                break
        if not match or match["home_goals"] is None or match["away_goals"] is None:
            continue
        hg, ag = match["home_goals"], match["away_goals"]
        winner = "home" if hg > ag else "away" if ag > hg else "draw"
        stake = float(r["stake"] or 0) or 1.0
        won = winner in WINNING_RESULTS.get(r["outcome"], {r["outcome"]})
        r["status"] = "ganada" if won else "perdida"
        r["profit"] = f"{(stake * (float(r['odds']) - 1)) if won else -stake:.2f}"
        settled += 1
    return settled


def settle_pending(cfg: dict) -> int:
    rows = read_history(cfg["HISTORY_FILE"])
    now = datetime.now(timezone.utc)
    pending = [r for r in rows if r["status"] == "pendiente"
               and datetime.fromisoformat(r["kickoff"]) + timedelta(hours=2, minutes=30) < now]
    if not pending:
        return 0
    results: list[dict] = []
    if cfg["FOOTBALL_DATA_KEY"]:
        oldest = min(datetime.fromisoformat(r["kickoff"]) for r in pending).astimezone(timezone.utc)
        date_from = max(oldest.date(), (now - timedelta(days=9)).date())  # la API limita el rango a 10 días
        results = fetch_results_football_data(date_from.isoformat(), now.date().isoformat(), cfg)
    if cfg["ODDS_API_KEY"]:
        # Europa League no está en el plan gratis de football-data; también cubre huecos.
        unmatched_sports = {r["sport_key"] for r in pending if r["sport_key"]}
        if cfg["FOOTBALL_DATA_KEY"]:
            unmatched_sports = {s for s in unmatched_sports if s == "soccer_uefa_europa_league"}
        for sk in unmatched_sports:
            results += fetch_scores_odds_api(sk, cfg)
    n = settle_rows(rows, results)
    if n:
        write_history(cfg["HISTORY_FILE"], rows)
    return n


def history_stats(path: str) -> dict:
    rows = [r for r in read_history(path) if r["status"] in ("ganada", "perdida")]
    if not rows:
        return {"n": 0}
    staked = sum(float(r["stake"] or 1) for r in rows)
    profit = sum(float(r["profit"] or 0) for r in rows)
    wins = sum(r["status"] == "ganada" for r in rows)
    expected_wins = sum(float(r["fair_prob"]) for r in rows)
    return {
        "n": len(rows), "wins": wins, "win_rate": wins / len(rows), "expected_wins": expected_wins,
        "profit": profit, "staked": staked, "roi": profit / staked if staked else 0.0,
        "avg_odds": statistics.mean(float(r["odds"]) for r in rows),
    }


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def build_report(reviews: list[MatchReview], picks: list[Pick], cfg: dict, stats: dict,
                 remaining: str | None, today: datetime) -> str:
    e = html.escape
    if cfg.get("MODE") == "seguro":
        return build_safe_report(reviews, picks, cfg, stats, remaining, today)
    lines = [f"<b>📊 Value Betting · {today.strftime('%d/%m/%Y')}</b>",
             f"Partidos revisados: <b>{len(reviews)}</b> · Picks con valor: <b>{len(picks)}</b>", ""]

    if picks:
        lines.append(f"<b>✅ PICKS (EV ≥ {cfg['MIN_EV'] * 100:.1f}%, de mayor a menor)</b>")
        for i, p in enumerate(picks, 1):
            lines += [
                "",
                f"<b>{i}. {e(p.home)} vs {e(p.away)}</b>",
                f"🏆 {e(p.league)} · 🕒 {p.kickoff.strftime('%d/%m %H:%M')}",
                f"🎯 <b>{OUTCOME_LABEL[p.outcome]} – {e(p.selection)}</b> @ <b>{p.odds:.2f}</b> ({e(p.bookmaker)})",
                f"📈 EV {pct(p.ev)} · Prob. justa {p.fair_prob * 100:.1f}% · Cuota justa {p.fair_odds:.2f}",
                f"💰 Stake: {p.stake_pct * 100:.1f}% de banca ≈ {p.stake:.2f} · {p.n_books} casas ({p.source})",
                f"📱 En {e(cfg['MAIN_BOOK'])}: apuesta solo si la cuota es ≥ <b>{p.min_odds:.2f}</b>",
            ]
    else:
        lines.append("❌ Hoy no hay picks con valor suficiente. No apostar también es una decisión ganadora.")

    no_value = [r for r in reviews if not r.picks]
    if no_value:
        lines += ["", "<b>🔎 Revisados sin valor</b>"]
        for r in sorted(no_value, key=lambda r: r.kickoff):
            tag = f"mejor EV {pct(r.best_ev)}" if r.best_ev is not None else "pocas casas"
            lines.append(f"• {r.kickoff.strftime('%H:%M')} {e(r.home)} vs {e(r.away)} — {tag}")

    lines += history_lines(stats, remaining)
    lines += ["", "<i>El valor se mide a largo plazo (cientos de apuestas). Apuesta solo lo que puedas perder. +18</i>"]
    return "\n".join(lines)


def history_lines(stats: dict, remaining: str | None) -> list[str]:
    e = html.escape
    lines = ["", "<b>📒 Historial</b>"]
    if stats.get("n"):
        lines.append(
            f"{stats['n']} picks liquidados · Aciertos {stats['win_rate'] * 100:.1f}% "
            f"(esperado {stats['expected_wins'] / stats['n'] * 100:.1f}%) · ROI {pct(stats['roi'])} · "
            f"Beneficio {stats['profit']:+.2f}"
        )
    else:
        lines.append("Aún no hay picks liquidados.")
    if remaining is not None:
        lines.append(f"<i>Créditos The Odds-API restantes: {e(str(remaining))}</i>")
    return lines


def build_safe_report(reviews: list[MatchReview], picks: list[Pick], cfg: dict, stats: dict,
                      remaining: str | None, today: datetime) -> str:
    e = html.escape
    book = e(cfg["MAIN_BOOK"])
    lines = [f"<b>🛡️ Picks de alta probabilidad · {today.strftime('%d/%m/%Y')}</b>",
             f"Partidos revisados: <b>{len(reviews)}</b> · Picks: <b>{len(picks)}</b>", ""]
    if picks:
        expected_misses = sum(1 - p.fair_prob for p in picks)
        lines += [
            f"De estos {len(picks)} picks, lo esperable es fallar ≈ <b>{expected_misses:.1f}</b>.",
            f"Abre {book} y apuesta <b>solo</b> si su cuota es igual o mayor a la indicada; "
            "si es menor, sáltalo (a la larga pierdes dinero).",
        ]
        for i, p in enumerate(picks, 1):
            ref = f" · Mejor cuota en Europa: {p.odds:.2f} ({e(p.bookmaker)})" if p.odds else ""
            lines += [
                "",
                f"<b>{i}. {e(p.home)} vs {e(p.away)}</b>",
                f"🏆 {e(p.league)} · 🕒 {p.kickoff.strftime('%d/%m %H:%M')}",
                f"🎯 <b>{OUTCOME_LABEL[p.outcome]} – {e(p.selection)}</b>"
                + (" (doble oportunidad)" if "_" in p.outcome else ""),
                f"✅ Probabilidad de acierto: <b>{p.fair_prob * 100:.0f}%</b>",
                f"📱 En {book}: cuota mínima <b>{p.min_odds:.2f}</b>{ref}",
                f"💰 Stake fijo: {p.stake_pct * 100:.0f}% de banca ≈ {p.stake:.2f}",
            ]
        lines += ["", "<i>Nunca los combines en un parlay: 5 picks al 75% aciertan juntos solo el 24% de las veces.</i>"]
    else:
        lines.append(f"❌ Hoy no hay partidos con ≥{cfg['SAFE_MIN_PROB'] * 100:.0f}% de probabilidad "
                     f"que paguen al menos {cfg['SAFE_MIN_ODDS']:.2f}. Hoy no se apuesta.")

    rest = [r for r in reviews if not r.picks]
    if rest:
        lines += ["", "<b>🔎 Revisados sin pick</b>"]
        for r in sorted(rest, key=lambda r: r.kickoff):
            tag = f"máx. {r.best_prob * 100:.0f}% (1X/X2)" if r.best_prob is not None else "pocas casas"
            lines.append(f"• {r.kickoff.strftime('%H:%M')} {e(r.home)} vs {e(r.away)} — {tag}")

    lines += history_lines(stats, remaining)
    lines += ["", "<i>Alta probabilidad no es certeza: una derrota a cuota 1.40 borra 2.5 aciertos. "
              "Apuesta solo lo que puedas perder. +18</i>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Datos de demostración
# ---------------------------------------------------------------------------
def demo_events() -> dict[str, list[dict]]:
    start = (datetime.now(timezone.utc) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)

    def bk(key, title, h, d, a, home, away):
        return {"key": key, "title": title, "markets": [{"key": "h2h", "outcomes": [
            {"name": home, "price": h}, {"name": away, "price": a}, {"name": "Draw", "price": d}]}]}

    def ev(i, sport, home, away, books):
        return {"id": f"demo{i}", "sport_key": sport, "home_team": home, "away_team": away,
                "commence_time": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                "bookmakers": [bk(*b, home, away) for b in books]}

    return {
        "soccer_epl": [ev(1, "soccer_epl", "Arsenal", "Chelsea", [
            ("pinnacle", "Pinnacle", 2.05, 3.55, 3.85), ("bet365", "Bet365", 2.00, 3.50, 3.75),
            ("williamhill", "William Hill", 2.00, 3.40, 4.20), ("unibet", "Unibet", 2.02, 3.45, 3.70),
            ("betfair_ex_eu", "Betfair", 2.04, 3.50, 3.80), ("marathonbet", "Marathon Bet", 2.06, 3.50, 3.78)])],
        "soccer_spain_la_liga": [ev(2, "soccer_spain_la_liga", "Real Betis", "Sevilla", [
            ("pinnacle", "Pinnacle", 2.30, 3.30, 3.35), ("bet365", "Bet365", 2.25, 3.25, 3.20),
            ("codere", "Codere", 2.25, 3.60, 3.20), ("unibet", "Unibet", 2.28, 3.20, 3.25),
            ("betsson", "Betsson", 2.26, 3.25, 3.30)]),
            ev(3, "soccer_spain_la_liga", "Getafe", "Osasuna", [
                ("bet365", "Bet365", 2.40, 3.00, 3.30), ("unibet", "Unibet", 2.45, 2.95, 3.25)])],
        "soccer_italy_serie_a": [ev(4, "soccer_italy_serie_a", "Inter", "Torino", [
            ("pinnacle", "Pinnacle", 1.45, 4.70, 7.40), ("bet365", "Bet365", 1.44, 4.60, 7.00),
            ("williamhill", "William Hill", 1.42, 4.60, 7.50), ("unibet", "Unibet", 1.45, 4.50, 7.20),
            ("betsson", "Betsson", 1.44, 4.75, 7.10)])],
    }


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def run(cfg: dict, demo: bool = False, telegram: bool = True) -> str:
    tz = get_tz(cfg["TIMEZONE"])
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=cfg["HOURS_AHEAD"])

    if not demo:
        if not cfg["ODDS_API_KEY"]:
            raise RuntimeError("Falta ODDS_API_KEY (ponla como secreto en GitHub o ejecuta setup.py).")
        try:
            n = settle_pending(cfg)
            if n:
                print(f"✔ {n} picks anteriores liquidados.")
        except Exception as exc:  # la liquidación nunca debe impedir el informe
            print(f"⚠️  No se pudieron liquidar picks: {exc}")

    reviews: list[MatchReview] = []
    remaining = None
    data = demo_events() if demo else None
    for sport_key, league in cfg["LEAGUES"].items():
        if demo:
            events = data.get(sport_key, [])
        else:
            try:
                events, remaining = fetch_odds(sport_key, cfg)
            except RuntimeError:
                raise
            except Exception as exc:
                print(f"⚠️  {league}: error al descargar cuotas ({exc})")
                continue
        for event in events:
            ko = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            if not (now <= ko <= horizon):
                continue
            event.setdefault("sport_key", sport_key)
            review = analyze_event(event, league, cfg, tz)
            reviews.append(review)

    sort_key = (lambda p: p.fair_prob) if cfg.get("MODE") == "seguro" else (lambda p: p.ev)
    picks = sorted((p for r in reviews for p in r.picks), key=sort_key, reverse=True)[: cfg["MAX_PICKS"]]

    if not demo:
        record_picks(picks, cfg["HISTORY_FILE"])
    stats = history_stats(cfg["HISTORY_FILE"]) if not demo else {"n": 0}
    report = build_report(reviews, picks, cfg, stats, remaining, now.astimezone(tz))

    if telegram:
        send_telegram(report, cfg)
        print("✔ Informe enviado a Telegram.")
    return report


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analizador de apuestas de valor (1X2).")
    parser.add_argument("--demo", action="store_true", help="usar datos de ejemplo (sin APIs)")
    parser.add_argument("--no-telegram", action="store_true", help="no enviar a Telegram, solo mostrar")
    parser.add_argument("--stats", action="store_true", help="mostrar estadísticas del historial")
    parser.add_argument("--modo", choices=["seguro", "valor"], help="forzar modo para esta ejecución")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config()
    if args.modo:
        cfg["MODE"] = args.modo
    if args.stats:
        s = history_stats(cfg["HISTORY_FILE"])
        if not s["n"]:
            print("Aún no hay picks liquidados.")
            return
        print(f"Picks liquidados: {s['n']}  |  Ganadas: {s['wins']}  ({s['win_rate'] * 100:.1f}%, "
              f"esperado {s['expected_wins'] / s['n'] * 100:.1f}%)")
        print(f"Apostado: {s['staked']:.2f}  |  Beneficio: {s['profit']:+.2f}  |  ROI: {pct(s['roi'])}  |  "
              f"Cuota media: {s['avg_odds']:.2f}")
        return

    try:
        report = run(cfg, demo=args.demo, telegram=not (args.no_telegram or args.demo))
    except RuntimeError as exc:
        sys.exit(f"❌ {exc}")
    print(strip_html(report))


if __name__ == "__main__":
    main()
