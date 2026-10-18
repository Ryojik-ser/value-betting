# Value Betting Analyzer

Sistema automático que cada mañana revisa los partidos de **Premier League, La Liga, Serie A, Bundesliga, Champions y Europa League**, elige los picks con **más probabilidad de acierto** (o con valor esperado positivo) y te envía el informe a **Telegram**, con la cuota mínima que debes exigir en **BetMéxico**. Se configura y funciona 100 % desde el celular.

## Dos modos

| Modo | Qué busca | Aciertos típicos | Para quién |
|---|---|---|---|
| **`seguro`** (por defecto) | El resultado más probable de cada partido: victoria del favorito (1/2) o **doble oportunidad** (1X/X2), con ≥65 % de probabilidad. Te dice la **cuota mínima** que debes exigir en **BetMéxico**. | 65–80 % | Quien prioriza ganar seguido. |
| **`valor`** | Cuotas mal puestas en casas europeas (EV positivo). | 30–45 % | Quien busca rentabilidad a largo plazo. |

Cámbialo con la variable `MODE` (`seguro` o `valor`).

### Por qué BetMéxico va con "cuota mínima"

The Odds-API no tiene cuotas de casas mexicanas (BetMéxico, Caliente, Codere MX), y extraerlas automáticamente de su web viola sus términos. Por eso el sistema calcula la **probabilidad real** con 20+ casas internacionales y te da la **cuota mínima** que necesitas:

- Cuota de BetMéxico **≥ mínima** → apuesta.
- Cuota de BetMéxico **< mínima** → sáltalo. Aunque aciertes seguido, a la larga pierdes dinero.
- **⭐ Seguro + valor**: el pick es muy probable *y* alguna casa ya paga más que la cuota justa. Son los mejores del día y salen primero.

### Lo que debes saber

- **Alta probabilidad no es certeza.** Con 75 % de acierto fallas 1 de cada 4. Una derrota a cuota 1.40 borra 2.5 aciertos.
- **No combines los picks en un parlay**: 5 picks al 75 % aciertan juntos solo el 24 % de las veces.
- Las apuestas no son una fuente de liquidez rápida: la varianza a corto plazo puede vaciar la banca en días. **Apuesta solo dinero que puedas perder.** +18.

## Cómo calcula

1. Descarga cuotas 1X2 de 20+ casas (The Odds-API) y les quita el margen.
2. **Probabilidad real** = 70 % Pinnacle (la casa más precisa) + 30 % mediana del resto (mínimo 4 casas).
3. **Modo seguro**: por partido elige la opción más probable entre 1, 2, 1X y X2 que pague al menos 1.20 y tenga ≥65 %. Cuota mínima = 1 ÷ probabilidad.
4. **Modo valor**: `EV = probabilidad × mejor cuota − 1`; pick si EV ≥ 2 %. Stake con Kelly al 25 %.
5. Guarda cada pick, lo liquida con el resultado y reporta aciertos reales vs. esperados, ROI y beneficio.

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `value_betting_analyzer.py` | Motor principal: cuotas, cálculo de valor, historial e informe a Telegram. |
| `setup.py` | Asistente: pide tus claves, obtiene tu Chat ID, verifica todo y programa la tarea diaria en Windows. |
| `run_daily.bat` | Lo que ejecuta el Programador de tareas de Windows. |
| `requirements.txt` | Librerías: `requests` y `tzdata` (zonas horarias en Windows). |
| `.github/workflows/daily.yml` | Opción en la nube: corre solo cada día sin tener tu PC encendido. |
| `tests/` | Pruebas automáticas del cálculo. |

## 1. Instalación desde el celular (sin PC) — ≈15 minutos

Todo se hace desde el navegador y Telegram del teléfono. El sistema corre gratis en GitHub cada mañana y te llega el informe a Telegram.

**a) Bot de Telegram (2 min)**
1. En Telegram busca **@BotFather** → **Iniciar** → envía `/newbot`.
2. Ponle nombre y un usuario que termine en `bot`. Copia el **token** que te da.
3. Abre tu bot nuevo, pulsa **Iniciar** y escríbele `hola` (así el sistema detecta tu Chat ID solo).

**b) The Odds-API (3 min, obligatoria)**
1. Entra a **the-odds-api.com** → *Get API Key* → plan **Free** (sin tarjeta).
2. Copia la **API Key** que te llega por correo.

**c) football-data.org (2 min, opcional)**
1. **football-data.org/client/register** → regístrate → copia la **API Key** del correo. Sirve para liquidar resultados sin gastar créditos.

**d) Pegar las claves en GitHub (5 min)**
1. En el navegador abre tu repositorio en github.com (activa "Versión de escritorio" si no ves *Settings*).
2. *Settings → Secrets and variables → Actions → New repository secret*, y crea:
   - `TELEGRAM_TOKEN` → el token del bot
   - `ODDS_API_KEY` → la clave de The Odds-API
   - `FOOTBALL_DATA_KEY` → la clave de football-data (si la sacaste)
3. Pestaña *Variables* → *New repository variable*:
   - `BANKROLL` → tu banca (ej. `2000`)
   - `MODE` → `seguro`

**e) Probar**
*Actions → Informe diario de apuestas de valor → Run workflow*. En 1 minuto te llega el informe a Telegram. Desde entonces corre solo cada día a las **08:07 (CDMX)**.

> El primer informe te muestra tu Chat ID en el registro de Actions. Guárdalo como secreto `TELEGRAM_CHAT_ID` para no depender de la detección automática.

## 2. Instalación en PC con Windows (alternativa)

```bat
mkdir C:\ValueBetting
cd C:\ValueBetting
:: copia aquí los archivos del proyecto
pip install -r requirements.txt
python setup.py
```

`setup.py` pide las claves, encuentra tu Chat ID, envía un mensaje de prueba y programa el informe diario. Comandos útiles:

```bat
python value_betting_analyzer.py --demo         :: ejemplo sin gastar créditos
python value_betting_analyzer.py                :: análisis real + Telegram
python value_betting_analyzer.py --modo valor   :: cambiar de modo por una vez
python value_betting_analyzer.py --stats        :: resultados del historial
```

## 3. Ajustar la estrategia

Desde el celular: como *Variables* en GitHub. En PC: dentro de `config.json`.

| Clave | Por defecto | Qué hace |
|---|---|---|
| `MODE` | `seguro` | `seguro` (alta probabilidad) o `valor` (EV). |
| `SAFE_MIN_PROB` | `0.65` | Probabilidad mínima de acierto en modo seguro. Súbela a `0.75` para picks más seguros (pagan menos). |
| `SAFE_MIN_ODDS` | `1.20` | Descarta picks que pagarían menos de esto. |
| `SAFE_STAKE_PCT` | `0.02` | Stake fijo por pick en modo seguro (2 % de la banca). |
| `MIN_EV` | `0.02` | EV mínimo. `0` = todo valor positivo (más picks, más ruido). |
| `MAX_PICKS` | `10` | Picks máximos por día. |
| `MIN_ODDS` / `MAX_ODDS` | `1.40` / `6.00` | Rango de cuotas aceptado. |
| `BOOKMAKERS` | `[]` | Solo las casas donde tienes cuenta, p. ej. `["Bet365", "Codere"]`. **Muy importante**: un pick en una casa donde no puedes apostar no te sirve. |
| `BANKROLL` | `1000` | Tu banca, para calcular el stake. |
| `KELLY_FRACTION` | `0.25` | Agresividad del stake. |
| `HOURS_AHEAD` | `24` | Horas hacia adelante que se analizan. |
| `TIMEZONE` | `America/Mexico_City` | Zona horaria del informe. |
| `REGIONS` | `eu` | Regiones de casas en The Odds-API (`eu`, `uk`, `us`, `au`). Cada región extra multiplica los créditos. |

## Ejemplo de informe (modo seguro)

```
🛡️ Picks de alta probabilidad · 18/10/2026
Partidos revisados: 4 · Picks: 3

De estos 3 picks, lo esperable es fallar ≈ 0.9.
Abre BetMéxico y apuesta solo si su cuota es igual o mayor a la indicada.

1. Arsenal vs Chelsea
🏆 Premier League · 🕒 18/10 06:00
🎯 1X – Arsenal o empate (doble oportunidad)
✅ Probabilidad de acierto: 75%
📱 En BetMéxico: cuota mínima 1.34
💰 Stake fijo: 2% de banca ≈ 20.00
...
📒 Historial
42 picks liquidados · Aciertos 73.8% (esperado 72.1%) · ROI +1.9%
```

## Pruebas

```bash
pip install pytest
python -m pytest -q
```
