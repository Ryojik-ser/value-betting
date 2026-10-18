# Value Betting Analyzer

Sistema automático que cada mañana revisa los partidos de **Premier League, La Liga, Serie A, Bundesliga, Champions, Europa League y Liga MX**, elige los picks con **más probabilidad de acierto** (o con valor esperado positivo) y te envía el informe a **Telegram**, con la cuota mínima que debes exigir en **BetMéxico**. Se configura y funciona 100 % desde el celular.

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

### Momios americanos y parlays

- Todo se muestra en **momio americano** (como en BetMéxico) con la cuota decimal entre paréntesis. Ejemplo: `-296 (1.34)`. Un momio **paga más** cuanto más cerca de cero esté si es negativo (-250 paga más que -300) o cuanto más alto si es positivo.
- **🎰 Parlays sugeridas** (una selección por partido, de 2 a 3 selecciones):
  - Mercados: ganador (1X2), doble oportunidad, total de goles (más de 1.5 / menos de 3.5 / 2.5), ambos anotan, goles de un equipo y "Crear apuesta" (gana + más de 1.5 goles).
  - Las probabilidades de goles salen de un **modelo de Poisson calibrado con las cuotas del mercado** (Pinnacle + consenso), con un recorte prudente. Se marcan como «est.».
  - Objetivos: 🛡️ *doble más segura*, 💵 *doblar tu dinero (≥ +100)* y 🚀 *estilo ticket (≥ +145)*. Para cada objetivo elige la combinación con **menos selecciones**, porque cada selección extra suma margen de la casa.
  - Córners y remates a puerta **no** se incluyen: no hay datos gratuitos confiables.
- Te dice la **probabilidad real de que salga completa**, el momio total mínimo y cuánto cobras con tu apuesta (`PARLAY_STAKE`, por defecto $5).
- Consume 2 créditos por liga **solo si esa liga juega en las próximas 24 h** (antes lo comprueba con una consulta gratuita). Un día normal gasta 4–10 créditos.
- Cada selección tiene que cumplir su momio mínimo en BetMéxico; si no, el margen de la casa se multiplica en la parlay.

### 🐺 No favoritos con respaldo

El público apuesta sobre todo al favorito o al equipo popular (América, Chivas, Real Madrid…), y las casas recreativas bajan el pago de ese lado. Eso puede dejar al **no favorito pagando más de lo que debería**.

- Por partido busca la mejor selección del no favorito: *gana*, *o empate* (doble oportunidad), *+1.5 de hándicap* (no pierde por 2 o más) o *anota al menos un gol*.
- Filtro: probabilidad **≥ 55 %** y pago **≥ -222 (1.45)**.
- **⭐** cuando alguna casa ya paga por encima de la probabilidad real (valor ≥ 2 %); esos salen primero.
- Se usan como sencillas o como **una** selección dentro de una doble, nunca varios juntos.

### 🩵 Apartado Manchester City · Haaland

Sale **arriba de todo**, pero **solo el día en que juega el City** (partidos dentro del informe del día). Si no juega, no aparece.

- Probabilidades de City / empate / rival y goles esperados.
- Picks del City: gana, gana o empata, más de 1.5 goles, gana + más de 1.5, no recibe gol.
- Picks de Haaland: **anota en cualquier momento**, **2 o más goles** y **Gana City + Haaland anota** ("Crear apuesta").
- Cómo se calculan los goles de Haaland: cada gol esperado del City es suyo con la proporción de goles que lleva en la temporada (con la clave de football-data se calcula sola; sin ella usa 33 %). Si el partido es en menos de 30 h, también descarga los **momios reales de goleador** de las casas (≈2 créditos) y los mezcla con el modelo.
- Iconos: ✅ ≥60 % · ⚠️ 45–60 % · 🎲 menos de 45 %.
- Cámbialo a otro equipo o jugador con `FOCUS_TEAM` y `FOCUS_PLAYER`.

### Lo que debes saber

- **Alta probabilidad no es certeza.** Con 75 % de acierto fallas 1 de cada 4. Una derrota a cuota 1.40 borra 2.5 aciertos.
- **No combines los picks en un parlay**: 5 picks al 75 % aciertan juntos solo el 24 % de las veces.
- Las apuestas no son una fuente de liquidez rápida: la varianza a corto plazo puede vaciar la banca en días. **Apuesta solo dinero que puedas perder.** +18.

## Cómo calcula

1. Descarga cuotas 1X2 de 20+ casas (The Odds-API) y les quita el margen.
2. **Probabilidad real** = 70 % Pinnacle (la casa más precisa) + 30 % mediana del resto (mínimo 4 casas).
3. **Modo seguro**: por partido elige la opción más probable entre 1, 2, 1X y X2 que pague al menos 1.20 y tenga ≥65 %. Cuota mínima = 1 ÷ probabilidad.
4. **Modo valor**: `EV = probabilidad × mejor cuota − 1`; pick si EV ≥ 2 %. Stake con Kelly al 25 %.
5. Guarda **todas** las sugerencias (sencillas, parlays, no favoritos y picks ✅ del City), las liquida con el marcador final y el historial muestra aciertos reales vs. esperados y ROI **por tipo de apuesta**, para saber qué sección te da dinero. Los picks de goleador (Haaland) no se liquidan porque el marcador no dice quién anotó.

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
*Actions → Informe diario de apuestas de valor → Run workflow*. En 1 minuto te llega el informe a Telegram. Desde entonces corre solo cada día a las **08:07 (CDMX)** (informe oficial) y a las **14:07** (🌆 actualización de la tarde: partidos de la tarde-noche, como la Liga MX, con momios más frescos; no se registra en el historial para no contar doble).

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
| `PARLAY_STAKE` | `5` | Cuánto apuestas por parlay (en pesos). |
| `STAKE_AMOUNT` | `5` | Monto fijo por apuesta sencilla (`0` = usar `SAFE_STAKE_PCT`). |
| `PARLAY_MIN_PROB` | `0.40` | Probabilidad mínima para sugerir una parlay. |
| `DOG_MIN_PROB` | `0.55` | Probabilidad mínima para un no favorito. |
| `DOG_MIN_ODDS` | `1.45` | Pago mínimo del no favorito (-222). |
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

## Ejemplo de informe (datos de prueba)

En Telegram los momios salen en recuadro, los títulos en negritas y las notas del analista en un bloque plegable (tócalo para abrirlo).

```
📊 APUESTAS DEL DÍA
Dom 18 oct · BetMéxico · $5 por apuesta

🔎 5 partidos revisados
✅ 4 sencillas · 🎰 3 parlays · 🐺 3 no favoritos

📱 Apuesta solo si BetMéxico paga igual o más que el momio indicado.
Ej.: -300 paga más que -400.

━━━━━━━━━━━━━━━
🩵 MANCHESTER CITY · hoy 12:00
vs Wolves · Premier League · local

📊 Man City 74% · Empate 16% · Wolves 9%
⚽ Goles esperados 2.4 – 0.8
👤 Haaland: ≈33% de los goles del equipo (referencia)

Equipo
✅ Man City o empate -971 · 91% · +$0.51
✅ Gana Man City -292 · 74% · +$1.71
✅ Man City más de 1.5 goles -213 · ≈68% · +$2.35
✅ Gana Man City + más de 1.5 goles -166 · ≈62% · +$3.01
🎲 Man City no recibe gol +123 · ≈45% · +$6.17

Haaland
⚠️ Haaland anota -114 · ≈53% · +$4.38
⚠️ Gana Man City + Haaland anota +121 · ≈45% · +$6.03
🎲 Haaland 2 o más goles +478 · ≈17% · +$23.90
✅ ≥60% · ⚠️ 45–60% · 🎲 <45% · Confirma que Haaland sea titular.

━━━━━━━━━━━━━━━
✅ SENCILLAS · $5 c/u
De 4, lo normal es fallar ~1.

1. Arsenal o empate -296
Arsenal vs Chelsea · 08:00 · Premier League
75% · ganas $1.69

2. Gana Man City -292
Man City vs Wolves · 12:00 · Premier League
74% · ganas $1.71

3. Real Betis o empate -246
Real Betis vs Sevilla · 09:00 · La Liga
71% · ganas $2.03

4. Gana Inter -197
Inter vs Torino · 11:00 · Serie A
66% · ganas $2.54

━━━━━━━━━━━━━━━
🎰 PARLAYS · $5 c/u

🛡️ Segura · 70% · -236
$5 → cobras $7.12
├ Inter o empate -662 · 87%
│  Inter vs Torino · 11:00
└ Más de 1.5 goles -423 · ≈81%
   Man City vs Wolves · 12:00

💵 Dobla tu dinero · 50% · +101
$5 → cobras $10.05
...
```

## Pruebas

```bash
pip install pytest
python -m pytest -q
```
