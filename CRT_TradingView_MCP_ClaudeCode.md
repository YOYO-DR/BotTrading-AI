# Estrategia CRT de Cluti + TradingView MCP con Claude Code / OpenCode

> **Propósito de este documento:** Guía técnica y estratégica completa para que Claude Code/OpenCode pueda operar la estrategia CRT (Candle Range Theory) de Cluti Fx usando TradingView conectado vía MCP (Model Context Protocol).

---

## Índice

1. [¿Qué es CRT?](#1-qué-es-crt)
2. [Ciclo AMD – El corazón de CRT](#2-ciclo-amd)
3. [Setup CRT Alcista paso a paso](#3-setup-crt-alcista)
4. [Setup CRT Bajista paso a paso](#4-setup-crt-bajista)
5. [Daily Bias – Filtro Macro Obligatorio](#5-daily-bias)
6. [CRT Nested – Temporalidades Anidadas](#6-crt-nested)
7. [Modelo 4H CRT – Las velas clave de 1am y 5am EST](#7-modelo-4h)
8. [Horarios (Killzones) óptimos](#8-horarios-killzones)
9. [Mercados recomendados](#9-mercados-recomendados)
10. [Confluencias avanzadas](#10-confluencias-avanzadas)
11. [Errores fatales a evitar](#11-errores-fatales)
12. [Conectar TradingView con Claude Code via MCP](#12-mcp-setup)
13. [Instrucciones para Claude Code – Reglas de operación CRT](#13-instrucciones-claude-code)
14. [Gestión de riesgo](#14-gestión-de-riesgo)

---

## 1. ¿Qué es CRT?

**Candle Range Theory (CRT)** es una estrategia de price action que analiza cada vela de una temporalidad alta como un rango completo con sus propios niveles de liquidez. Es la simplificación definitiva del método ICT (Inner Circle Trader) y del Método Wyckoff, aplicados a velas individuales.

### Definición fundamental

> **Cuando una vela liquida (toma el máximo o mínimo de) una vela previa y el precio regresa dentro de su rango, se proyecta un movimiento hacia el extremo opuesto del rango liquidado.**

### Línea evolutiva de CRT

| Época | Innovador | Concepto | Aplicación |
|-------|-----------|----------|-----------|
| 1930s | Richard Wyckoff | Spring / Upthrust | Estructuras macro |
| 1990s | Linda Raschke | Turtle Soup | Swing trading |
| 2010s | ICT (Michael Huddleston) | Liquidity Sweep / AMD | Daytrading Forex |
| 2024 | Comunidad ICT / Cluti | Candle Range Theory | Velas individuales |

### Por qué funciona CRT

CRT funciona porque los grandes operadores **necesitan liquidez** para ejecutar órdenes de gran tamaño. Los stops y órdenes pendientes se acumulan en los extremos de las velas previas. Al barrer esos niveles, se activan esas órdenes y se genera la liquidez necesaria para absorber grandes posiciones. Tras capturar esa liquidez, el precio revierte hacia el extremo opuesto.

---

## 2. Ciclo AMD

El corazón de CRT es el ciclo **AMD** (Acumulación – Manipulación – Distribución), que es el ciclo de mercado de Wyckoff comprimido en el rango de una sola vela.

```
ACUMULACIÓN  →  MANIPULACIÓN  →  DISTRIBUCIÓN
(consolidación)   (barrido stops)   (movimiento real)
```

### Las 3 fases en detalle

| Fase | Qué ocurre en el mercado | Equivalente Wyckoff |
|------|--------------------------|---------------------|
| **Acumulación (A)** | El precio consolida en un rango. Las instituciones construyen posiciones. | Fase B de Wyckoff |
| **Manipulación (M)** | El precio rompe brevemente un extremo, barre stops (liquidity sweep). | Fase C – Spring/Upthrust |
| **Distribución (D)** | El movimiento real se desarrolla hacia el extremo opuesto. | Fase D – Movimiento tendencial |

### Ejemplo práctico AMD

```
Vela H1 con Máximo: 1.1050 | Mínimo: 1.1000

1. Acumulación: precio lateral entre 1.1020 - 1.1030
2. Manipulación: precio baja a 1.0995 (barre mínimo 1.1000, activa stops)
3. Distribución: precio revierte y sube hacia 1.1050 (extremo opuesto)
```

---

## 3. Setup CRT Alcista

Un setup CRT alcista busca una **entrada en largo** después de que el precio haya liquidado el mínimo de una vela previa.

### Las 3 velas del patrón

```
Vela 1: Establece el RANGO (máximo y mínimo que serán referencia)
  ┌─────┐
  │     │  ← Máximo (TARGET)
  │     │
  └─────┘  ← Mínimo (nivel de liquidez)

Vela 2: LIQUIDA el mínimo (barre stops bajistas)
  ┌─────┐
  │     │
  └─────┘
     │     ← Wick que toca/supera el mínimo de Vela 1

Vela 3: CONFIRMA re-entrada al rango (cierre dentro del rango de Vela 1)
  ┌─────┐  ← Cierre dentro del rango → CONFIRMACIÓN SETUP
  │     │
  └─────┘
```

### Pasos de ejecución

1. **Identificar la vela de referencia** en H1 o H4 con un rango claro. Marcar su máximo y mínimo.
2. **Esperar la liquidación del mínimo** → el precio debe romper por debajo del mínimo de la vela de referencia (activa stops de traders largos).
3. **Confirmar re-entrada al rango** → el precio debe cerrar una vela (en TF de entrada, ej: M15) de vuelta dentro del rango.
4. **Entrada y gestión:**
   - **Entry:** en la re-entrada al rango (o Buy Stop sobre el máximo de la vela alcista de confirmación)
   - **Stop Loss:** por debajo del mínimo del barrido (mínimo de la Vela 2)
   - **Take Profit:** máximo de la vela de referencia (Vela 1) – extremo opuesto

### Checklist CRT Alcista

- [ ] ¿El Daily Bias es alcista? (obligatorio)
- [ ] ¿Existe una vela de referencia clara con rango definido?
- [ ] ¿El mínimo de la vela referencia fue liquidado (barrido)?
- [ ] ¿La Vela 3 cerró DENTRO del rango de la Vela 1?
- [ ] ¿El setup ocurre en una Killzone válida?
- [ ] ¿Hay confluencia con Order Block, FVG o estructura Wyckoff?
- [ ] ¿El R:R es mínimo 1:2?

---

## 4. Setup CRT Bajista

Un setup CRT bajista busca una **entrada en corto** después de que el precio haya liquidado el máximo de una vela previa.

### Las 3 velas del patrón

```
Vela 1: Establece el RANGO
  ┌─────┐  ← Máximo (nivel de liquidez)
  │     │
  └─────┘  ← Mínimo (TARGET)

Vela 2: LIQUIDA el máximo (barre stops alcistas)
     ↑     ← Wick que toca/supera el máximo de Vela 1
  ┌─────┐
  │     │
  └─────┘

Vela 3: CONFIRMA re-entrada al rango
  ┌─────┐  ← Cierre dentro del rango → CONFIRMACIÓN SETUP
  │     │
  └─────┘
```

### Pasos de ejecución

1. **Identificar la vela de referencia** en H1 o H4. Marcar máximo y mínimo.
2. **Esperar la liquidación del máximo** → el precio debe romper por encima del máximo de la vela de referencia.
3. **Confirmar re-entrada al rango** → cierre de vela de vuelta dentro del rango (confirmación de manipulación, no rotura real).
4. **Entrada y gestión:**
   - **Entry:** en la re-entrada al rango
   - **Stop Loss:** por encima del máximo del barrido (máximo de la Vela 2)
   - **Take Profit:** mínimo de la vela de referencia (Vela 1)

### Variantes válidas vs. inválidas

| Escenario | Válido? | Acción |
|-----------|---------|--------|
| Vela 2 cierra dentro del rango + Vela 3 confirma | ✅ Alta probabilidad | Entrar |
| Vela 2 cierra fuera del rango + Vela 3 confirma | ⚠️ Moderado (enfoque Wyckoff) | Entrar con cautela |
| Vela 2 cierra dentro del rango + NO hay vela 3 | ❌ Sin confirmación | NO entrar |
| Vela 2 cierra fuera del rango + NO hay confirmación | ❌ Inválido | NO entrar |

---

## 5. Daily Bias

**El Daily Bias es el filtro macro más importante en CRT.** Un CRT perfecto en M15 tiene muy baja probabilidad si la tendencia diaria va en contra.

### Cómo determinar el Daily Bias

1. **Analizar el gráfico Diario (D1):** ¿Hay máximos y mínimos crecientes (alcista) o decrecientes (bajista)?
2. **Buscar CRT en D1:** ¿El precio liquidó el mínimo diario y re-entró? → Bias alcista. ¿Liquidó el máximo y re-entró? → Bias bajista.
3. **Confirmar con H4:** La estructura de H4 debe coincidir con D1.

### Regla de operación

```
Daily Bias ALCISTA  → Solo buscar CRTs alcistas en H1/M15
Daily Bias BAJISTA  → Solo buscar CRTs bajistas en H1/M15
Bias AMBIGUO        → No operar hasta claridad
```

### Combinación Daily CRT + Intraday CRT

```
PASO 1: Identificar CRT en D1 → define el bias del día
PASO 2: Bajar a H4 → confirmar estructura a favor del bias
PASO 3: Bajar a H1/M15 → buscar SOLO setups en dirección del bias diario
PASO 4: Ejecutar con confluencias
```

---

## 6. CRT Nested

**CRT Nested (anidado)** ocurre cuando un patrón CRT en una temporalidad baja se forma **dentro del rango** de un CRT en temporalidad alta. Es la máxima confluencia multi-timeframe.

> "The more inside bars, the higher the probability" – cuantas más velas internas contenga el rango antes de la liquidación, mayor la probabilidad de un movimiento significativo.

### Combinaciones recomendadas

| TF de Análisis | TF de Entrada | Estilo | Confluencia |
|---------------|---------------|--------|-------------|
| Monthly | Daily | Position Trading | Muy alta |
| Weekly | H4 | Swing Trading | Alta |
| Daily | H1 | Day Trading | Alta |
| **H4** | **M15** | **Intraday** | **Media-Alta** ← Recomendado Cluti |
| H1 | M5 | Scalping | Media |

### Cómo operar un CRT Nested (H4 + M15)

1. **Identificar el CRT macro en H4:** Vela cuyo mínimo fue liquidado y precio re-entró → CRT alcista H4.
2. **Bajar a M15:** Dentro del rango H4, buscar un nuevo CRT que se forme.
3. **Esperar la liquidación anidada en M15:** El CRT de M15 liquida su propio sub-rango (que está DENTRO del rango H4).
4. **Ejecutar con confluencia doble:**
   - Stop Loss: debajo del mínimo del CRT M15
   - Take Profit: puede extenderse hasta el máximo del CRT H4

---

## 7. Modelo 4H CRT

El **Modelo 4H CRT** es la aplicación más específica y efectiva de la estrategia. Se basa en las velas de 4 horas que cierran a la **1am y 5am hora EST** como velas de referencia clave.

### Horarios de referencia

| Vela 4H (EST) | Horario UTC | Horario CET | Horario Colombia (COT) | Sesión |
|---------------|-------------|-------------|------------------------|--------|
| **1:00 AM EST** | 6:00 UTC | 7:00 CET | **1:00 AM COT** | Fin Asia / Pre-Londres |
| **5:00 AM EST** | 10:00 UTC | 11:00 CET | **5:00 AM COT** | Mid-Londres |
| **9:00 AM EST** | 14:00 UTC | 15:00 CET | **9:00 AM COT** | Apertura NY |

### Protocolo de operación 4H

```
1. Abrir gráfico H4 del activo (XAUUSD, NQ, ES, EURUSD)
2. Identificar la vela que CERRÓ a la 1am o 5am EST
3. Marcar el MÁXIMO y MÍNIMO de esa vela de referencia
4. Observar la SIGUIENTE vela H4: ¿Liquida algún extremo?
5. Si liquida y re-entra al rango → SETUP VÁLIDO
6. Bajar a M15/M5 para entrada precisa
7. Target: extremo opuesto del rango H4
```

---

## 8. Horarios (Killzones)

| Killzone | Horario CET | Horario Colombia (COT) | Características | Usar CRT? |
|----------|-------------|----------------------|-----------------|-----------|
| Asia | 00:00 - 08:00 | 18:00 - 02:00 | Baja volatilidad, formación de rangos | Formar rango |
| **Londres Open** | **08:00 - 09:00** | **02:00 - 03:00** | Barrido del rango asiático | ✅ Alta |
| Londres Sesión | 09:00 - 12:00 | 03:00 - 06:00 | Continuación del movimiento | ✅ Media |
| Almuerzo | 12:00 - 14:00 | 06:00 - 08:00 | Baja liquidez | ❌ Evitar |
| **NY Open** | **14:30 - 15:30** | **08:30 - 09:30** | Segunda ola de liquidez | ✅ Alta |
| NY Sesión | 15:30 - 17:00 | 09:30 - 11:00 | Desarrollo americano | ✅ Media |
| **London Close** | **17:00 - 18:00** | **11:00 - 12:00** | Reversiones frecuentes | ✅ Alta |

**Patrón clásico de Cluti:** El rango de Asia se forma → Londres abre y barre uno de los extremos (manipulación) → el precio revierte hacia el extremo opuesto. Uno de los setups CRT más fiables.

---

## 9. Mercados Recomendados

| Mercado | Efectividad CRT | Dificultad | TF Análisis | TF Entrada | R:R mínimo |
|---------|-----------------|------------|-------------|------------|------------|
| **Gold (XAUUSD)** | ⭐⭐⭐⭐⭐ | Media | H4 / Daily | M15 / M5 | 1:2 |
| **NAS100** | ⭐⭐⭐⭐⭐ | Media-Alta | H4 | M15 | 1:2 |
| **US30** | ⭐⭐⭐⭐ | Media-Alta | H4 | M15 | 1:2 |
| EUR/USD | ⭐⭐⭐⭐ | Baja | H1 / H4 | M15 | 1:2 |
| GBP/USD | ⭐⭐⭐⭐ | Media | H1 / H4 | M15 | 1:2 |
| BTC/USD | ⭐⭐⭐ | Alta | H4 / Daily | H1 | 1:3 |
| Altcoins | ⭐ | Muy Alta | Daily | H4 | Evitar |

---

## 10. Confluencias Avanzadas

Para aumentar la probabilidad de cada setup CRT, buscar confluencias con los siguientes elementos:

### Order Blocks (OB)
Zona donde el precio se movió de forma explosiva (última vela bajista antes de un movimiento alcista, o viceversa). Cuando la liquidación del CRT ocurre **dentro de un Order Block**, la probabilidad de reversión aumenta significativamente.

```
Identificación de OB:
- OB Alcista: Última vela BAJISTA antes de un movimiento alcista impulsivo
- OB Bajista: Última vela ALCISTA antes de un movimiento bajista impulsivo
```

### Fair Value Gap (FVG)
Zona donde el precio se movió tan rápido que dejó un "hueco de valor justo" (gap entre velas). Si la re-entrada al rango CRT coincide con un FVG, es un punto de entrada óptimo.

```
Identificación de FVG:
- 3 velas: si el cuerpo de vela 3 NO solapa con el cuerpo de vela 1 → existe FVG
- El FVG actúa como imán de precio (precio tiende a "llenar" estos gaps)
```

### SMT Divergence (Smart Money Tool)
Cuando dos activos correlacionados (ej: NAS100 y US30) NO confirman el mismo movimiento. Si NAS100 hace un nuevo mínimo pero US30 NO lo hace → divergencia SMT = señal de reversión.

```
SMT Alcista:  Activo A hace nuevo mínimo | Activo B NO lo confirma → reversión alcista
SMT Bajista:  Activo A hace nuevo máximo | Activo B NO lo confirma → reversión bajista
```

### Tabla de Confluencias

| Confluencia | Peso | Descripción breve |
|-------------|------|-------------------|
| Daily Bias alineado | ⭐⭐⭐ | Obligatorio. Sin esto, no operar. |
| CRT Nested (2 TF) | ⭐⭐⭐ | CRT en H4 + CRT en M15 anidado |
| Order Block | ⭐⭐⭐ | Liquidación dentro de OB |
| Fair Value Gap | ⭐⭐ | Re-entrada coincide con FVG |
| SMT Divergence | ⭐⭐⭐ | Divergencia entre activos correlacionados |
| Killzone válida | ⭐⭐ | Operando en horario de alta liquidez |
| Estructura Wyckoff | ⭐⭐⭐ | CRT en Fase C de acumulación/distribución |

---

## 11. Errores Fatales

### Error 1 – Entrar sin confirmación (el más común)

❌ **NO:** Entrar en Vela 2 sin esperar que cierre y sin Vela 3 de confirmación.
✅ **SÍ:** Esperar siempre la Vela 3 que cierre DENTRO del rango de la Vela 1.

### Error 2 – Ignorar el Daily Bias

❌ **NO:** Operar CRTs alcistas en M15 cuando el D1 es claramente bajista.
✅ **SÍ:** Filtrar SIEMPRE por el bias de temporalidad mayor. Si no hay claridad, no operar.

### Error 3 – No usar confluencias

❌ **NO:** Operar CRT como patrón aislado en cualquier lugar del gráfico.
✅ **SÍ:** Buscar CRT dentro de Order Blocks, FVGs o zonas de interés institucional.

### Error 4 – Re-operar zonas ya mitigadas

❌ **NO:** Buscar un segundo trade en la misma zona CRT ya alcanzada.
✅ **SÍ:** Un nivel CRT es válido **solo la primera vez** que el precio lo toca. Una vez mitigado, descartarlo.

### Error 5 – Operar fuera de las Killzones

❌ **NO:** Operar durante el almuerzo europeo (12:00 - 14:00 CET) o en horarios de baja liquidez.
✅ **SÍ:** Operar solo en London Open, NY Open o London Close.

---

## 12. Conectar TradingView con Claude Code via MCP

### Opción 1: tradesdontlie/tradingview-mcp (Recomendada – Chrome DevTools Protocol)

Este MCP se conecta directamente a TradingView Desktop usando Chrome DevTools Protocol (CDP), sin necesidad de API keys ni webhooks.

#### Instalación

```bash
# Clonar repositorio
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp

# Instalar dependencias
npm install
```

#### Lanzar TradingView con CDP habilitado

```bash
# Linux (Ubuntu)
google-chrome --remote-debugging-port=9222 https://www.tradingview.com

# O con Chromium
chromium-browser --remote-debugging-port=9222 https://www.tradingview.com
```

#### Configurar Claude Code (claude_desktop_config.json)

**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/ruta/absoluta/tradingview-mcp/src/server.js"]
    }
  }
}
```

#### Agregar via CLI de Claude Code

```bash
claude mcp add --scope user tradingview node "/ruta/absoluta/tradingview-mcp/src/server.js"
```

---

### Opción 2: tradingview-mcp via UV (Python – Datos de mercado)

Esta opción provee datos en tiempo real de precios, sin necesidad de cuenta Pro.

```bash
# Instalar UV y tradingview-mcp
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
uv tool install tradingview-mcp-server
```

#### Configuración en claude_desktop_config.json

```json
{
  "mcpServers": {
    "tradingview-data": {
      "command": "uvx",
      "args": ["tradingview-mcp-server"]
    }
  }
}
```

---

### Opción 3: tradingview-chart-mcp (Capturas de gráfico)

Permite a Claude ver capturas del gráfico de TradingView para análisis visual.

```bash
git clone https://github.com/ertugrul59/tradingview-chart-mcp.git
cd tradingview-chart-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Configuración

```json
{
  "mcpServers": {
    "tradingview-chart": {
      "command": "/ruta/tradingview-chart-mcp/.venv/bin/python",
      "args": ["/ruta/tradingview-chart-mcp/main_optimized.py"],
      "env": {
        "TRADINGVIEW_SESSION_ID": "TU_SESSION_ID",
        "TRADINGVIEW_SESSION_ID_SIGN": "TU_SESSION_ID_SIGN"
      }
    }
  }
}
```

---

### Opción 4: OpenCode (alternativa a Claude Code)

Si usas **OpenCode** en lugar de Claude Code, la configuración MCP es similar pero en el archivo de config de OpenCode:

```bash
# Verificar config de OpenCode
cat ~/.config/opencode/config.json
```

Agregar los mismos bloques `mcpServers` en el JSON de configuración de OpenCode. OpenCode soporta MCP de la misma forma que Claude Code ya que implementa el protocolo estándar.

---

### Cómo obtener Session ID de TradingView (para Opción 3)

1. Abrir TradingView en Chrome
2. Presionar F12 → Application → Cookies → `tradingview.com`
3. Buscar `sessionid` y `sessionid_sign`
4. Copiar los valores a tu configuración

---

### Verificar que el MCP está conectado

```bash
# Con Claude Code CLI
claude mcp list

# Debería mostrar algo como:
# tradingview: node /ruta/tradingview-mcp/src/server.js ● connected
```

---

## 13. Instrucciones para Claude Code – Reglas de Operación CRT

> Esta sección contiene el **system prompt / instrucciones de contexto** que debes proporcionar a Claude Code para que opere con la estrategia CRT correctamente.

---

### SYSTEM PROMPT PARA CLAUDE CODE (Copiar y pegar)

```
Eres un asistente de trading que opera la estrategia CRT (Candle Range Theory) 
desarrollada por Cluti Fx. Debes seguir ESTRICTAMENTE las siguientes reglas:

## IDENTIDAD DEL SISTEMA
- Estrategia: CRT (Candle Range Theory) – metodología de Cluti Fx
- Estilo: Day Trading / Intraday
- Mercados principales: Gold (XAUUSD), NAS100, EUR/USD
- Temporalidad de análisis: D1 → H4 → H1
- Temporalidad de entrada: M15 (primaria), M5 (alternativa)

## REGLA 1: DAILY BIAS (OBLIGATORIO ANTES DE CUALQUIER ANÁLISIS)
Antes de buscar cualquier setup, determina el Daily Bias:
1. Analizar D1: ¿máximos y mínimos crecientes (alcista) o decrecientes (bajista)?
2. Buscar CRT en D1: ¿el precio liquidó mínimo y re-entró? → Bias alcista
3. Si bias alcista → SOLO buscar CRTs alcistas en TF inferiores
4. Si bias bajista → SOLO buscar CRTs bajistas en TF inferiores
5. Si ambiguo → NO OPERAR. Reportar: "Bias no definido, esperando claridad"

## REGLA 2: VALIDACIÓN DEL PATRÓN CRT
Para que un setup sea VÁLIDO se requieren las 3 velas:
- VELA 1: Establece el rango de referencia (marcar máximo y mínimo)
- VELA 2: Liquida (barre) uno de los extremos de Vela 1
- VELA 3: CIERRA DENTRO del rango de Vela 1 (confirmación obligatoria)
⚠️ Sin Vela 3 cerrada = NO HAY SETUP. Nunca entrar anticipadamente.

## REGLA 3: NIVELES DE LA OPERACIÓN
CRT Alcista (liquidó mínimo, re-entró):
- Entry: al cierre de Vela 3 dentro del rango
- Stop Loss: por debajo del mínimo de Vela 2 (mínimo del barrido)
- Take Profit: máximo de Vela 1 (extremo opuesto del rango)
- R:R mínimo aceptable: 1:2

CRT Bajista (liquidó máximo, re-entró):
- Entry: al cierre de Vela 3 dentro del rango
- Stop Loss: por encima del máximo de Vela 2 (máximo del barrido)
- Take Profit: mínimo de Vela 1 (extremo opuesto del rango)
- R:R mínimo aceptable: 1:2

## REGLA 4: HORARIOS VÁLIDOS (Killzones)
OPERAR solo en estas ventanas horarias (hora Colombia COT):
- London Open: 02:00 - 03:00 COT ✅
- London Session: 03:00 - 06:00 COT ✅
- NY Open: 08:30 - 09:30 COT ✅ (PRIORIDAD MÁXIMA)
- NY Session: 09:30 - 11:00 COT ✅
- London Close: 11:00 - 12:00 COT ✅
EVITAR: 06:00 - 08:30 COT (almuerzo europeo, baja liquidez) ❌

## REGLA 5: CONFLUENCIAS REQUERIDAS
Mínimo 2 confluencias para entrar (además del patrón CRT):
- Daily Bias alineado (OBLIGATORIO siempre)
- Order Block en la zona de liquidación
- Fair Value Gap en la re-entrada
- SMT Divergence con activo correlacionado
- CRT Nested (patrón en H4 + M15 anidados)
- Killzone válida

## REGLA 6: REGLA DE MITIGACIÓN
Una zona CRT es válida SOLO LA PRIMERA VEZ que el precio la toca.
Una vez que el precio alcanza el extremo opuesto (take profit),
la zona queda MITIGADA. Nunca buscar segundo trade en misma zona.

## REGLA 7: MODELO 4H (PRIORIDAD)
Las velas de 4H que cierran a 1am y 5am EST son referencias CLAVE.
En horario Colombia (COT): 1am EST = 1am COT | 5am EST = 5am COT
Marcar siempre los rangos de estas velas como niveles prioritarios.

## REGLA 8: GESTIÓN DE RIESGO
- Máximo riesgo por trade: 1% del capital de la cuenta
- Máximo 2 trades simultáneos
- Si pierde 2 trades consecutivos: PARAR por el día
- Break Even al 50% del recorrido hacia el target

## FORMATO DE REPORTE
Al analizar el mercado, reportar siempre:
1. Activo analizado y temporalidad
2. Daily Bias (alcista/bajista/ambiguo) + justificación
3. Setup encontrado (si hay): tipo CRT, velas identificadas, niveles exactos
4. Confluencias presentes
5. Veredicto: OPERAR / NO OPERAR + razón
6. Si OPERAR: Entry, SL, TP, R:R calculado

## PROHIBICIONES ABSOLUTAS
❌ No entrar sin Vela 3 cerrada dentro del rango
❌ No operar contra el Daily Bias
❌ No operar en horario de almuerzo (06:00-08:30 COT)
❌ No re-operar zonas ya mitigadas
❌ No entrar si R:R < 1:2
❌ No operar si el bias es ambiguo
```

---

## 14. Gestión de Riesgo

### Reglas de Money Management

| Parámetro | Regla | Ejemplo (cuenta $10,000) |
|-----------|-------|--------------------------|
| Riesgo por trade | Máximo 1% del capital | $100 por trade |
| Trades simultáneos | Máximo 2 abiertos | 2 posiciones |
| Daily drawdown | Parar si pierde 2% en el día | Stop en -$200/día |
| Consecutive losses | Parar si pierde 2 trades seguidos | Descanso obligatorio |
| Break Even | Mover SL a Entry cuando precio recorre 50% hacia TP | Proteger capital |
| R:R mínimo | 1:2 (riescar 1 para ganar 2) | $100 riesgo → $200 ganancia |

### Cálculo de tamaño de posición

```
Tamaño = (Capital × Riesgo%) / (Stop Loss en pips × Valor pip)

Ejemplo Gold (XAUUSD):
- Capital: $10,000
- Riesgo: 1% = $100
- SL: 50 pips (50 puntos)
- Valor pip 0.01 lot = $0.10

Lots = $100 / (50 × $1) = 0.02 lots
```

---

*Documento generado para uso con Claude Code / OpenCode – Estrategia CRT de Cluti Fx*
*Última actualización: Abril 2026*
