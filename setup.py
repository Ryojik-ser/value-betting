#!/usr/bin/env python3
"""
Asistente de configuración inicial del Value Betting Analyzer.

1. Pide tus credenciales (token de Telegram y API keys).
2. Obtiene automáticamente tu Chat ID de Telegram.
3. Verifica que las APIs respondan.
4. Envía un mensaje de prueba y guarda todo en config.json.
5. (Windows) Ofrece programar la ejecución diaria.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Ejecuta primero:  pip install -r requirements.txt")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"


def ask(prompt: str, current: str = "") -> str:
    hint = f" [{current[:6]}…]" if current else ""
    value = input(f"{prompt}{hint}: ").strip()
    return value or current


def get_chat_id(token: str) -> str:
    print("\n👉 Abre Telegram, busca tu bot, pulsa Iniciar y envíale cualquier mensaje (ej. 'hola').")
    input("   Cuando lo hayas hecho, pulsa Enter aquí… ")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
    if r.status_code == 401:
        sys.exit("❌ El token de Telegram no es válido. Revísalo en @BotFather.")
    updates = r.json().get("result", [])
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("first_name") or chat.get("title") or ""
            print(f"✔ Chat ID encontrado: {chat['id']} ({name})")
            return str(chat["id"])
    sys.exit("❌ No encontré mensajes. Envía un mensaje a tu bot y vuelve a ejecutar setup.py.")


def check_odds_api(key: str) -> None:
    r = requests.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": key}, timeout=30)
    if r.ok:
        print(f"✔ The Odds-API OK · créditos restantes este mes: {r.headers.get('x-requests-remaining', '?')}")
    else:
        sys.exit(f"❌ The Odds-API respondió {r.status_code}. Revisa tu API Key.")


def check_football_data(key: str) -> None:
    r = requests.get("https://api.football-data.org/v4/competitions/PL",
                     headers={"X-Auth-Token": key}, timeout=30)
    if r.ok:
        print("✔ football-data.org OK")
    else:
        print(f"⚠️  football-data.org respondió {r.status_code}. El sistema funcionará, "
              "pero liquidará resultados con The Odds-API (gasta más créditos).")


def schedule_windows(hour: str) -> None:
    bat = BASE_DIR / "run_daily.bat"
    cmd = ["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", "ValueBettingAnalyzer",
           "/TR", str(bat), "/ST", hour]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"✔ Tarea programada: cada día a las {hour} (Programador de tareas de Windows).")
    else:
        print(f"⚠️  No se pudo crear la tarea: {res.stderr.strip() or res.stdout.strip()}")
        print("   Puedes crearla a mano (ver README, sección 'Programar').")


def main() -> None:
    print("=" * 60)
    print("  VALUE BETTING ANALYZER · Configuración inicial")
    print("=" * 60)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}

    cfg["TELEGRAM_TOKEN"] = ask("Token del bot de Telegram", cfg.get("TELEGRAM_TOKEN", ""))
    cfg["ODDS_API_KEY"] = ask("API Key de The Odds-API", cfg.get("ODDS_API_KEY", ""))
    cfg["FOOTBALL_DATA_KEY"] = ask("API Key de football-data.org (opcional, Enter para omitir)",
                                   cfg.get("FOOTBALL_DATA_KEY", ""))
    if not cfg["TELEGRAM_TOKEN"] or not cfg["ODDS_API_KEY"]:
        sys.exit("❌ El token de Telegram y la API Key de The Odds-API son obligatorios.")

    bankroll = ask("Tu banca total para apuestas (ej. 1000)", str(cfg.get("BANKROLL", "1000")))
    try:
        cfg["BANKROLL"] = float(bankroll)
    except ValueError:
        cfg["BANKROLL"] = 1000.0

    cfg["TELEGRAM_CHAT_ID"] = cfg.get("TELEGRAM_CHAT_ID") or get_chat_id(cfg["TELEGRAM_TOKEN"])

    print("\nVerificando APIs…")
    check_odds_api(cfg["ODDS_API_KEY"])
    if cfg["FOOTBALL_DATA_KEY"]:
        check_football_data(cfg["FOOTBALL_DATA_KEY"])

    r = requests.post(f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage",
                      json={"chat_id": cfg["TELEGRAM_CHAT_ID"],
                            "text": "✅ Value Betting Analyzer conectado. Recibirás aquí tu informe diario."},
                      timeout=30)
    print("✔ Mensaje de prueba enviado a Telegram." if r.ok else f"⚠️  Telegram: {r.text[:200]}")

    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✔ Configuración guardada en {CONFIG_FILE.name}")

    if os.name == "nt":
        hour = input("\n¿A qué hora quieres el informe diario? (HH:MM, Enter = 08:00, 'no' = omitir): ").strip()
        if hour.lower() != "no":
            schedule_windows(hour or "08:00")

    print("\nListo. Prueba ahora:  python value_betting_analyzer.py")


if __name__ == "__main__":
    main()
