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
    "MARKETS": "h2h,totals",      # mercados a descargar (ganador + total de goles)
    "CURRENCY": "$",
    "REGIONS": "eu",              # eu incluye Pinnacle; puedes usar "eu,uk"
    "HOURS_AHEAD": 24,            # ventana de partidos a analizar
    "RUN_KIND": "manana",         # "manana" (oficial, registra picks) o "tarde" (actualización, no registra)
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
    sport_key: str = ""


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
    review.sport_key = event.get("sport_key", "")
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


def has_events(sport_key: str, cfg: dict, start: datetime, end: datetime) -> bool:
    """Consulta GRATIS (0 créditos): ¿hay partidos de esta liga en la ventana?"""
    try:
        r = _requests().get(
            f"{ODDS_API_URL}/sports/{sport_key}/events",
            params={"apiKey": cfg["ODDS_API_KEY"], "dateFormat": "iso",
                    "commenceTimeFrom": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "commenceTimeTo": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
            timeout=30,
        )
        if r.status_code == 404:
            return False
        if not r.ok:
            return True  # ante la duda, consulta los momios
        return len(r.json()) > 0
    except Exception:
        return True


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
    """Telegram admite 4096 caracteres por mensaje. Junta secciones completas en cada
    mensaje; solo si una sección sola no cabe, la corta por líneas."""
    pieces = []
    for sec in text.split("\x1e"):
        if len(sec) <= limit:
            pieces.append(sec)
            continue
        current = ""
        for line in sec.split("\n"):
            if len(current) + len(line) + 1 > limit and current:
                pieces.append(current.rstrip("\n"))
                current = ""
            current += line + "\n"
        if current.strip():
            pieces.append(current.rstrip("\n"))
    chunks, current = [], ""
    for pc in pieces:
        if current and len(current) + len(pc) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{pc}" if current else pc
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


def find_result(item: dict, results: list[dict]) -> dict | None:
    """Busca el marcador final de un partido (por id o por equipos y fecha)."""
    kickoff_date = item["kickoff"][:10]
    for res in results:
        if res.get("id") and res["id"] == item["event_id"]:
            match = res
            break
        if abs((datetime.fromisoformat(res["date"]) - datetime.fromisoformat(kickoff_date)).days) <= 1 \
                and _same_team(res["home"], item["home"]) and _same_team(res["away"], item["away"]):
            match = res
            break
    else:
        return None
    if match["home_goals"] is None or match["away_goals"] is None:
        return None
    return match


def leg_wins(code: str, hg: int, ag: int) -> bool | None:
    """¿Gana la selección con este marcador? None si no se puede saber con el marcador."""
    side, _, rest = code.partition("_")
    if side in ("home", "away"):
        tg, og = (hg, ag) if side == "home" else (ag, hg)
        return {"win": tg > og, "dc": tg >= og, "scores": tg >= 1, "win_over_1.5": tg > og and hg + ag >= 2,
                "plus_1.5": tg - og >= -1, "cs": og == 0, "team_over_1.5": tg >= 2}.get(rest)
    total = hg + ag
    return {"over_1.5": total >= 2, "under_3.5": total <= 3, "over_2.5": total >= 3, "under_2.5": total <= 2,
            "btts_yes": hg >= 1 and ag >= 1, "btts_no": hg == 0 or ag == 0}.get(code)


def settle_rows(rows: list[dict], results: list[dict]) -> int:
    settled = 0
    for r in rows:
        if r["status"] != "pendiente":
            continue
        match = find_result(r, results)
        if not match:
            continue
        hg, ag = match["home_goals"], match["away_goals"]
        winner = "home" if hg > ag else "away" if ag > hg else "draw"
        stake = float(r["stake"] or 0) or 1.0
        won = winner in WINNING_RESULTS.get(r["outcome"], {r["outcome"]})
        r["status"] = "ganada" if won else "perdida"
        r["profit"] = f"{(stake * (float(r['odds']) - 1)) if won else -stake:.2f}"
        settled += 1
    return settled


# Historial de parlays, no favoritos y City (una fila por apuesta, selecciones en JSON)
BETS_FIELDS = ["date", "kind", "name", "bet_id", "legs", "prob", "odds", "stake", "status", "profit"]
BET_KINDS = {"sencilla": "Sencillas", "parlay": "Parlays", "nofav": "No favoritos", "city": "City"}


def bets_file(cfg: dict) -> str:
    return str(Path(cfg["HISTORY_FILE"]).parent / "bets_history.csv")


def read_bets(path: str) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_bets(path: str, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BETS_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def record_bets(bets: list[dict], path: str) -> int:
    """Guarda apuestas sugeridas (sin repetir) para medir después qué sección funciona."""
    rows = read_bets(path)
    seen = {r["bet_id"] for r in rows}
    added = 0
    for b in bets:
        legs = [lg for lg in b["legs"] if lg.code and lg.code != "player"]
        if len(legs) != len(b["legs"]) or not legs:
            continue  # sin forma de liquidar con el marcador
        bet_id = b["kind"] + ":" + "|".join(sorted(f"{lg.event_id}/{lg.code}" for lg in legs))
        if bet_id in seen:
            continue
        rows.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "kind": b["kind"], "name": b.get("name", ""),
            "bet_id": bet_id, "prob": f"{b['prob']:.4f}", "odds": f"{b['odds']:.3f}", "stake": f"{b['stake']:.2f}",
            "status": "pendiente", "profit": "",
            "legs": json.dumps([{"event_id": lg.event_id, "sport_key": lg.sport_key, "home": lg.home,
                                 "away": lg.away, "kickoff": lg.kickoff.isoformat(), "code": lg.code,
                                 "sel": lg.selection, "result": ""} for lg in legs], ensure_ascii=False),
        })
        seen.add(bet_id)
        added += 1
    write_bets(path, rows)
    return added


def settle_bets(rows: list[dict], results: list[dict]) -> int:
    settled = 0
    for r in rows:
        if r["status"] != "pendiente":
            continue
        legs = json.loads(r["legs"])
        for lg in legs:
            if lg["result"]:
                continue
            match = find_result(lg, results)
            if match:
                won = leg_wins(lg["code"], match["home_goals"], match["away_goals"])
                if won is not None:
                    lg["result"] = "G" if won else "P"
        r["legs"] = json.dumps(legs, ensure_ascii=False)
        stake = float(r["stake"] or 0) or 1.0
        if any(lg["result"] == "P" for lg in legs):
            r["status"], r["profit"] = "perdida", f"{-stake:.2f}"
            settled += 1
        elif all(lg["result"] == "G" for lg in legs):
            r["status"], r["profit"] = "ganada", f"{stake * (float(r['odds']) - 1):.2f}"
            settled += 1
    return settled


def bets_stats(path: str) -> dict:
    out = {}
    for r in read_bets(path):
        if r["status"] not in ("ganada", "perdida"):
            continue
        k = out.setdefault(r["kind"], {"n": 0, "wins": 0, "exp": 0.0, "staked": 0.0, "profit": 0.0})
        k["n"] += 1
        k["wins"] += r["status"] == "ganada"
        k["exp"] += float(r["prob"])
        k["staked"] += float(r["stake"] or 0)
        k["profit"] += float(r["profit"] or 0)
    return out


def settle_pending(cfg: dict) -> int:
    rows = read_history(cfg["HISTORY_FILE"])
    bet_rows = read_bets(bets_file(cfg))
    now = datetime.now(timezone.utc)

    def finished(kickoff: str) -> bool:
        return datetime.fromisoformat(kickoff) + timedelta(hours=2, minutes=30) < now

    pending = [r for r in rows if r["status"] == "pendiente" and finished(r["kickoff"])]
    pending += [lg for r in bet_rows if r["status"] == "pendiente" for lg in json.loads(r["legs"])
                if not lg["result"] and finished(lg["kickoff"])]
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
    nb = settle_bets(bet_rows, results)
    if bet_rows:
        write_bets(bets_file(cfg), bet_rows)
    return n + nb


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
            ("1X2", f"Gana {dog}", p_win, bp.get(dog_side, (None,))[0], f"{dog_side}_win"),
            ("Doble oportunidad", f"{dog} o empate", p_win + pd, dc_price, f"{dog_side}_dc"),
            ("Hándicap", f"{dog} +1.5 (no pierde por 2 o más)",
             P(lambda i, j: dog_goals(i, j) - fav_goals(i, j) >= -1) - cut, None, f"{dog_side}_plus_1.5"),
            (f"Total goles {dog}", f"{dog} anota (más de 0.5)", P(lambda i, j: dog_goals(i, j) >= 1) - cut, None,
             f"{dog_side}_scores"),
        ]
        best = None
        for market, sel, prob, price, code in options:
            if not (0 < prob < 1) or prob < cfg["DOG_MIN_PROB"] or 1 / prob < cfg["DOG_MIN_ODDS"]:
                continue
            ev = price * prob - 1 if price else None
            leg = Leg(r.event_id, r.league, r.kickoff, r.home, r.away, market, sel, prob,
                      "mercado" if market in ("1X2", "Doble oportunidad") else "modelo", code, r.sport_key)
            cand = {"leg": leg, "market_odds": price, "ev": ev}
            key = (ev is not None and ev >= 0.02, ev or 0, 1 / prob)
            if best is None or key > best[0]:
                best = (key, cand)
        if best:
            out.append(best[1])
    out.sort(key=lambda c: (c["ev"] is not None and c["ev"] >= 0.02, c["ev"] or 0, c["leg"].min_odds), reverse=True)
    return out[: cfg["DOG_MAX"]]


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
    code: str = ""     # condición para liquidar con el marcador (ver leg_wins)
    sport_key: str = ""

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


def score_matrix(lh: float, la: float, rho: float = 0.0) -> list[list[float]]:
    """Probabilidad de cada marcador. rho es el ajuste Dixon-Coles: corrige que el Poisson
    simple subestima los marcadores bajos (0-0, 1-1) y sobreestima 1-0 / 0-1."""
    ph, pa = _pmf(lh), _pmf(la)
    m = [[x * y for y in pa] for x in ph]
    if rho:
        m[0][0] *= max(0.0, 1 - lh * la * rho)
        m[0][1] *= max(0.0, 1 + lh * rho)
        m[1][0] *= max(0.0, 1 + la * rho)
        m[1][1] *= max(0.0, 1 - rho)
        total = sum(map(sum, m))
        m = [[v / total for v in row] for row in m]
    return m


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


_FIT_CACHE: dict = {}
DC_RHO_DEFAULT = -0.08   # valor típico en fútbol europeo


def fit_goals(fair: list[float], p_over25: float | None) -> tuple[float, float, float]:
    """Goles esperados (local, visitante) y rho de Dixon-Coles que mejor reproducen
    las cuotas del mercado (1X2 y, si hay, más/menos de 2.5)."""
    key = (tuple(round(x, 5) for x in fair), None if p_over25 is None else round(p_over25, 5))
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]

    def err(lh, la, rho):
        h, d, a, o = _summary(score_matrix(lh, la, rho))
        e = (h - fair[0]) ** 2 + (d - fair[1]) ** 2 + (a - fair[2]) ** 2
        e += (o - p_over25) ** 2 if p_over25 is not None else 0.0
        return e + 0.02 * (rho - DC_RHO_DEFAULT) ** 2  # rho cerca de lo típico salvo que el mercado diga otra cosa

    grid = [x / 10 for x in range(2, 40)]
    lh, la = min(((x, y) for x in grid for y in grid), key=lambda t: err(*t, DC_RHO_DEFAULT))
    fine = [d / 50 for d in range(-5, 6)]
    rhos = [r / 100 for r in range(-20, 6, 3)]
    best = min(((max(0.05, lh + dx), max(0.05, la + dy), r) for dx in fine for dy in fine for r in rhos),
               key=lambda t: err(*t))
    _FIT_CACHE[key] = best
    return best


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
        ("1X2", f"Gana {h}", ph, "mercado", "home_win"),
        ("1X2", f"Gana {a}", pa, "mercado", "away_win"),
        ("Doble oportunidad", f"{h} o empate", ph + pd, "mercado", "home_dc"),
        ("Doble oportunidad", f"{a} o empate", pa + pd, "mercado", "away_dc"),
        ("Total de goles", "Más de 1.5 goles", P(lambda i, j: i + j >= 2) - cut, "modelo", "over_1.5"),
        ("Total de goles", "Menos de 3.5 goles", P(lambda i, j: i + j <= 3) - cut, "modelo", "under_3.5"),
        ("Ambos equipos anotan", "Sí", P(lambda i, j: i >= 1 and j >= 1) - cut, "modelo", "btts_yes"),
        ("Ambos equipos anotan", "No", P(lambda i, j: i == 0 or j == 0) - cut, "modelo", "btts_no"),
        (f"Total goles {h}", f"{h} anota (más de 0.5)", P(lambda i, j: i >= 1) - cut, "modelo", "home_scores"),
        (f"Total goles {a}", f"{a} anota (más de 0.5)", P(lambda i, j: j >= 1) - cut, "modelo", "away_scores"),
        ("Crear apuesta", f"Gana {h} + más de 1.5 goles", P(lambda i, j: i > j and i + j >= 2) - cut, "modelo",
         "home_win_over_1.5"),
        ("Crear apuesta", f"Gana {a} + más de 1.5 goles", P(lambda i, j: j > i and i + j >= 2) - cut, "modelo",
         "away_win_over_1.5"),
    ]
    if po is not None:
        raw += [("Total de goles", "Más de 2.5 goles", po, "mercado", "over_2.5"),
                ("Total de goles", "Menos de 2.5 goles", 1 - po, "mercado", "under_2.5")]
    return [Leg(review.event_id, review.league, review.kickoff, h, a, mk, sel, p, src, code, review.sport_key)
            for mk, sel, p, src, code in raw if 0 < p < 1]


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
    out = [make("🛡️ Segura", [first, second])]
    for name, target in (("💵 Dobla tu dinero", 2.0), ("🚀 Estilo ticket", 2.45)):
        cands = [c for c in combos if 1 / prod(lg.prob for lg in c) >= target
                 and prod(lg.prob for lg in c) >= cfg["PARLAY_MIN_PROB"]]
        if not cands:
            continue
        best = min(cands, key=lambda c: (len(c), 1 / prod(lg.prob for lg in c)))
        if all({id(x) for x in best} != {id(x) for x in o["legs"]} for o in out):
            out.append(make(name, best))
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
    lh, la, rho = fit_goals(review.fair, review.p_over25)
    m = score_matrix(lh, la, rho)
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
    team = short_name(team)
    p_any = P(lambda i, j: scores(tg(i, j))) - cut
    market = None
    if props:
        best_price, best_book = max(props)
        mkt_prob = statistics.median(1 / p for p, _ in props) * 0.93  # quita el margen típico del mercado
        p_any = 0.5 * p_any + 0.5 * mkt_prob
        market = {"best": best_price, "book": best_book, "n": len(props), "ev": best_price * p_any - 1}
    sd = "home" if home_side else "away"
    picks = [
        ("1X2", f"Gana {team}", p_win, "mercado", "equipo", f"{sd}_win"),
        ("Doble oportunidad", f"{team} o empate", p_win + pd, "mercado", "equipo", f"{sd}_dc"),
        (f"Total goles {team}", f"{team} más de 1.5 goles", P(lambda i, j: tg(i, j) >= 2) - cut, "modelo", "equipo",
         f"{sd}_team_over_1.5"),
        ("Crear apuesta", f"Gana {team} + más de 1.5 goles",
         P(lambda i, j: tg(i, j) > og(i, j) and i + j >= 2) - cut, "modelo", "equipo", f"{sd}_win_over_1.5"),
        ("Portería a cero", f"{team} no recibe gol", P(lambda i, j: og(i, j) == 0) - cut, "modelo", "equipo",
         f"{sd}_cs"),
        ("Goleador", f"{player} anota", p_any, "mercado" if props else "modelo", "jugador", "player"),
        ("Goleador", f"{player} 2 o más goles", P(lambda i, j: brace(tg(i, j))) - cut, "modelo", "jugador", "player"),
        ("Crear apuesta", f"Gana {team} + {player} anota",
         P(lambda i, j: scores(tg(i, j)) if tg(i, j) > og(i, j) else 0.0) - cut * 1.5, "modelo", "jugador", "player"),
    ]
    return {"review": review, "rival": rival, "home": home_side, "xg": (lh, la) if home_side else (la, lh),
            "share": share, "season": season, "market": market,
            "picks": [pk for pk in picks if 0 < pk[2] < 1]}


# ---------------------------------------------------------------------------
# Presentación del informe (HTML de Telegram)
# ---------------------------------------------------------------------------
SECTION_BREAK = "\x1e"      # separa secciones: el envío nunca corta una sección a la mitad
DIV = "━━━━━━━━━━━━━━━"
DAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
SHORT_NAMES = {
    "Manchester City": "Man City", "Manchester United": "Man United", "Tottenham Hotspur": "Tottenham",
    "Brighton and Hove Albion": "Brighton", "Wolverhampton Wanderers": "Wolves", "West Ham United": "West Ham",
    "Newcastle United": "Newcastle", "Nottingham Forest": "Nottm Forest", "Crystal Palace": "Palace",
    "Borussia Monchengladbach": "Gladbach", "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen",
    "Bayern Munich": "Bayern", "Eintracht Frankfurt": "Frankfurt", "Real Racing Club de Santander": "Racing",
    "Atlético Madrid": "Atlético", "Athletic Bilbao": "Athletic", "Paris Saint Germain": "PSG",
    "Inter Milan": "Inter",
}
_PREFIX = re.compile(r"^(?:1\. FC|FC|CF|CA|AC|AS|SC|SSC|TSG|RC|RCD|CD|UD|SD|VfB|VfL|SV)\s+")
_SUFFIX = re.compile(r"\s+(?:FC|CF|BC|AFC|SC)$")


def short_name(name: str) -> str:
    if name in SHORT_NAMES:
        return SHORT_NAMES[name]
    return _SUFFIX.sub("", _PREFIX.sub("", name)).strip() or name


def shortener(names):
    """Devuelve una función que cambia nombres largos de equipos por cortos en un texto."""
    pairs = sorted({(n, short_name(n)) for n in names if short_name(n) != n}, key=lambda t: -len(t[0]))

    def apply(text: str) -> str:
        for full, short in pairs:
            text = text.replace(full, short)
        return text
    return apply


def c_odds(decimal_odds: float) -> str:
    return f"<code>{american(decimal_odds)}</code>"


def p_txt(prob: float, estimated: bool) -> str:
    return f"{'≈' if estimated else ''}{prob * 100:.0f}%"


def money(x: float, cfg: dict) -> str:
    cur = html.escape(cfg.get("CURRENCY", "$"))
    return f"{cur}{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{cur}{x:.2f}"


def date_es(dt: datetime) -> str:
    return f"{DAYS_ES[dt.weekday()]} {dt.day} {MONTHS_ES[dt.month - 1]}"


def match_line(home: str, away: str, kickoff: datetime, league: str | None = None) -> str:
    e = html.escape
    extra = f" · {e(league)}" if league else ""
    return f"<i>{e(short_name(home))} vs {e(short_name(away))} · {kickoff.strftime('%H:%M')}{extra}</i>"


def focus_section(fa: dict | None, cfg: dict) -> str | None:
    if not fa:
        return None
    e = html.escape
    r = fa["review"]
    team, player = cfg["FOCUS_TEAM"], cfg["FOCUS_PLAYER"]
    surname = player.split()[-1]
    stake = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    ph, pd, pa = r.fair
    p_team, p_riv = (ph, pa) if fa["home"] else (pa, ph)
    t_s, riv_s = short_name(team), short_name(fa["rival"])
    lines = [DIV, f"🩵 <b>{e(team.upper())}</b> · hoy {r.kickoff.strftime('%H:%M')}",
             f"<i>vs {e(riv_s)} · {e(r.league)} · {'local' if fa['home'] else 'visitante'}</i>", "",
             f"📊 {e(t_s)} <b>{p_team * 100:.0f}%</b> · Empate {pd * 100:.0f}% · {e(riv_s)} {p_riv * 100:.0f}%",
             f"⚽ Goles esperados <b>{fa['xg'][0]:.1f} – {fa['xg'][1]:.1f}</b>"]
    sz = fa["season"]
    if sz:
        lines.append(f"👤 {e(surname)}: {sz['goals']} goles en {sz['played']} partidos · "
                     f"{fa['share'] * 100:.0f}% de los goles del equipo")
    else:
        lines.append(f"👤 {e(surname)}: ≈{fa['share'] * 100:.0f}% de los goles del equipo <i>(referencia)</i>")

    def row(sel, p, src):
        icon = "✅" if p >= 0.60 else "⚠️" if p >= 0.45 else "🎲"
        return (f"{icon} {e(sel)} {c_odds(1 / p)} · {p_txt(p, src != 'mercado')} · "
                f"+{money(stake * (1 / p - 1), cfg)}")

    for group, title in (("equipo", "Equipo"), ("jugador", surname)):
        items = sorted((pk for pk in fa["picks"] if pk[4] == group), key=lambda pk: pk[2], reverse=True)
        if items:
            lines += ["", f"<b>{e(title)}</b>"] + [row(pk[1], pk[2], pk[3]) for pk in items]
    mkt = fa["market"]
    if mkt:
        flag = f"⭐ valor {pct(mkt['ev'])}" if mkt["ev"] >= 0.02 else "sin valor extra"
        lines.append(f"🎯 Casas: mejor {c_odds(mkt['best'])} en {e(mkt['book'])} · {flag}")
    lines.append(f"<i>✅ ≥60% · ⚠️ 45–60% · 🎲 &lt;45% · Confirma que {e(surname)} sea titular.</i>")
    return "\n".join(lines)


def singles_section(picks: list[Pick], cfg: dict) -> str:
    e = html.escape
    stake = cfg.get("STAKE_AMOUNT") or 0
    head = "✅ <b>SENCILLAS</b>" + (f" · {money(stake, cfg)} c/u" if stake else "")
    lines = [DIV, head]
    if not picks:
        lines.append("<i>Hoy ninguna cumple los filtros de seguridad.</i>")
        return "\n".join(lines)
    misses = sum(1 - p.fair_prob for p in picks)
    lines.append(f"<i>De {len(picks)}, lo normal es fallar ~{max(1, round(misses))}.</i>")
    for i, p in enumerate(picks, 1):
        label = f"Gana {short_name(p.selection)}" if p.outcome in ("home", "away") else short_name(p.home) + " o empate" \
            if p.outcome == "home_draw" else short_name(p.away) + " o empate" if p.outcome == "away_draw" else p.selection
        info = f"{p.fair_prob * 100:.0f}% · ganas {money(p.stake * (p.min_odds - 1), cfg)}"
        if p.odds and p.odds > p.min_odds:
            info += f" · ⭐ Europa paga {american(p.odds)}"
        lines += ["", f"<b>{i}. {e(label)}</b> {c_odds(p.min_odds)}",
                  match_line(p.home, p.away, p.kickoff, p.league), info]
    return "\n".join(lines)


def parlays_section(reviews: list[MatchReview], cfg: dict) -> str | None:
    parlays = build_parlays(reviews, cfg)
    if not parlays:
        return None
    e = html.escape
    S = shortener([n for r in reviews for n in (r.home, r.away)])
    stake = cfg["PARLAY_STAKE"]
    lines = [DIV, f"🎰 <b>PARLAYS</b> · {money(stake, cfg)} c/u"]
    for par in parlays:
        lines += ["", f"<b>{par['name']}</b> · {par['prob'] * 100:.0f}% · {c_odds(par['odds'])}",
                  f"{money(stake, cfg)} → cobras <b>{money(stake * par['odds'], cfg)}</b>"]
        legs = par["legs"]
        for k, lg in enumerate(legs):
            last = k == len(legs) - 1
            lines.append(f"{'└' if last else '├'} {e(S(lg.selection))} {c_odds(lg.min_odds)} · "
                         f"{p_txt(lg.prob, lg.source != 'mercado')}")
            lines.append(f"{'   ' if last else '│  '}{match_line(lg.home, lg.away, lg.kickoff)}")
    bank = cfg.get("BANKROLL") or 0
    if bank and stake > 0.10 * bank:
        lines += ["", f"<i>⚠️ {money(stake, cfg)} por parlay es el {stake / bank * 100:.0f}% de tu banca.</i>"]
    return "\n".join(lines)


def underdogs_section(reviews: list[MatchReview], cfg: dict) -> str | None:
    picks = underdog_picks(reviews, cfg)
    if not picks:
        return None
    e = html.escape
    S = shortener([n for r in reviews for n in (r.home, r.away)])
    stake = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    lines = [DIV, "🐺 <b>NO FAVORITOS</b> · pagan más",
             "<i>El público apuesta al favorito; estos tienen buena probabilidad y mejor pago.</i>"]
    for c in picks:
        lg = c["leg"]
        info = f"{p_txt(lg.prob, lg.source != 'mercado')} · ganas {money(stake * (lg.min_odds - 1), cfg)}"
        if c["ev"] is not None and c["ev"] >= 0.02:
            info += f" · ⭐ valor {pct(c['ev'])}"
        lines += ["", f"<b>{e(S(lg.selection))}</b> {c_odds(lg.min_odds)}",
                  match_line(lg.home, lg.away, lg.kickoff, lg.league), info]
    lines += ["", "<i>Úsalos solos o como 1 selección de una doble, no varios juntos.</i>"]
    return "\n".join(lines)


def history_section(stats: dict, cfg: dict, bstats: dict | None = None) -> str:
    """Resultados reales por tipo de apuesta (a momio mínimo: estimación conservadora)."""
    kinds = dict(bstats or {})
    if stats.get("n"):
        kinds = {"sencilla": {"n": stats["n"], "wins": stats["wins"], "exp": stats["expected_wins"],
                              "staked": stats["staked"], "profit": stats["profit"]}, **kinds}
    lines = [DIV, "📒 <b>HISTORIAL</b>"]
    if not kinds:
        lines.append("<i>Aún no hay apuestas liquidadas.</i>")
        return "\n".join(lines)
    tot_s = tot_p = 0.0
    for key in BET_KINDS:
        k = kinds.get(key)
        if not k:
            continue
        roi = k["profit"] / k["staked"] if k["staked"] else 0.0
        tot_s, tot_p = tot_s + k["staked"], tot_p + k["profit"]
        lines.append(f"{'🟢' if roi >= 0 else '🔴'} <b>{BET_KINDS[key]}</b> · {k['wins']}/{k['n']} "
                     f"({k['wins'] / k['n'] * 100:.0f}%, esp. {k['exp'] / k['n'] * 100:.0f}%) · ROI {pct(roi)}")
    lines.append(f"Total: {'+' if tot_p >= 0 else '−'}{money(abs(tot_p), cfg)} sobre {money(tot_s, cfg)} apostados")
    lines.append("<i>Calculado a momio mínimo; si cobraste más en BetMéxico, ganaste más.</i>")
    return "\n".join(lines)


def notes_section(reviews: list[MatchReview], cfg: dict, remaining: str | None) -> str:
    e = html.escape
    rest = sorted((r for r in reviews if not r.picks), key=lambda r: r.kickoff)
    items = [
        "🧠 <b>Notas del analista</b>",
        f"• <b>Momio mínimo</b> = probabilidad real sin margen. Si {e(cfg['MAIN_BOOK'])} paga menos, salta el pick.",
        "• <b>≈</b> = estimado con un modelo de goles calibrado con el mercado (con recorte prudente).",
        "• Las cuotas ya reflejan lesiones y noticias; aun así revisa alineaciones 1 h antes.",
        "• Córners y remates a puerta no se incluyen: no hay datos gratuitos confiables.",
    ]
    if rest:
        items.append("• Sin pick sencillo: " + ", ".join(
            f"{r.kickoff.strftime('%H:%M')} {e(short_name(r.home))}–{e(short_name(r.away))}" for r in rest))
    if remaining is not None:
        items.append(f"• Créditos de cuotas restantes: {e(str(remaining))}")
    items.append("+18 · Apuesta solo lo que puedas perder.")
    return DIV + "\n<blockquote expandable>" + "\n".join(items) + "</blockquote>"


def build_report(reviews: list[MatchReview], picks: list[Pick], cfg: dict, stats: dict,
                 remaining: str | None, today: datetime, focus: str | None = None) -> str:
    e = html.escape
    if cfg.get("MODE") == "seguro":
        return build_safe_report(reviews, picks, cfg, stats, remaining, today, focus)
    lines = [f"📊 <b>VALUE BETTING</b> · {date_es(today)}",
             f"Partidos revisados: <b>{len(reviews)}</b> · Picks con valor: <b>{len(picks)}</b>"]
    body = [DIV, f"✅ <b>PICKS DE VALOR</b> · EV ≥ {cfg['MIN_EV'] * 100:.1f}%"]
    if picks:
        for i, p in enumerate(picks, 1):
            body += ["", f"<b>{i}. {e(p.selection)}</b> @ {odds_txt(p.odds)} ({e(p.bookmaker)})",
                     match_line(p.home, p.away, p.kickoff, p.league),
                     f"EV {pct(p.ev)} · prob. {p.fair_prob * 100:.1f}% · stake {money(p.stake, cfg)}",
                     f"En {e(cfg['MAIN_BOOK'])}: solo si paga ≥ {c_odds(p.min_odds)}"]
    else:
        body.append("<i>Hoy no hay picks con valor suficiente.</i>")
    sections = ["\n".join(lines)] + ([focus] if focus else []) + ["\n".join(body),
                history_section(stats, cfg, cfg.get("_bstats")), notes_section(reviews, cfg, remaining)]
    return SECTION_BREAK.join(sections)


def build_safe_report(reviews: list[MatchReview], picks: list[Pick], cfg: dict, stats: dict,
                      remaining: str | None, today: datetime, focus: str | None = None) -> str:
    e = html.escape
    n_par = len(build_parlays(reviews, cfg))
    n_dog = len(underdog_picks(reviews, cfg))
    stake = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    header = "\n".join([
        "🌆 <b>ACTUALIZACIÓN DE LA TARDE</b>" if cfg.get("RUN_KIND") == "tarde" else "📊 <b>APUESTAS DEL DÍA</b>",
        f"<i>{date_es(today)} · {e(cfg['MAIN_BOOK'])} · {money(stake, cfg)} por apuesta</i>",
        "",
        f"🔎 {len(reviews)} partidos revisados",
        f"✅ <b>{len(picks)}</b> sencillas · 🎰 <b>{n_par}</b> parlays · 🐺 <b>{n_dog}</b> no favoritos",
        "",
        f"📱 Apuesta solo si {e(cfg['MAIN_BOOK'])} paga <b>igual o más</b> que el momio indicado.",
        "Ej.: <code>-300</code> paga más que <code>-400</code>.",
    ])
    sections = [header, focus, singles_section(picks, cfg), parlays_section(reviews, cfg),
                underdogs_section(reviews, cfg), history_section(stats, cfg, cfg.get("_bstats")),
                notes_section(reviews, cfg, remaining)]
    return SECTION_BREAK.join(sc for sc in sections if sc)


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
    afternoon = cfg.get("RUN_KIND") == "tarde"
    if afternoon:  # 2 p.m. → 8 a.m.: partidos de la tarde-noche con momios más frescos
        cfg = {**cfg, "HOURS_AHEAD": min(cfg["HOURS_AHEAD"], 18)}
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
    remaining = None
    data = demo_events() if demo else None
    for sport_key, league in cfg["LEAGUES"].items():
        if demo:
            events = data.get(sport_key, [])
        else:
            if not has_events(sport_key, cfg, now, horizon):
                print(f"·  {league}: sin partidos en la ventana (0 créditos)")
                continue
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
            if team and now <= ko <= horizon and (_same_team(event["home_team"], team)
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

    record = not demo and not afternoon  # la tarde no registra: el historial mide el informe oficial
    if record:
        record_picks(picks, cfg["HISTORY_FILE"])
    stats = history_stats(cfg["HISTORY_FILE"]) if not demo else {"n": 0}
    unit = cfg.get("STAKE_AMOUNT") or cfg["PARLAY_STAKE"]
    bets = [{"kind": "parlay", "name": par["name"], "legs": par["legs"], "prob": par["prob"],
             "odds": par["odds"], "stake": cfg["PARLAY_STAKE"]} for par in build_parlays(reviews, cfg)]
    bets += [{"kind": "nofav", "legs": [c["leg"]], "prob": c["leg"].prob, "odds": c["leg"].min_odds,
              "stake": unit} for c in underdog_picks(reviews, cfg)]

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
            focus = focus_section(fa, cfg)  # solo el día en que juega el equipo
            if fa:  # registra los picks ✅ del equipo para medir el apartado
                for mk, sel, p, src, group, code in fa["picks"]:
                    if group == "equipo" and p >= 0.60:
                        lg = Leg(fr.event_id, fr.league, fr.kickoff, fr.home, fr.away, mk, sel, p, src, code,
                                 fr.sport_key)
                        bets.append({"kind": "city", "legs": [lg], "prob": p, "odds": 1 / p, "stake": unit})

    if record:
        record_bets(bets, bets_file(cfg))
    cfg = {**cfg, "_bstats": {} if demo else bets_stats(bets_file(cfg))}

    report = build_report(reviews, picks, cfg, stats, remaining, now.astimezone(tz), focus)

    if telegram:
        send_telegram(report, cfg)
        print("✔ Informe enviado a Telegram.")
    return report


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text.replace(SECTION_BREAK, "\n\n")))


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
