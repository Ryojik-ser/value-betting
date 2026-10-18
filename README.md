# Value Betting Analyzer

Sistema automático que cada mañana revisa los partidos 1X2 de **Premier League, La Liga, Serie A, Bundesliga, Champions y Europa League**, detecta las apuestas con **valor esperado (EV) positivo** y te envía el informe a **Telegram**, ordenado de mayor a menor valor.

## Antes de empezar: qué puede y qué no puede hacer

- **Apostar con valor no significa ganar más veces.** Significa cobrar cuotas más altas de lo que "debería" pagar el resultado. Muchos picks de valor son empates o visitantes a cuota 3–4: aciertas el 25–35 %, pero a largo plazo ganas dinero. Tu % de aciertos puede **bajar** mientras tu beneficio sube.
- La métrica que importa es el **ROI** sobre cientos de apuestas, no la racha de una semana. El sistema lleva ese registro por ti.
- Las casas limitan a quien gana a menudo. Y ningún sistema elimina el riesgo: **apuesta solo dinero que puedas perder.** +18.

## Cómo calcula el valor

1. Descarga las cuotas 1X2 de 20+ casas (The Odds-API).
2. Quita el margen de cada casa para obtener probabilidades "limpias".
3. **Probabilidad justa** = 70 % Pinnacle (la casa más eficiente del mundo) + 30 % mediana del resto. Si Pinnacle no está, usa solo la mediana (mínimo 4 casas).
4. Busca la **mejor cuota** disponible para cada resultado.
5. `EV = probabilidad justa × cuota − 1`. Si supera el umbral (2 % por defecto) y la cuota está entre 1.40 y 6.00, es pick.
6. **Stake sugerido**: Kelly al 25 %, con tope del 3 % de tu banca.
7. Guarda cada pick en `picks_history.csv`, lo liquida cuando hay resultado (football-data.org) y te reporta aciertos reales vs. esperados, ROI y beneficio.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `value_betting_analyzer.py` | Motor principal: cuotas, cálculo de valor, historial e informe a Telegram. |
| `setup.py` | Asistente: pide tus claves, obtiene tu Chat ID, verifica todo y programa la tarea diaria en Windows. |
| `run_daily.bat` | Lo que ejecuta el Programador de tareas de Windows. |
| `requirements.txt` | Librerías: `requests` y `tzdata` (zonas horarias en Windows). |
| `.github/workflows/daily.yml` | Opción en la nube: corre solo cada día sin tener tu PC encendido. |
| `tests/` | Pruebas automáticas del cálculo. |

## 1. Requisitos (≈10 minutos)

1. **Python 3.10+** — al instalarlo en Windows marca **«Add Python to PATH»**.
2. **Bot de Telegram** — en Telegram habla con **@BotFather**, envía `/newbot`, ponle nombre y guarda el **token**. Luego abre tu bot y pulsa **Iniciar**.
3. **The Odds-API** (obligatoria) — [the-odds-api.com](https://the-odds-api.com), plan *Free* (500 créditos/mes). El sistema gasta ~6 al día (1 por liga).
4. **football-data.org** (recomendada) — [football-data.org/client/register](https://www.football-data.org/client/register). Se usa gratis para liquidar resultados.

## 2. Instalación en Windows

```bat
mkdir C:\ValueBetting
cd C:\ValueBetting
:: copia aquí los archivos del proyecto
pip install -r requirements.txt
python setup.py
```

`setup.py` te pedirá las claves y tu banca, encontrará tu Chat ID, enviará un mensaje de prueba y te ofrecerá programar el informe diario (por defecto a las 08:00).

Prueba sin gastar créditos:

```bat
python value_betting_analyzer.py --demo
```

Ejecución real:

```bat
python value_betting_analyzer.py               :: analiza y envía a Telegram
python value_betting_analyzer.py --no-telegram :: solo en pantalla
python value_betting_analyzer.py --stats       :: resultados de tu historial
```

## 3. Programar (si no lo hizo setup.py)

Programador de tareas → *Crear tarea básica* → Diariamente 08:00 → *Iniciar un programa* → `C:\ValueBetting\run_daily.bat`. El PC debe estar encendido a esa hora; si no, usa la opción en la nube.

## 4. Opción en la nube (sin PC encendido)

1. Sube este repositorio a GitHub (privado recomendado).
2. *Settings → Secrets and variables → Actions → New repository secret*: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `ODDS_API_KEY`, `FOOTBALL_DATA_KEY`.
3. (Opcional) en la pestaña *Variables*: `BANKROLL`, `MIN_EV`.
4. Corre cada día a las 08:07 (CDMX). Para probarlo: *Actions → Informe diario → Run workflow*.

## 5. Ajustar la estrategia

Edita `config.json` (lo crea `setup.py`) y añade las claves que quieras cambiar:

| Clave | Por defecto | Qué hace |
|---|---|---|
| `MIN_EV` | `0.02` | EV mínimo. `0` = todo valor positivo (más picks, más ruido). |
| `MAX_PICKS` | `10` | Picks máximos por día. |
| `MIN_ODDS` / `MAX_ODDS` | `1.40` / `6.00` | Rango de cuotas aceptado. |
| `BOOKMAKERS` | `[]` | Solo las casas donde tienes cuenta, p. ej. `["Bet365", "Codere"]`. **Muy importante**: un pick en una casa donde no puedes apostar no te sirve. |
| `BANKROLL` | `1000` | Tu banca, para calcular el stake. |
| `KELLY_FRACTION` | `0.25` | Agresividad del stake. |
| `HOURS_AHEAD` | `24` | Horas hacia adelante que se analizan. |
| `TIMEZONE` | `America/Mexico_City` | Zona horaria del informe. |
| `REGIONS` | `eu` | Regiones de casas en The Odds-API (`eu`, `uk`, `us`, `au`). Cada región extra multiplica los créditos. |

## Ejemplo de informe

```
📊 Value Betting · 18/10/2026
Partidos revisados: 4 · Picks con valor: 2

✅ PICKS (EV ≥ 2.0%, de mayor a menor)
1. Arsenal vs Chelsea
🏆 Premier League · 🕒 18/10 06:00
🎯 2 – Chelsea @ 4.20 (William Hill)
📈 EV +6.1% · Prob. justa 25.3% · Cuota justa 3.96
💰 Stake: 0.5% de banca ≈ 4.78 · 6 casas (Pinnacle+consenso)
...
🔎 Revisados sin valor
• 09:00 Inter vs Torino — mejor EV -1.6%

📒 Historial
42 picks liquidados · Aciertos 38.1% (esperado 36.4%) · ROI +4.2% · Beneficio +31.50
```

## Pruebas

```bash
pip install pytest
python -m pytest -q
```
