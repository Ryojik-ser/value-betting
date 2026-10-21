# CLAUDE.md — Value Betting Analyzer

Guía rápida para trabajar en este repo sin releer todo el código. Responde al usuario en **español, corto y claro**: lo lee desde el celular.

## Qué es
Analizador de apuestas de fútbol que corre solo en **GitHub Actions** y envía un informe a **Telegram** (@ClaudepuestasEVbot). El usuario apuesta en **Draftea** con banca de **$50 MXN** y **$5 por apuesta/parlay**. Draftea no está en ninguna API: el sistema da el **momio mínimo** (americano) que debe pagar Draftea.

## Reglas del usuario
- **No lanzar informes manuales** (workflow_dispatch) salvo que lo pida explícitamente. Cada informe gasta créditos de The Odds-API (500/mes).
- Nunca poner claves en el código: el repo es **público**. Van como *secrets* de GitHub.
- Ser honesto: alta probabilidad ≠ certeza; no inventar datos (córners, remates, lesiones no tienen fuente gratuita).
- Rama de trabajo: `claude/sports-betting-value-analyzer-w5avcm` (es la rama por defecto). El bot hace commits del historial: haz `git pull --no-rebase` antes de push.

## Archivos
- `value_betting_analyzer.py` — todo el sistema (un solo archivo).
- `.github/workflows/daily.yml` — 07:40 CDMX, llega ~8:00 (`RUN_KIND=manana`, informe oficial, registra) y 13:40, llega ~14:00 (`tarde`, ventana 18 h, **no registra**).
- `data/picks_history.csv` (sencillas), `data/bets_history.csv` (parlays/no favoritos/City), `data/telegram_chat_id.txt` — los escribe el bot.
- `tests/test_analyzer.py` — `python -m pytest -q` (sin red, usa `demo_events()`).
- `setup.py`, `run_daily.bat` — instalación alternativa en Windows.

## Flujo de `run()`
1. `settle_pending` liquida picks y apuestas con marcadores (football-data si hay clave; si no, `/scores` de Odds-API, 2 créditos por liga).
2. Por liga: `has_events` (gratis) → si juega en la ventana, `fetch_odds` (`h2h,totals`, 2 créditos).
3. `analyze_event`: probabilidad justa = 70 % Pinnacle sin margen + 30 % mediana del resto (mín. 4 casas); `p_over25`; mejores cuotas.
4. Modo `seguro` (default): `safe_pick` (1/2/1X/X2 con ≥65 % y cuota ≥1.20). Modo `valor`: EV vs. casas europeas.
5. `fit_goals` → Poisson + **Dixon-Coles** (λ local, λ visitante, rho) calibrado al mercado, con caché. `market_legs` genera selecciones (goles, ambos anotan, equipo anota, crear apuesta) con recorte prudente (marcadas ≈).
6. `build_parlays` (una selección por partido; Segura / Dobla ≥+100 / Ticket ≥+145, menos selecciones primero), `underdog_picks` (🐺 no favoritos ≥55 % y ≥1.45, ⭐ si hay EV), `focus_analysis` (🩵 Man City + Haaland **solo el día que juega**; props de goleador si <30 h).
7. `record_picks` / `record_bets` (solo `manana`), `bets_stats`.
8. Render: secciones separadas por `SECTION_BREAK` (`\x1e`); `split_message` nunca corta una sección. HTML de Telegram (`<b> <i> <code> <blockquote expandable>`), sin anidar `<code>` dentro de otras etiquetas.

## Configuración
Valores por defecto en `DEFAULTS`; se sobreescriben con `config.json` o variables de entorno / *Variables* de GitHub (`BANKROLL=50`, `STAKE_AMOUNT`, `PARLAY_STAKE`, `MODE`, `SAFE_MIN_PROB`…). Secrets: `TELEGRAM_TOKEN`, `ODDS_API_KEY`, `FOOTBALL_DATA_KEY`.

## Calibración y banca
- `calibration()` (90 días, legs `src=modelo`, mín. `CALIB_MIN`=30) → `_calib.factor` ∈ [0.85, 1] multiplica probabilidades ≈.
- `bankroll_rules()` → `_rules` (racha, stop-loss 7 días); `plan_section()` arma el plan del día sin repetir partidos.
- `FOOTBALL_DATA_KEY` ya está configurada (resultados + goles de Haaland).

## Informes especiales (workflow_dispatch con inputs)
- `report=parlays` + `leagues=<clave>`: solo parlays de pago alto (+150…+400), no registra.
- `report=corners`: córners de Liga MX con API-Football (secreto `API_FOOTBALL_KEY`, ~14 consultas); `corners_report()`, binomial negativa (var 1.3·media), prior de liga. No usa créditos de Odds-API.

## Ideas pendientes
- Remates a puerta / alineaciones con API-Football si el plan gratis lo permite.

## Antes de subir
`python -m pyflakes *.py tests/*.py && python -m pytest -q`, luego commit en español y push a la rama.
