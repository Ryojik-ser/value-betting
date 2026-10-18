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
import math
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
        "soccer_mexico_ligamx": "Liga MX",
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
    # Parlays sugeridas (se arman con los picks de alta probabilidad del día)
    "STAKE_AMOUNT": 5.0,          # monto fijo por apuesta sencilla (0 = usar SAFE_STAKE_PCT)
    "PARLAY_STAKE": 5.0,          # cuánto pondrías en cada parlay
    "PARLAY_MAX_LEGS": 3,         # máximo de selecciones por parlay
    "PARLAY_MIN_PROB": 0.35,      # no sugerir parlays con menos de 35 % de probabilidad
    "LEG_MIN_PROB": 0.60,         # cada selección de parlay debe tener >= 60 %
    "LEG_MIN_ODDS": 1.15,         # ...y pagar al menos 1.15 (-667)
    "LEG_MAX_ODDS": 1.75,         # ...y como máximo 1.75 (-133)
    # No favoritos con respaldo: selecciones del equipo NO favorito con buena probabilidad y buen pago
    "DOG_MIN_PROB": 0.55,         # probabilidad mínima
    "DOG_MIN_ODDS": 1.45,         # que pague al menos 1.45 (-222)
    "DOG_MAX": 4,                 # cuántas mostrar
    # Apartado de tu equipo y jugador favoritos
    "FOCUS_TEAM": "Manchester City",
    "FOCUS_PLAYER": "Erling Haaland",
    "FOCUS_PLAYER_SHARE": 0.33,   # parte de los goles del equipo que anota (se recalcula con football-data)
    "FOCUS_PROPS": True,          # descargar momios de goleador (≈2 créditos por partido del equipo)
    "FOCUS_DAYS": 7,              # muestra el próximo partido aunque falten hasta 7 días
    "MARKETS": "h2h,totals",      # mercados a descargar (ganador + total de goles)
    "CURRENCY": "$",
    "REGIONS": "eu",              # eu incluye Pinnacle; puedes usar "eu,uk"
    "HOURS_AHEAD": 24,            # ventana de partidos a analizar
    "MIN_EV": 0.02,               # EV mínimo (2 %). 0 = cualquier valor positivo
    "MAX_PICKS": 5,               # picks sencillos máximos por día
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
        # Quita espacios, saltos de línea y comillas que se cuelan al copiar desde el celular.
        cfg[k] = re.sub(r"\s+", "", str(cfg.get(k, "") or "")).strip("\"'")
        if cfg[k].startswith(PLACEHOLDER_PREFIX):
            cfg[k] = ""
    # Las claves de The Odds-API son hexadecimales en minúscula; el teclado del celular
    # a veces pone mayúsculas al pegar.
    if re.fullmatch(r"[0-9a-fA-F]+", cfg["ODDS_API_KEY"]):
        cfg["ODDS_API_KEY"] = cfg["ODDS_API_KEY"].lower()
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
    event_id: str = ""
    fair: list[float] | None = None     # prob. justa [local, empate, visitante]
    p_over25: float | None = None       # prob. justa de más de 2.5 goles
    best_prices: dict | None = None     # mejor cuota europea por resultado: {"home": (cuota, casa), ...}


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


def _totals_25(bookmaker: dict) -> tuple[float, float] | None:
    """Cuotas (más, menos) de 2.5 goles de una casa, si las ofrece."""
    for market in bookmaker.get("markets", []):
        if market.get("key") != "totals":
            continue
        over = under = None
        for o in market.get("outcomes", []):
            if abs(float(o.get("point", 0)) - 2.5) > 1e-9 or not o.get("price"):
                continue
            if o.get("name") == "Over":
                over = float(o["price"])
            elif o.get("name") == "Under":
                under = float(o["price"])
        if over and under and over > 1 and under > 1:
            return over, under
    return None


def fair_over25(event: dict, sharp: str, weight: float) -> float | None:
    probs, sharp_p = [], None
    for bk in event.get("bookmakers", []):
        t = _totals_25(bk)
        if not t:
            continue
        p = remove_vig(list(t))[0]
        if bk["key"] == sharp:
            sharp_p = p
        else:
            probs.append(p)
    if not probs and sharp_p is None:
        return None
    cons = statistics.median(probs) if probs else sharp_p
    return weight * sharp_p + (1 - weight) * cons if sharp_p is not None else cons


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

    review.event_id, review.fair = event["id"], fair
    review.best_prices = {}
    for o in order:
        cands = [(p[o], titles[k]) for k, p in all_prices.items() if k != sharp]
        if cands:
            review.best_prices[o] = max(cands)
    review.p_over25 = fair_over25(event, sharp, cfg["SHARP_WEIGHT"])

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
    amount = cfg.get("STAKE_AMOUNT") or 0
    if amount > 0:
        stake = float(amount)
        pct_stake = stake / cfg["BANKROLL"] if cfg["BANKROLL"] else 0.0
    else:
        pct_stake = cfg["SAFE_STAKE_PCT"]
        stake = round(pct_stake * cfg["BANKROLL"], 2)
    return Pick(
        **base, outcome=outcome, selection=selection, odds=ref_odds, bookmaker=ref_book,
        fair_prob=prob, fair_odds=fair_odds, ev=0.0, stake_pct=pct_stake,
        stake=stake, min_odds=fair_odds, kind="seguro",
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
        params={"apiKey": cfg["ODDS_API_KEY"], "regions": cfg["REGIONS"], "markets": cfg.get("MARKETS", "h2h"),
                "oddsFormat": "decimal", "dateFormat": "iso"},
        timeout=30,
    )
    if r.status_code == 422 and cfg.get("MARKETS", "h2h") != "h2h":
        return fetch_odds(sport_key, {**cfg, "MARKETS": "h2h"})
    if r.status_code == 404:
        return [], r.headers.get("x-requests-remaining")
    if r.status_code == 401:
        raise RuntimeError(f"The Odds-API rechazó la API Key: {odds_key_diagnosis(cfg['ODDS_API_KEY'], r)}")
    if r.status_code == 429:
        raise RuntimeError("Cuota de The Odds-API agotada este mes.")
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining")


def odds_key_diagnosis(key: str, response) -> str:
    """Explica por qué falló la clave sin mostrarla."""
    try:
        api_msg = response.json().get("message", "")
    except Exception:
        api_msg = response.text[:150]
    problems = []
    if len(key) != 32:
        problems.append(f"tiene {len(key)} caracteres y debe tener 32")
    bad = sorted({c for c in key if c not in "0123456789abcdef"})
    if bad:
        problems.append("contiene caracteres que no van en la clave: " + " ".join(repr(c) for c in bad)
                        + " (solo lleva números y letras a-f en minúscula; ¿una O en vez de cero?)")
    hint = "; ".join(problems) if problems else "el formato es correcto; revisa que sea la clave del último correo"
    return f"{hint}. Respuesta de la API: {api_msg or response.status_code}"


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
    if r.status_code in (401, 404):
        raise RuntimeError("Telegram rechazó el token: revisa el secreto TELEGRAM_TOKEN (cópialo completo de "
                           "@BotFather; distingue mayúsculas y minúsculas).")
    if r.ok:
        for upd in reversed(r.json().get("result", [])):
            chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
            if chat:
                return str(chat["id"])
    return ""


def chat_id_file(cfg: dict) -> Path:
    return Path(cfg["HISTORY_FILE"]).parent / "telegram_chat_id.txt"


def send_telegram(text: str, cfg: dict) -> None:
    token, chat = cfg["TELEGRAM_TOKEN"], cfg["TELEGRAM_CHAT_ID"]
    saved = chat_id_file(cfg)
    if token and not chat and saved.exists():
        chat = saved.read_text(encoding="utf-8").strip()
    if token and not chat:
        # Telegram solo guarda los mensajes 24 h: se detecta una vez y se guarda.
        chat = discover_chat_id(token)
        if chat:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(chat, encoding="utf-8")
            print(f"ℹ️  Chat ID detectado y guardado en {saved.name}.")
    if not token:
        raise RuntimeError("Falta TELEGRAM_TOKEN.")
    if not chat:
        raise RuntimeError("El token de Telegram es válido, pero tu bot aún no tiene mensajes. Abre "
                           "t.me/ClaudepuestasEVbot, pulsa Iniciar, escribe 'hola' y vuelve a ejecutar.")
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
        # Europa League y Liga MX no están en el plan gratis de football-data.
        unmatched_sports = {r["sport_key"] for r in pending if r["sport_key"]}
        if cfg["FOOTBALL_DATA_KEY"]:
            unmatched_sports = {s for s in unmatched_sports
                                if s in ("soccer_uefa_europa_league", "soccer_mexico_ligamx")}
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
def underdog_picks(reviews: list[MatchReview], cfg: dict) -> list[dict]:
    """Por partido, la mejor selección del equipo NO favorito que tenga buena probabilidad
    y buen pago. Si las casas europeas ya pagan por encima de la cuota justa, se marca ⭐."""
    out = []
    for r in reviews:
        if not r.fair:
            continue
        ph, pd, pa = r.fair
        dog_side = "away" if ph >= pa else "home"
        dog = r.away if dog_side == "away" else r.home
        p_win = pa if dog_side == "away" else ph
        m = score_matrix(*fit_goals(r.fair, r.p_over25))
        cut = 0.02 if r.p_over25 is not None else 0.04

        def P(cond):
            return sum(v for i, row in enumerate(m) for j, v in enumerate(row) if cond(i, j))

        dog_goals = (lambda i, j: j) if dog_side == "away" else (lambda i, j: i)
        fav_goals = (lambda i, j: i) if dog_side == "away" else (lambda i, j: j)
        bp = r.best_prices or {}
        dc_price = None
        if dog_side in bp and "draw" in bp:  # doble oportunidad equivalente con las mejores cuotas
            dc_price = 1 / (1 / bp[dog_side][0] + 1 / bp["draw"][0])
        options = [
            ("1X2", f"Gana {dog}", p_win, bp.get(dog_side, (None,))[0]),
            ("Doble oportunidad", f"{dog} o empate", p_win + pd, dc_price),
            ("Hándicap", f"{dog} +1.5 (no pierde por 2 o más)",
             P(lambda i, j: dog_goals(i, j) - fav_goals(i, j) >= -1) - cut, None),
            (f"Total goles {dog}", f"{dog} más de 0.5 goles", P(lambda i, j: dog_goals(i, j) >= 1) - cut, None),
        ]
        best = None
        for market, sel, prob, price in options:
            if not (0 < prob < 1) or prob < cfg["DOG_MIN_PROB"] or 1 / prob < cfg["DOG_MIN_ODDS"]:
                continue
            ev = price * prob - 1 if price else None
            leg = Leg(r.event_id, r.league, r.kickoff, r.home, r.away, market, sel, prob,
                      "mercado" if market in ("1X2", "Doble oportunidad") else "modelo")
            cand = {"leg": leg, "market_odds": price, "ev": ev}
            key = (ev is not None and ev >= 0.02, ev or 0, 1 / prob)
            if best is None or key > best[0]:
                best = (key, cand)
        if best:
            out.append(best[1])
    out.sort(key=lambda c: (c["ev"] is not None and c["ev"] >= 0.02, c["ev"] or 0, c["leg"].min_odds), reverse=True)
    return out[: cfg["DOG_MAX"]]


def underdog_lines(reviews: list[MatchReview], cfg: dict) -> list[str]:
    picks = underdog_picks(reviews, cfg)
    if not picks:
        return []
    e = html.escape
    cur = e(cfg.get("CURRENCY", "$"))
    stake = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    lines = ["", "<b>🐺 NO FAVORITOS CON RESPALDO</b>",
             "El público carga el dinero al favorito y las casas le bajan su pago; aquí el no favorito "
             "tiene buena probabilidad y paga más."]
    for c in picks:
        lg = c["leg"]
        tag = "" if lg.source == "mercado" else " ·est."
        lines += ["",
                  f"• <b>{e(lg.selection)}</b> — mín. {odds_txt(lg.min_odds)} ({lg.prob * 100:.0f}%{tag})",
                  f"   {e(lg.home)} vs {e(lg.away)} · {lg.kickoff.strftime('%d/%m %H:%M')} · {e(lg.league)} · {e(lg.market)}",
                  f"   💵 Con {cur}{stake:.0f} ganas ≥ {cur}{stake * (lg.min_odds - 1):.2f}"]
        if c["market_odds"]:
            if c["ev"] is not None and c["ev"] >= 0.02:
                lines.append(f"   ⭐ Casas europeas ya pagan hasta {american(c['market_odds'])}: "
                             f"valor {pct(c['ev'])} sobre la probabilidad real")
            else:
                lines.append(f"   Mejor pago en Europa: {american(c['market_odds'])} (sin valor extra; "
                             f"busca que {e(cfg['MAIN_BOOK'])} pague más que el mínimo)")
    lines.append("<i>Pagan más porque fallan más seguido que un favorito: úsalos como sencillas o "
                 "como 1 selección dentro de una doble, no varios juntos.</i>")
    return lines


def american(decimal_odds: float) -> str:
    """Convierte cuota decimal a momio americano (+150, -250...)."""
    if decimal_odds <= 1.0:
        return "—"
    if decimal_odds >= 2.0:
        return f"+{round((decimal_odds - 1) * 100)}"
    return f"-{round(100 / (decimal_odds - 1))}"


def odds_txt(decimal_odds: float) -> str:
    """Momio americano con la cuota decimal entre paréntesis."""
    return f"{american(decimal_odds)} ({decimal_odds:.2f})"


# ---------------------------------------------------------------------------
# Armador de parlays: modelo de goles (Poisson) calibrado con el mercado
# ---------------------------------------------------------------------------
@dataclass
class Leg:
    event_id: str
    league: str
    kickoff: datetime
    home: str
    away: str
    market: str        # nombre del mercado como aparece en BetMéxico
    selection: str
    prob: float
    source: str        # "mercado" (cuotas directas) o "modelo" (estimado de goles)

    @property
    def min_odds(self) -> float:
        return 1 / self.prob


_PMF_CACHE: dict[float, list[float]] = {}


def _pmf(lam: float, n: int = 10) -> list[float]:
    key = round(lam, 3)
    if key not in _PMF_CACHE:
        vals, term = [], math.exp(-lam)
        for k in range(n + 1):
            vals.append(term)
            term *= lam / (k + 1)
        _PMF_CACHE[key] = vals
    return _PMF_CACHE[key]


def score_matrix(lh: float, la: float) -> list[list[float]]:
    ph, pa = _pmf(lh), _pmf(la)
    return [[x * y for y in pa] for x in ph]


def _summary(m: list[list[float]]) -> tuple[float, float, float, float]:
    home = draw = away = over = 0.0
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            if i > j:
                home += v
            elif i == j:
                draw += v
            else:
                away += v
            if i + j >= 3:
                over += v
    return home, draw, away, over


def fit_goals(fair: list[float], p_over25: float | None) -> tuple[float, float]:
    """Goles esperados (local, visitante) que mejor reproducen las cuotas del mercado."""
    def err(lh, la):
        h, d, a, o = _summary(score_matrix(lh, la))
        e = (h - fair[0]) ** 2 + (d - fair[1]) ** 2 + (a - fair[2]) ** 2
        return e + ((o - p_over25) ** 2 if p_over25 is not None else 0.0)

    grid = [x / 10 for x in range(2, 40)]
    lh, la = min(((x, y) for x in grid for y in grid), key=lambda t: err(*t))
    fine = [d / 50 for d in range(-5, 6)]
    lh, la = min(((max(0.05, lh + dx), max(0.05, la + dy)) for dx in fine for dy in fine), key=lambda t: err(*t))
    return lh, la


def market_legs(review: MatchReview, cfg: dict) -> list[Leg]:
    """Todas las selecciones posibles de un partido con su probabilidad."""
    if not review.fair:
        return []
    ph, pd, pa = review.fair
    po = review.p_over25
    m = score_matrix(*fit_goals(review.fair, po))
    cells = [(i, j, v) for i, row in enumerate(m) for j, v in enumerate(row)]

    def P(cond):
        return sum(v for i, j, v in cells if cond(i, j))

    # Recorte prudente a lo estimado por el modelo (más grande si no hay mercado de goles)
    cut = 0.02 if po is not None else 0.04
    h, a = review.home, review.away
    raw = [
        ("1X2", f"Gana {h}", ph, "mercado"),
        ("1X2", f"Gana {a}", pa, "mercado"),
        ("Doble oportunidad", f"{h} o empate", ph + pd, "mercado"),
        ("Doble oportunidad", f"{a} o empate", pa + pd, "mercado"),
        ("Total de goles", "Más de 1.5 goles", P(lambda i, j: i + j >= 2) - cut, "modelo"),
        ("Total de goles", "Menos de 3.5 goles", P(lambda i, j: i + j <= 3) - cut, "modelo"),
        ("Ambos equipos anotan", "Sí", P(lambda i, j: i >= 1 and j >= 1) - cut, "modelo"),
        ("Ambos equipos anotan", "No", P(lambda i, j: i == 0 or j == 0) - cut, "modelo"),
        (f"Total goles {h}", f"{h} más de 0.5 goles", P(lambda i, j: i >= 1) - cut, "modelo"),
        (f"Total goles {a}", f"{a} más de 0.5 goles", P(lambda i, j: j >= 1) - cut, "modelo"),
        ("Crear apuesta", f"Gana {h} + más de 1.5 goles", P(lambda i, j: i > j and i + j >= 2) - cut, "modelo"),
        ("Crear apuesta", f"Gana {a} + más de 1.5 goles", P(lambda i, j: j > i and i + j >= 2) - cut, "modelo"),
    ]
    if po is not None:
        raw += [("Total de goles", "Más de 2.5 goles", po, "mercado"),
                ("Total de goles", "Menos de 2.5 goles", 1 - po, "mercado")]
    return [Leg(review.event_id, review.league, review.kickoff, h, a, mk, sel, p, src)
            for mk, sel, p, src in raw if 0 < p < 1]


def valid_legs(review: MatchReview, cfg: dict) -> list[Leg]:
    return [lg for lg in market_legs(review, cfg)
            if lg.prob >= cfg["LEG_MIN_PROB"] and cfg["LEG_MIN_ODDS"] <= lg.min_odds <= cfg["LEG_MAX_ODDS"]]


def build_parlays(reviews: list[MatchReview], cfg: dict) -> list[dict]:
    """Parlays con una selección por partido. Para cada objetivo de pago elige la
    combinación con MENOS selecciones (menos margen de la casa) y más probabilidad."""
    from itertools import combinations
    from math import prod

    pool = sorted((lg for r in reviews for lg in valid_legs(r, cfg)), key=lambda lg: lg.prob, reverse=True)[:40]
    if len({lg.event_id for lg in pool}) < 2:
        return []
    max_legs = max(2, int(cfg["PARLAY_MAX_LEGS"]))
    combos = [c for n in range(2, max_legs + 1) for c in combinations(pool, n)
              if len({lg.event_id for lg in c}) == n]  # una selección por partido

    def make(name, legs):
        prob = prod(lg.prob for lg in legs)
        return {"name": name, "legs": list(legs), "prob": prob, "odds": 1 / prob}

    first = pool[0]
    second = next(lg for lg in pool if lg.event_id != first.event_id)
    out = [make("🛡️ Doble más segura", [first, second])]
    for name, target in (("💵 Para doblar tus $ (≥ +100)", 2.0), ("🚀 Estilo de tu ticket (≥ +145)", 2.45)):
        cands = [c for c in combos if 1 / prod(lg.prob for lg in c) >= target
                 and prod(lg.prob for lg in c) >= cfg["PARLAY_MIN_PROB"]]
        if not cands:
            continue
        best = min(cands, key=lambda c: (len(c), 1 / prod(lg.prob for lg in c)))
        if all({id(x) for x in best} != {id(x) for x in o["legs"]} for o in out):
            out.append(make(name.replace("$", cfg.get("CURRENCY", "$") + f"{cfg['PARLAY_STAKE']:.0f}"), best))
    return out


def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ---------------------------------------------------------------------------
# Apartado del equipo y jugador favoritos (por defecto: Manchester City y Haaland)
# ---------------------------------------------------------------------------
def fetch_player_props(sport_key: str, event_id: str, cfg: dict) -> list[tuple[float, str]]:
    """Momios de 'anota en cualquier momento' del jugador (2 créditos: regiones uk y eu)."""
    r = _requests().get(
        f"{ODDS_API_URL}/sports/{sport_key}/events/{event_id}/odds",
        params={"apiKey": cfg["ODDS_API_KEY"], "regions": "uk,eu", "markets": "player_goal_scorer_anytime",
                "oddsFormat": "decimal"},
        timeout=30,
    )
    if not r.ok:
        return []
    surname = cfg["FOCUS_PLAYER"].split()[-1].lower()
    prices = []
    for bk in r.json().get("bookmakers", []):
        for mk in bk.get("markets", []):
            for o in mk.get("outcomes", []):
                who = f"{o.get('description', '')} {o.get('name', '')}".lower()
                if surname in who and str(o.get("name", "")).lower() != "no" and o.get("price", 0) > 1:
                    prices.append((float(o["price"]), bk.get("title", bk.get("key", ""))))
    return prices


def fetch_player_season(cfg: dict) -> dict | None:
    """Goles del jugador y del equipo en la Premier esta temporada (football-data.org, gratis)."""
    if not cfg.get("FOOTBALL_DATA_KEY"):
        return None
    req, hdr = _requests(), {"X-Auth-Token": cfg["FOOTBALL_DATA_KEY"]}
    try:
        sc = req.get(f"{FOOTBALL_DATA_URL}/competitions/PL/scorers", headers=hdr, params={"limit": 50}, timeout=30)
        st = req.get(f"{FOOTBALL_DATA_URL}/competitions/PL/standings", headers=hdr, timeout=30)
        if not (sc.ok and st.ok):
            return None
        surname = cfg["FOCUS_PLAYER"].split()[-1].lower()
        player = next((x for x in sc.json().get("scorers", []) if surname in x["player"]["name"].lower()), None)
        table = st.json()["standings"][0]["table"]
        team = next((t for t in table if _same_team(t["team"]["name"], cfg["FOCUS_TEAM"])), None)
        if not player or not team or not team.get("goalsFor"):
            return None
        return {"goals": player.get("goals") or 0, "played": player.get("playedMatches") or 0,
                "penalties": player.get("penalties") or 0, "team_goals": team["goalsFor"],
                "team_played": team.get("playedGames") or 0, "position": team.get("position")}
    except Exception:
        return None


def focus_analysis(review: MatchReview, cfg: dict, props: list[tuple[float, str]] | None = None,
                   season: dict | None = None) -> dict | None:
    """Probabilidades de los picks del equipo y del jugador favoritos en un partido."""
    if not review.fair:
        return None
    team = cfg["FOCUS_TEAM"]
    home_side = _same_team(review.home, team)
    rival = review.away if home_side else review.home
    share = cfg["FOCUS_PLAYER_SHARE"]
    if season and season["team_goals"] >= 5:
        share = min(0.6, max(0.1, season["goals"] / season["team_goals"]))
    lh, la = fit_goals(review.fair, review.p_over25)
    m = score_matrix(lh, la)
    cut = 0.02 if review.p_over25 is not None else 0.04

    def tg(i, j):
        return i if home_side else j

    def og(i, j):
        return j if home_side else i

    def P(fn):
        return sum(v * fn(i, j) for i, row in enumerate(m) for j, v in enumerate(row))

    def scores(n):  # P(el jugador marca al menos 1 de los n goles de su equipo)
        return 1 - (1 - share) ** n

    def brace(n):   # P(al menos 2)
        return 1 - (1 - share) ** n - n * share * (1 - share) ** max(n - 1, 0) if n >= 2 else 0.0

    ph, pd, pa = review.fair
    p_win = ph if home_side else pa
    player = cfg["FOCUS_PLAYER"].split()[-1]
    p_any = P(lambda i, j: scores(tg(i, j))) - cut
    market = None
    if props:
        best_price, best_book = max(props)
        mkt_prob = statistics.median(1 / p for p, _ in props) * 0.93  # quita el margen típico del mercado
        p_any = 0.5 * p_any + 0.5 * mkt_prob
        market = {"best": best_price, "book": best_book, "n": len(props), "ev": best_price * p_any - 1}
    picks = [
        ("1X2", f"Gana {team}", p_win, "mercado"),
        ("Doble oportunidad", f"{team} o empate", p_win + pd, "mercado"),
        (f"Total goles {team}", f"{team} más de 1.5 goles", P(lambda i, j: tg(i, j) >= 2) - cut, "modelo"),
        ("Crear apuesta", f"Gana {team} + más de 1.5 goles",
         P(lambda i, j: tg(i, j) > og(i, j) and i + j >= 2) - cut, "modelo"),
        ("Portería a cero", f"{team} no recibe gol", P(lambda i, j: og(i, j) == 0) - cut, "modelo"),
        ("Goleador", f"{player} anota en cualquier momento", p_any, "mercado" if props else "modelo"),
        ("Goleador", f"{player} anota 2 o más", P(lambda i, j: brace(tg(i, j))) - cut, "modelo"),
        ("Crear apuesta", f"Gana {team} + {player} anota",
         P(lambda i, j: scores(tg(i, j)) if tg(i, j) > og(i, j) else 0.0) - cut * 1.5, "modelo"),
    ]
    return {"review": review, "rival": rival, "home": home_side, "xg": (lh, la) if home_side else (la, lh),
            "share": share, "season": season, "market": market,
            "picks": [(mk, sel, p, src) for mk, sel, p, src in picks if 0 < p < 1]}


def focus_lines(fa: dict | None, cfg: dict, next_only: bool = False) -> list[str]:
    e = html.escape
    team, player = cfg["FOCUS_TEAM"], cfg["FOCUS_PLAYER"]
    if not fa:
        return ["", f"<b>🩵 APARTADO {e(team.upper())}</b>",
                f"No hay partido del {e(team)} con momios en los próximos {cfg['FOCUS_DAYS']} días."]
    r = fa["review"]
    cur = e(cfg.get("CURRENCY", "$"))
    stake = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    ph, pd, pa = r.fair
    p_team, p_riv = (ph, pa) if fa["home"] else (pa, ph)
    lines = ["", f"<b>🩵 APARTADO {e(team.upper())} · {e(player.upper())}</b>",
             f"{'📅 Próximo partido' if next_only else '⚽ Hoy'}: <b>{e(r.home)} vs {e(r.away)}</b> · "
             f"{r.kickoff.strftime('%d/%m %H:%M')} · {e(r.league)}",
             f"📊 {e(team)} {p_team * 100:.0f}% · empate {pd * 100:.0f}% · {e(fa['rival'])} {p_riv * 100:.0f}%",
             f"⚽ Goles esperados: {e(team)} {fa['xg'][0]:.1f} – {fa['xg'][1]:.1f} {e(fa['rival'])}"]
    sz = fa["season"]
    if sz:
        lines.append(f"📈 {e(player)} en Premier: {sz['goals']} goles en {sz['played']} partidos "
                     f"({sz['penalties']} de penal) · {fa['share'] * 100:.0f}% de los goles del {e(team)}")
    else:
        lines.append(f"📈 {e(player)} anota ≈{fa['share'] * 100:.0f}% de los goles del {e(team)} (valor de "
                     "referencia; con la clave de football-data se calcula con la temporada actual)")
    lines.append("")
    for mk, sel, p, src in sorted(fa["picks"], key=lambda x: x[2], reverse=True):
        icon = "✅" if p >= 0.60 else "⚠️" if p >= 0.45 else "🎲"
        tag = "" if src == "mercado" else " ·est."
        lines.append(f"{icon} <b>{e(sel)}</b> — {p * 100:.0f}%{tag} · mín. {odds_txt(1 / p)} · "
                     f"con {cur}{stake:.0f} ganas ≥ {cur}{stake * (1 / p - 1):.2f}")
    mkt = fa["market"]
    if mkt:
        flag = (f"⭐ valor {pct(mkt['ev'])}" if mkt["ev"] >= 0.02 else "sin valor extra")
        lines.append(f"🎯 Goleador en casas ({mkt['n']} momios): mejor {american(mkt['best'])} en "
                     f"{e(mkt['book'])} → {flag}")
    lines += [
        f"<i>✅ ≥60% · ⚠️ 45–60% · 🎲 menos de 45%. Confirma que {e(player.split()[-1])} sea titular "
        "1 h antes; si no juega, olvida sus picks. «Gana + anota» suele pagar más en BetMéxico si "
        "cumple el mínimo.</i>",
    ]
    return lines


def build_report(reviews: list[MatchReview], picks: list[Pick], cfg: dict, stats: dict,
                 remaining: str | None, today: datetime, focus: list[str] | None = None) -> str:
    e = html.escape
    if cfg.get("MODE") == "seguro":
        return build_safe_report(reviews, picks, cfg, stats, remaining, today, focus)
    lines = [f"<b>📊 Value Betting · {today.strftime('%d/%m/%Y')}</b>",
             f"Partidos revisados: <b>{len(reviews)}</b> · Picks con valor: <b>{len(picks)}</b>", ""]
    lines += (focus or []) + ([""] if focus else [])

    if picks:
        lines.append(f"<b>✅ PICKS (EV ≥ {cfg['MIN_EV'] * 100:.1f}%, de mayor a menor)</b>")
        for i, p in enumerate(picks, 1):
            lines += [
                "",
                f"<b>{i}. {e(p.home)} vs {e(p.away)}</b>",
                f"🏆 {e(p.league)} · 🕒 {p.kickoff.strftime('%d/%m %H:%M')}",
                f"🎯 <b>{OUTCOME_LABEL[p.outcome]} – {e(p.selection)}</b> @ <b>{odds_txt(p.odds)}</b> ({e(p.bookmaker)})",
                f"📈 EV {pct(p.ev)} · Prob. justa {p.fair_prob * 100:.1f}% · Momio justo {american(p.fair_odds)}",
                f"💰 Stake: {p.stake_pct * 100:.1f}% de banca ≈ {p.stake:.2f} · {p.n_books} casas ({p.source})",
                f"📱 En {e(cfg['MAIN_BOOK'])}: apuesta solo si el momio paga ≥ <b>{odds_txt(p.min_odds)}</b>",
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
                      remaining: str | None, today: datetime, focus: list[str] | None = None) -> str:
    e = html.escape
    book = e(cfg["MAIN_BOOK"])
    cur = e(cfg.get("CURRENCY", "$"))
    lines = [f"<b>🛡️ Picks de alta probabilidad · {today.strftime('%d/%m/%Y')}</b>",
             f"Partidos revisados: <b>{len(reviews)}</b> · Picks: <b>{len(picks)}</b>"]
    lines += (focus or []) + [""]
    if picks:
        expected_misses = sum(1 - p.fair_prob for p in picks)
        lines += [
            f"De estos {len(picks)} picks, lo esperable es fallar ≈ <b>{expected_misses:.1f}</b>.",
            f"Abre {book} y apuesta <b>solo</b> si su momio paga igual o más que el indicado "
            "(ej. -300 paga más que -400); si paga menos, sáltalo.",
        ]
        for i, p in enumerate(picks, 1):
            ref = f" · Mejor en Europa: {american(p.odds)} ({e(p.bookmaker)})" if p.odds else ""
            lines += [
                "",
                f"<b>{i}. {e(p.home)} vs {e(p.away)}</b>",
                f"🏆 {e(p.league)} · 🕒 {p.kickoff.strftime('%d/%m %H:%M')}",
                f"🎯 <b>{OUTCOME_LABEL[p.outcome]} – {e(p.selection)}</b>"
                + (" (doble oportunidad)" if "_" in p.outcome else ""),
                f"✅ Probabilidad de acierto: <b>{p.fair_prob * 100:.0f}%</b>",
                f"📱 En {book}: momio mínimo <b>{odds_txt(p.min_odds)}</b>{ref}",
                f"💰 Stake sencillo: {cur}{p.stake:.2f} ({p.stake_pct * 100:.0f}% de banca) · "
                f"si aciertas ganas ≥ {cur}{p.stake * (p.min_odds - 1):.2f}",
            ]
            if p.odds and p.odds > p.min_odds:
                lines.append(f"⭐ <b>Seguro + valor</b>: el mercado ya paga {pct(p.odds / p.min_odds - 1)} "
                             "por encima de la cuota justa")
    else:
        lines.append(f"❌ Hoy no hay sencillas con ≥{cfg['SAFE_MIN_PROB'] * 100:.0f}% de probabilidad "
                     f"que paguen al menos {cfg['SAFE_MIN_ODDS']:.2f}.")
    lines += parlay_lines(reviews, cfg)
    lines += underdog_lines(reviews, cfg)

    rest = [r for r in reviews if not r.picks]
    if rest:
        lines += ["", "<b>🔎 Revisados sin pick</b>"]
        for r in sorted(rest, key=lambda r: r.kickoff):
            tag = f"máx. {r.best_prob * 100:.0f}% (1X/X2)" if r.best_prob is not None else "pocas casas"
            lines.append(f"• {r.kickoff.strftime('%H:%M')} {e(r.home)} vs {e(r.away)} — {tag}")

    lines += history_lines(stats, remaining)
    lines += ["", "<i>Alta probabilidad no es certeza: una derrota a momio -250 borra 2.5 aciertos. "
              "Apuesta solo lo que puedas perder. +18</i>"]
    return "\n".join(lines)


def parlay_lines(reviews: list[MatchReview], cfg: dict) -> list[str]:
    e = html.escape
    cur = e(cfg.get("CURRENCY", "$"))
    stake = cfg["PARLAY_STAKE"]
    parlays = build_parlays(reviews, cfg)
    if not parlays:
        return []
    lines = ["", "<b>🎰 PARLAYS SUGERIDAS</b>",
             f"Cada selección debe cumplir su momio mínimo en {e(cfg['MAIN_BOOK'])}; si una no lo cumple, "
             "cámbiala o arma una doble."]
    for par in parlays:
        payout = stake * par["odds"]
        lines += ["", f"<b>{par['name']}</b> · {len(par['legs'])} selecciones"]
        for lg in par["legs"]:
            tag = "" if lg.source == "mercado" else " ·est."
            lines.append(f"• <b>{e(lg.selection)}</b> — mín. {american(lg.min_odds)} ({lg.prob * 100:.0f}%{tag})\n"
                         f"   {e(lg.home)} vs {e(lg.away)} · {lg.kickoff.strftime('%H:%M')} · {e(lg.market)}")
        lines += [
            f"✅ Probabilidad de que salga completa: <b>{par['prob'] * 100:.0f}%</b> "
            f"(falla ≈ {round((1 - par['prob']) * 10)} de cada 10)",
            f"📱 Momio total mínimo: <b>{odds_txt(par['odds'])}</b>",
            f"💵 Con {cur}{stake:.0f} cobras ≥ {cur}{payout:.2f} (ganancia ≥ {cur}{payout - stake:.2f})",
        ]
    bank = cfg.get("BANKROLL") or 0
    if bank and stake > 0.10 * bank:
        lines += ["", f"⚠️ {cur}{stake:.0f} por parlay es el {stake / bank * 100:.0f}% de tu banca. "
                  f"Dos parlays perdidas seguidas te quitan {min(2 * stake / bank, 1) * 100:.0f}%."]
    lines += [
        "",
        "<b>🧠 Notas del analista</b>",
        "• Las cuotas de Pinnacle ya incluyen lesiones, rotaciones y noticias. Aun así, revisa la "
        "alineación confirmada 1 h antes: si falta un titular clave del favorito, quita esa selección.",
        "• «est.» = estimado con un modelo de goles calibrado con el mercado (ya con recorte prudente).",
        "• Córners y remates a puerta no se incluyen: no hay datos gratuitos confiables y no invento números.",
        "• Más selecciones = más margen de la casa. Preferimos 2–3 selecciones fuertes.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Datos de demostración
# ---------------------------------------------------------------------------
def demo_events() -> dict[str, list[dict]]:
    start = (datetime.now(timezone.utc) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)

    totals = {"Manchester City": (1.55, 2.50), "Arsenal": (1.95, 1.90), "Real Betis": (2.05, 1.80), "Inter": (1.70, 2.15), "Getafe": (2.40, 1.60)}

    def bk(key, title, h, d, a, home, away):
        over, under = totals[home]
        return {"key": key, "title": title, "markets": [
            {"key": "h2h", "outcomes": [
                {"name": home, "price": h}, {"name": away, "price": a}, {"name": "Draw", "price": d}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": over, "point": 2.5}, {"name": "Under", "price": under, "point": 2.5}]}]}

    def ev(i, sport, home, away, books):
        return {"id": f"demo{i}", "sport_key": sport, "home_team": home, "away_team": away,
                "commence_time": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                "bookmakers": [bk(*b, home, away) for b in books]}

    return {
        "soccer_epl": [ev(1, "soccer_epl", "Arsenal", "Chelsea", [
            ("pinnacle", "Pinnacle", 2.05, 3.55, 3.85), ("bet365", "Bet365", 2.00, 3.50, 3.75),
            ("williamhill", "William Hill", 2.00, 3.40, 4.20), ("unibet", "Unibet", 2.02, 3.45, 3.70),
            ("betfair_ex_eu", "Betfair", 2.04, 3.50, 3.80), ("marathonbet", "Marathon Bet", 2.06, 3.50, 3.78)]),
            ev(5, "soccer_epl", "Manchester City", "Wolverhampton Wanderers", [
                ("pinnacle", "Pinnacle", 1.30, 6.00, 10.5), ("bet365", "Bet365", 1.29, 5.75, 10.0),
                ("williamhill", "William Hill", 1.28, 6.00, 11.0), ("unibet", "Unibet", 1.30, 5.80, 9.50),
                ("betsson", "Betsson", 1.29, 6.10, 10.0)])],
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
    focus_event = None  # (kickoff, event, league)
    focus_limit = now + timedelta(days=cfg.get("FOCUS_DAYS", 7))
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
            event.setdefault("sport_key", sport_key)
            team = cfg.get("FOCUS_TEAM")
            if team and now <= ko <= focus_limit and (_same_team(event["home_team"], team)
                                                      or _same_team(event["away_team"], team)):
                if focus_event is None or ko < focus_event[0]:
                    focus_event = (ko, event, league)
            if not (now <= ko <= horizon):
                continue
            review = analyze_event(event, league, cfg, tz)
            reviews.append(review)

    if cfg.get("MODE") == "seguro":
        # Primero los que además tienen valor en el mercado; luego por probabilidad.
        def sort_key(p):
            return (bool(p.odds and p.odds > p.min_odds), p.fair_prob)
    else:
        def sort_key(p):
            return p.ev
    picks = sorted((p for r in reviews for p in r.picks), key=sort_key, reverse=True)[: cfg["MAX_PICKS"]]

    if not demo:
        record_picks(picks, cfg["HISTORY_FILE"])
    stats = history_stats(cfg["HISTORY_FILE"]) if not demo else {"n": 0}

    focus = None
    if cfg.get("FOCUS_TEAM"):
        fa = None
        if focus_event:
            ko, ev, league = focus_event
            fr = analyze_event(ev, league, {**cfg, "MIN_BOOKS": min(cfg["MIN_BOOKS"], 3)}, tz)
            props = None
            if not demo and cfg.get("FOCUS_PROPS") and ko <= now + timedelta(hours=30):
                try:
                    props = fetch_player_props(ev["sport_key"], ev["id"], cfg)
                except Exception as exc:
                    print(f"⚠️  No se pudieron descargar momios de goleador: {exc}")
            season = None if demo else fetch_player_season(cfg)
            fa = focus_analysis(fr, cfg, props, season)
            focus = focus_lines(fa, cfg, next_only=ko > horizon)
        else:
            focus = focus_lines(None, cfg)

    report = build_report(reviews, picks, cfg, stats, remaining, now.astimezone(tz), focus)

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
