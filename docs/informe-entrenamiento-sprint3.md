# Informe de entrenamiento — Sprint 3, Fase A (Ítem 7)

**Fecha:** 17/10/2026 · **Entrenado en:** Máquina 2, CPU (Ryzen 5 3600) · **Duración:** 9,0 s de entrenamiento + 1,7 s de análisis
**Artefacto:** `models/ritech-mlp-20261018T002724Z.pt` (gitignorado) · **Informe JSON:** `models/ritech-mlp-20261018T002724Z.metrics.json`
**Plan pre-registrado:** `docs/plan-ia-sprint3.md` — ningún número de este informe fue ajustado para llegar a un resultado

> **Estado:** Fase A sin cerrar. Este informe **está pendiente de revisión** antes de pasar al PASO 3 (despliegue en M1/M3).

---

## 1. Datos y corte

| | |
|---|---|
| Velas | 40,000 (OHLCV, `NasdaqTick`, 26/09/2024 → 16/10/2026) |
| Sesiones | 515 · 3 descartadas por ser cortas |
| Muestras | 32,751 · ventana 12 velas (1 h) → horizonte 3 velas (15 min) |
| Cortes | **train 22,862** (27/09/2024→05/03/2026) · **val 4,912** (06/03→25/06/2026) · **test 4,977** (26/06→16/10/2026) |
| Embargo | 15 velas (`WINDOW+HORIZON`) en ambos cortes, verificado por test |
| Banda STABLE | **±0,10 %, fija** (no derivada de datos — regla §1 del plan) |

Distribución de clases (% del corte):

| Corte | DOWN | STABLE | UP | ratio STABLE/liga |
|---|---|---|---|---|
| train | 18.8% | **62.1%** | 19.1% | 3.2× |
| val | 21.6% | 55.8% | 22.6% | 2.7× |
| test | 17.2% | **65.2%** | 17.6% | 3.6× |

Pesos de clase por frecuencia inversa, calculados **solo sobre train**: `DOWN 1.311 · STABLE 0.397 · UP 1.292`.

**El test es más plano que train y val.** Es desplazamiento real de régimen y se documenta en §7.

---

## 2. Resultados en test (evaluado **una sola vez**, al final)

| Modelo | macro-F1 | IC 95% (bootstrap por sesión, 2,000 réplicas) | accuracy | F1 DOWN | F1 STABLE | F1 UP |
|---|---|---|---|---|---|---|
| **MLP 120→64→32→3** | **0.4313** | **[0.4106, 0.4512]** | 0.5614 | 0.2682 | 0.7399 | 0.2857 |
| Lineal 120→3 | **0.4443** | — | 0.6000 | 0.289 | 0.770 | 0.275 |

Los tres cortes, MLP frente a lineal:

| Corte | MLP macro-F1 | Lineal macro-F1 | MLP accuracy | Lineal accuracy |
|---|---|---|---|---|
| train | 0.4803 | 0.4666 | 0.5724 | 0.5889 |
| val | 0.4019 | **0.4141** | 0.4469 | 0.4853 |
| test | 0.4313 | **0.4443** | 0.5614 | 0.6000 |

Entrenamiento: **lineal 24 épocas, MLP 25** (tope 200, early stopping con paciencia 20 sobre *val* macro-F1). Ambos pararon solos y pronto; el val dejó de mejorar antes de la época 30.

---

## 3. Los cuatro baselines (los cuatro reportados, sin excepción)

| # | Baseline | macro-F1 en test | Nota |
|---|---|---|---|
| 1 | **Mayoritario** (siempre `STABLE`) | **0.2631** | El que predice "no pasaría nada". Accuracy sí sería 65.2% — por eso la métrica titular es macro-F1. |
| 2 | **Azar por priors de train** (promedio de 1,000 tiradas) | **0.3328** · IC [0.3203, 0.3462] | Tirada representativa usada en la comparación pareada: 0.3398. |
| 3 | **Persistencia** (clase del movimiento de las 3 velas previas, misma banda ±0,10%) | **0.3941** | Lo que haría un humano mirando el impulso reciente. Es el baseline fuerte. |
| 4 | **Lineal** (misma semilla, mismos lotes, mismo early stopping) | **0.4443** | El mejor de los cuatro. |

**Confirmación exacta de lo pre-registrado:** el plan §3 anticipaba "mayoritario ≈0,26 por debajo del azar ponderado ≈0,33, así que batirlo no demuestra nada". Se cumple al céntimo: 0.2631 frente a 0.3328. **Batir al mayoritario no prueba nada.**

---

## 4. Comparaciones pareadas (bootstrap por sesión, 2,000 réplicas, IC 2.5/97.5)

| Contra | Δ macro-F1 | IC 95% de la diferencia | ¿Ventaja demostrada? |
|---|---|---|---|
| mayoritario | **+0.1682** | [+0.1457, +0.1913] | **SÍ** |
| azar por priors | **+0.0914** | [+0.0667, +0.1155] | **SÍ** |
| persistencia | **+0.0372** | [+0.0182, +0.0557] | **SÍ** |
| **lineal** | **−0.0131** | **[−0.0295, +0.0034]** | **NO** |

### Hallazgo 1 — el MLP **no** supera al lineal

El intervalo de la diferencia **contiene el 0**, así que, según la regla pre-registrada §4, **no hay ventaja demostrada del MLP sobre una sola capa lineal**. De hecho el punto de la diferencia es negativo (−0.0131): el lineal salió mejor en test, en *val* y en early stopping.

> **Esto se reporta tal cual, sin retocar nada.** El MLP no justifica su complejidad frente al baseline lineal con estos datos y este plan.

Sobre los tres baselines clásicos sí hay ventaja demostrada, pero frente a persistencia es pequeña (+0.0372) y el único baseline que lo bate es justamente el lineal.

---

## 5. Matriz de confusión del MLP en test

```
                DOWN   STABLE       UP
      DOWN       253      300      301
    STABLE      488     2262      495
        UP      292      307      279
```

**Hallazgo 2 — el modelo distingue "STABLE de no STABLE", no subidas de bajadas.** De los 854 DOWN reales, acierta 253 y manda 301 a UP. De los 878 UP reales, acierta 279 y manda 292 a DOWN. Las confusiones directas DOWN↔UP (301 y 292) son prácticamente tan frecuentes como los aciertos. F1 por clase: DOWN 0.268, UP 0.286, STABLE 0.740.

Es decir: **en la dirección que usa el Motor Asimétrico 2×, el modelo apenas está por encima del azar.** El macro-F1 de 0.4313 lo sostiene casi entero la clase STABLE.

---

## 6. Calibración y umbrales de perfil de riesgo

### 6.1 Calibración por tramos de confianza (test)

| Tramo | n | Confianza media | Precisión observada | Brecha |
|---|---|---|---|---|
| [0.00, 0.50) | 3,291 | 0.4088 | 0.4260 | **+0.0172** |
| [0.50, 0.60) | 923 | 0.5494 | 0.8028 | **+0.2535** |
| [0.60, 0.70) | 655 | 0.6419 | 0.8580 | **+0.2162** |
| [0.70, 0.80) | 108 | 0.7262 | 0.8241 | **+0.0979** |
| [0.80, 0.90) | **0** | — | — | — |
| [0.90, 1.00] | **0** | — | — | — |

**Hallazgo 3 — la calibración es mala, y en el sentido *conservador*.** El modelo **declara menos confianza de la que tiene**: dice 0,55 y acierta el 80%. **No hay una sola predicción con confianza ≥ 0,80.**

> **Propuesta — NO aplicada, pendiente de consulta:** *temperature scaling* ajustado **solo sobre val** (un solo parámetro T, sin tocar pesos, sin mirar test). Endurecería las probabilidades y permitiría que las predicciones buenas alcancen los umbrales.
> **No se aplica sin decisión expresa**, tal como se acordó.

### 6.2 Umbrales de los perfiles de riesgo (SDD §5.4) sobre test

| Umbral | Perfil | Predicciones | % del test | Prec. global | n UP | Prec. UP | n DOWN | Prec. DOWN |
|---|---|---|---|---|---|---|---|---|
| 0.85 | CONVENTIONAL | **0** | 0.0% | — | 0 | — | 0 | — |
| 0.75 | MODERATE (defecto) | **18** | 0.4% | 0.7778 | **0** | — | **0** | — |
| 0.65 | AGGRESSIVE | **365** | 7.3% | 0.8438 | **0** | — | **0** | — |

**Hallazgo 4 — el más operativamente grave para el Sprint 4.** **Ninguna predicción `UP` ni `DOWN` alcanza siquiera el umbral más bajo (0,65).** Las 365 predicciones que sí lo alcanzan son **todas `STABLE`**.

Consecuencia literal: en Modo Autónomo con cualquiera de los tres perfiles, el sistema **no abriría ni una sola posición** — solo emitiría "no hacer nada" cuando el modelo está seguro, y descartaría todos los `UP`/`DOWN`.

La precisión global de 0.8438 en ≥0.65 es engañosa: es 365 aciertos sobre 365 predicciones `STABLE` en un test donde STABLE es el 65.2% de los reales.

---

## 7. Desplazamiento de régimen (tercios cronológicos del test)

| Tercio | n | macro-F1 | accuracy | DOWN/STABLE/UP reales |
|---|---|---|---|---|
| 1 (26/06–01/09) | 1,659 | **0.4200** | 0.4448 | 0.23 / **0.53** / 0.24 |
| 2 (02/09–07/10) | 1,659 | 0.4145 | 0.6335 | 0.14 / 0.72 / 0.14 |
| 3 (08/10–16/10) | 1,659 | **0.4033** | 0.6058 | 0.14 / 0.71 / 0.15 |

**Hallazgo 5 — la accuracy sube y el macro-F1 baja, y no es casualidad.** Del tercio 1 al 2, la *accuracy* salta de 0.4448 a 0.6335 porque el mercado aplanó (STABLE pasó del 53% al 72%), mientras que el macro-F1 **se deteriora de forma sostenida** (0.4200 → 0.4145 → 0.4033).

Es decir: **medir la accuracy del tercer tercio haría parecer que el modelo mejoró, cuando en realidad el mercado dejó de moverse.** Se mantiene como limitación conocida y documentada (plan §6), y refuerza por qué la métrica titular es macro-F1.

---

## 8. Reproducibilidad

- Semilla 20261017; numpy y torch fijados antes de cada entrenamiento.
- Dos corridas consecutivas dan métricas idénticas (test de suite).
- Artefacto `state_dict` portátil, se carga con `weights_only=True` y `map_location` — igual en CPU (M2) y CUDA (M1).
- Dataset, features y métricas se pueden construir **sin `torch`** (grupo `base`), verificado por test con el import bloqueado.
- Ningún dato sale del servidor (REST-02). Todo cálculo local.

---

## 9. Decisiones que quedan abiertas

1. **Modelo a desplegar: MLP o lineal.** El lineal iguala o supera al MLP en los tres cortes con ventaja **no demostrada** del MLP (IC contiene 0). Opciones: (a) desplegar el lineal por honestidad y simplicidad, (b) mantener el MLP como planificado, (c) investigar antes de decidir.
2. **Calibración.** La propuesta es *temperature scaling* sobre val. Requiere decisión explícita.
3. **Umbrales vs. hallazgo 4.** Ningún `UP`/`DOWN` alcanza 0,65. Opciones: calibrar primero y re-medir, o revisar con el Sprint 4 (RiskManager) si el umbral debe aplicarse de otro modo. **Es bloqueante para el Modo Autónomo.**
4. **Dirección.** F1 0.268/0.286 en DOWN/UP frente a 0.740 en STABLE. Si la señal direccional es lo que importa al motor 2×, el modelo actual no la entrega.

---

*Próximo paso: revisión de este informe → PASO 3 (`serve.py`, cliente, `deploy/m1/`, `docs/despliegue.md`, grupos de dependencias con lockfile).*
