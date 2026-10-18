# Informe de entrenamiento — Sprint 3, Fase A (Ítem 7)

**Fecha:** 18/10/2026 · **Entrenado en:** Máquina 2, CPU (Ryzen 5 3600) · **Duración:** 9,0 s de entrenamiento + 1,7 s de análisis
**Artefacto activo:** `models/ritech-mlp-20261018T021631Z.pt` (gitignorado) · **Informe JSON:** `models/ritech-mlp-20261018T021631Z.metrics.json`
**Plan pre-registrado:** `docs/plan-ia-sprint3.md` — ningún número de este informe fue ajustado para llegar a un resultado

> **Estado:** Fase A completada tras la revisión del informe anterior. Las tres decisiones que quedaron abiertas están resueltas en §10 y documentadas en el código con sus pruebas.

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

Pesos de clase por frecuencia inversa, calculados **solo sobre train**: `DOWN 1.311 · STABLE 0.397 · UP 1.292`. Viajan guardados en el artefacto y son necesarios para §2.

**El test es más plano que train y val.** Es desplazamiento real de régimen y se documenta en §8.

---

## 2. Qué significa "confianza" en este informe

**La confianza que se expone NO es la softmax cruda de la red.** Es el **posterior corregido por prior**:

```
p(c|x) ∝ softmax(c|x) / w_c          y luego se renormaliza
```

**Por qué.** El modelo se entrenó con entropía cruzada ponderada por frecuencia inversa. Eso no cambia a qué clase apunta la red, sino a qué escala: la softmax converge a `q(c|x) ∝ w_c · p(c|x)`. Deshacer el factor devuelve el posterior bajo la distribución **real** de clases, que es el que tiene sentido para decidir.

**Sin parámetros.** No hay temperatura ni nada que ajustar: no se tocó nada contra val ni contra test. Con `w = 1` la corrección es la identidad.

> **Antes de la corrección** (softmax cruda) la calibración mostraba brechas de **+0,25 / +0,22 / +0,10**: el modelo declaraba 0,55 y acertaba el 0,80. Esa subconfianza era en gran parte **un artefacto de la reponderación**, no una propiedad del modelo. Ver §7.

**Consecuencia que hay que saber leer.** La clase que **decide** la red sigue siendo el argmax de la softmax —la misma que se evaluó en las métricas— pero la **confianza** que se reporta es la del posterior corregido de esa clase. En el **38.0 %** de las muestras de test (1,890 de 4,977) la corrección mueve el argmax, es decir: el argmax del posterior corregido no coincide con el que decidió la red. Ese desacuerdo se mide y se reporta (`confianza.divergencia_contra_argmax_corregido` en el JSON) en lugar de esconderse.

---

## 3. Resultados en test (evaluado **una sola vez**, al final)

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

Las métricas de clasificación son idénticas a las del informe anterior: la corrección de prior **no toca la decisión**, solo el número que se expone.

---

## 4. Los cuatro baselines (los cuatro reportados, sin excepción)

| # | Baseline | macro-F1 en test | Nota |
|---|---|---|---|
| 1 | **Mayoritario** (siempre `STABLE`) | **0.2631** | El que predice "no pasaría nada". Accuracy sí sería 65.2% — por eso la métrica titular es macro-F1. |
| 2 | **Azar por priors de train** (promedio de 1,000 tiradas) | **0.3328** · IC [0.3203, 0.3462] | Tirada representativa usada en la comparación pareada: 0.3398. |
| 3 | **Persistencia** (clase del movimiento de las 3 velas previas, misma banda ±0,10%) | **0.3941** | Lo que haría un humano mirando el impulso reciente. Es el baseline fuerte. |
| 4 | **Lineal** (misma semilla, mismos lotes, mismo early stopping) | **0.4443** | El mejor de los cuatro. |

**Confirmación exacta de lo pre-registrado:** el plan §3 anticipaba "mayoritario ≈0,26 por debajo del azar ponderado ≈0,33, así que batirlo no demuestra nada". Se cumple al céntimo: 0.2631 frente a 0.3328. **Batir al mayoritario no prueba nada.**

---

## 5. Comparaciones pareadas (bootstrap por sesión, 2,000 réplicas, IC 2.5/97.5)

| Contra | Δ macro-F1 | IC 95% de la diferencia | ¿Ventaja demostrada? |
|---|---|---|---|
| mayoritario | **+0.1682** | [+0.1457, +0.1913] | **SÍ** |
| azar por priors | **+0.0914** | [+0.0667, +0.1155] | **SÍ** |
| persistencia | **+0.0372** | [+0.0182, +0.0557] | **SÍ** |
| **lineal** | **−0.0131** | **[−0.0295, +0.0034]** | **NO** |

### Hallazgo 1 — el MLP **no** supera al lineal

El intervalo de la diferencia **contiene el 0**, así que, según la regla pre-registrada §4, **no hay ventaja demostrada del MLP sobre una sola capa lineal**. De hecho el punto de la diferencia es negativo (−0.0131): el lineal salió mejor en test, en *val* y en early stopping.

Sobre los tres baselines clásicos sí hay ventaja demostrada, pero frente a persistencia es pequeña (+0.0372) y el único baseline que lo bate es justamente el lineal.

> **Esto no se retocó ni se investigó más.** Se reporta tal cual. La consecuencia operativa está en §10, decisión 1.

---

## 6. Matriz de confusión y acierto direccional

### 6.1 Matriz de confusión del MLP en test

```
                DOWN   STABLE       UP
      DOWN       253      300      301
    STABLE      488     2262      495
        UP      292      307      279
```

**Hallazgo 2 — el modelo distingue "STABLE de no STABLE", no subidas de bajadas.** De los 854 DOWN reales, acierta 253 y manda 301 a UP. De los 878 UP reales, acierta 279 y manda 292 a DOWN. Las confusiones directas DOWN↔UP (301 y 292) son prácticamente tan frecuentes como los aciertos. F1 por clase: DOWN 0.268, UP 0.286, STABLE 0.740.

### 6.2 Acierto direccional

Se miran **solo las muestras en las que tanto la verdad como la predicción son `UP` o `DOWN`**. Las `STABLE` se descartan de los dos lados: si el modelo dice "no pasa nada" y no pasó nada, eso no acredita que sepa hacia dónde se va el mercado. El intervalo usa el **mismo bootstrap por bloques de sesión** pre-registrado, no un intervalo binomial i.i.d. (las ventanas solapan 11 de sus 12 velas).

| Corte | Casos direccionales (verdad y predicción) | Aciertos | Acierto | IC 95% | (de truth direccional) |
|---|---|---|---|---|---|
| **test** | **1,125** | **532** | **47.3 %** | **[43.9 %, 50.8 %]** | 1,732 |
| val | 1,590 | 766 | 48.2 % | [45.0 %, 51.5 %] | 2,171 |

**Hallazgo 3 — el modelo predice si habrá movimiento, no la dirección.** El acierto direccional es 47.3 % y su intervalo **contiene el 50 %**: no se demuestra ninguna ventaja sobre lanzar una moneda al decidir el signo. Val y test coinciden (48.2 % y 47.3 %), así que el resultado no es un capricho de un solo corte.

Es la misma conclusión que §6.1 desde otro ángulo, y con el intervalo puesto: el modelo captura **que el mercado se mueve**, que es justo lo que alimenta el motor asimétrico 2×, pero **no hacia dónde**.

---

## 7. Calibración y umbrales de perfil de riesgo

### 7.1 Calibración por tramos de confianza

Primero se valida en **val** y después se reporta **test una sola vez**, sin ningún bucle de ajuste. Ambos cortes se muestran porque la calibración solo es creíble si se sostiene en dos cortes distintos sin haberse tocado.

**Validación en val:**

| Tramo | n | Confianza media | Precisión observada | Brecha |
|---|---|---|---|---|
| [0.00, 0.50) | 2,883 | 0.2810 | 0.2643 | −0.0167 |
| [0.50, 0.60) | 16 | 0.5407 | 0.2500 | −0.2907 |
| [0.60, 0.70) | 578 | 0.6637 | 0.6349 | −0.0287 |
| [0.70, 0.80) | 846 | 0.7519 | 0.6998 | −0.0522 |
| [0.80, 0.90) | 586 | 0.8361 | 0.7969 | −0.0392 |
| [0.90, 1.00] | 3 | 0.9012 | 1.0000 | +0.0988 |

**Reporte en test (una sola vez):**

| Tramo | n | Confianza media | Precisión observada | Brecha |
|---|---|---|---|---|
| [0.00, 0.50) | 2,103 | 0.2711 | 0.2520 | −0.0190 |
| [0.50, 0.60) | 5 | 0.5058 | 0.4000 | −0.1058 |
| [0.60, 0.70) | 533 | 0.6658 | 0.6979 | +0.0321 |
| [0.70, 0.80) | 1,110 | 0.7533 | 0.7739 | +0.0206 |
| [0.80, 0.90) | 1,188 | 0.8414 | 0.8418 | **+0.0004** |
| [0.90, 1.00] | 38 | 0.9084 | 0.8158 | −0.0926 |

**Hallazgo 4 — la corrección de prior arregla la calibración.** Las brechas pasaron de **+0.254 / +0.216 / +0.098** con la softmax cruda a **−0.019 / +0.032 / +0.021 / +0.000** con el posterior corregido. El tramo [0.80, 0.90), que antes no tenía **ni una sola** predicción, ahora tiene 1,188 y su confianza media (0.8414) coincide con su precisión observada (0.8418) a la cuarta decimal.

Val y test se comportan de forma coherente, lo que sostiene que la corrección **generaliza** y no se ajustó contra ningún corte.

### 7.2 Calibración por clase predicha

| Clase | Corte | n | Confianza media | Precisión observada | Brecha |
|---|---|---|---|---|---|
| DOWN | val | 1,412 | 0.2788 | 0.2677 | −0.0111 |
| STABLE | val | 2,010 | 0.7515 | 0.7109 | −0.0405 |
| UP | val | 1,490 | 0.2867 | 0.2604 | −0.0263 |
| DOWN | **test** | 1,033 | 0.2699 | 0.2449 | −0.0250 |
| STABLE | **test** | 2,869 | 0.7756 | 0.7884 | +0.0129 |
| UP | **test** | 1,075 | 0.2733 | 0.2595 | −0.0137 |

El desglose expone lo que el agregado esconde: **la confianza media de `DOWN` y `UP` es ~0.27, mientras que la de `STABLE` es 0.78.** Las clases direccionales están calibradas con honestidad —declaran poco y aciertan poco—, no están infravaloradas.

### 7.3 Umbrales de los perfiles de riesgo (SDD §5.4)

**Test:**

| Umbral | Perfil | Predicciones | % del test | Prec. global | n UP | Prec. UP | n DOWN | Prec. DOWN |
|---|---|---|---|---|---|---|---|---|
| 0.85 | CONVENTIONAL | **478** | 9.6% | 0.8452 | **0** | — | **0** | — |
| 0.75 | MODERATE (defecto) | **1,846** | 37.1% | 0.8294 | **0** | — | **0** | — |
| 0.65 | AGGRESSIVE | **2,718** | 54.6% | 0.7976 | **0** | — | **0** | — |

**Val (misma lectura):** 0.85 → 167 (3.4 %, prec. 0.8323) · 0.75 → 1,032 (21.0 %, 0.7645) · 0.65 → 1,854 (37.7 %, 0.7174). **Cero `UP` y cero `DOWN` en los tres.**

**Hallazgo 5 — el hallazgo anterior no solo se mantiene: se vuelve nítido.** Con la softmax cruda, ningún `UP` ni `DOWN` alcanzaba 0,65 y se podían atribuir la cifra a la subconfianza de la corrección pendiente. Ahora que la calibración está arreglada y las predicciones buenas **sí** alcanzan los umbrales —478 por encima de 0,85, frente a 0 antes—, la tabla sigue diciendo lo mismo: **las 2,718 predicciones por encima de 0,65 son todas `STABLE`.**

La precisión global de 0.7976 es engañosa: son 2,718 aciertos sobre 2,718 predicciones `STABLE` en un test donde STABLE es el 65.2 % de los reales.

---

## 8. Desplazamiento de régimen (tercios cronológicos del test)

| Tercio | n | macro-F1 | accuracy | DOWN/STABLE/UP reales |
|---|---|---|---|---|
| 1 (26/06–01/09) | 1,659 | **0.4200** | 0.4448 | 0.23 / **0.53** / 0.24 |
| 2 (02/09–07/10) | 1,659 | 0.4145 | 0.6335 | 0.14 / 0.72 / 0.14 |
| 3 (08/10–16/10) | 1,659 | **0.4033** | 0.6058 | 0.14 / 0.71 / 0.15 |

**Hallazgo 6 — la accuracy sube y el macro-F1 baja, y no es casualidad.** Del tercio 1 al 2, la *accuracy* salta de 0.4448 a 0.6335 porque el mercado aplanó (STABLE pasó del 53% al 72%), mientras que el macro-F1 **se deteriora de forma sostenida** (0.4200 → 0.4145 → 0.4033).

Es decir: **medir la accuracy del tercer tercio haría parecer que el modelo mejoró, cuando en realidad el mercado dejó de moverse.** Se mantiene como limitación conocida y documentada (plan §6), y refuerza por qué la métrica titular es macro-F1.

---

## 9. Reproducibilidad

- Semilla 20261017; numpy y torch fijados antes de cada entrenamiento.
- Dos corridas consecutivas dan métricas idénticas (test de suite).
- Artefacto `state_dict` portátil, se carga con `weights_only=True` y `map_location` — igual en CPU (M2) y CUDA (M1).
- **Los pesos de clase viajan dentro del artefacto.** Un artefacto sin ellos se rechaza al cargar, porque sin ellos no se puede deshacer la reponderación ni exponer la confianza correcta.
- Dataset, features y métricas se pueden construir **sin `torch`** (grupo `base`), verificado por test con el import bloqueado.
- Ningún dato sale del servidor (REST-02). Todo cálculo local.

---

## 10. Decisiones tomadas

Las tres decisiones que el informe anterior dejó abiertas, resueltas:

### 1. Modelo desplegado: **MLP**

Se sirve el MLP **por especificación, no por rendimiento**. El SRS §5.3 y el SDD §5.3.2 exigen una red neuronal, y esa es la única razón: el lineal igualó o superó al MLP en los tres cortes y la diferencia **no alcanzó significancia estadística** (§5). Se descarta investigar más.

El lineal **queda como referencia y es seleccionable** con `MODEL_ARCH=mlp|linear`. Ambas se entrenan y se guardan en cada corrida; `active.json` apunta a la que diga la configuración, así que cambiar de opinión es un ajuste de entorno y no un reentrenamiento. Las dos pasan por la misma `LocalPredictiveModel`.

> Este texto es deliberadamente duro porque es una decisión incómoda: **el sistema despliega el modelo que no ganó.** Si un día la especificación permite servir el lineal, el archivo `active.json` ya lo permite sin tocar código.

### 2. Sin *temperature scaling*: corrección de prior sin parámetros

En lugar de un parámetro de temperatura ajustado contra val, la confianza expuesta usa la corrección `p ∝ softmax / w_c` con los pesos de train guardados en el artefacto. **No tiene parámetros que ajustar**, no se tocó nada contra test y arregla la calibración (§7.1). **"Confianza" en todo este sistema = posterior corregido por prior, no softmax cruda.**

### 3. Hallazgo 4: no se calibra ni se mueven umbrales para "pasar"

Se añadió el acierto direccional (§6.2): **532/1.125 = 47.3 %**, IC [43.9 %, 50.8 %], con la lectura de que **el modelo predice si habrá movimiento, no la dirección**.

**Conclusión: sin ventaja direccional demostrada, el sistema se abstiene.** RF-304 cumple su función: el umbral de confianza existe exactamente para que, ante una señal que no se puede defender, el sistema no opere. Ningún `UP` ni `DOWN` alcanza 0,65 en ninguno de los dos cortes, y eso **no** se maquilló recalibrando ni bajando umbrales.

### Límite conocido y declarado

El modelo separa "habrá movimiento" de "no habrá movimiento", con ventaja demostrada sobre los cuatro baselines. **No separa subidas de bajadas con ventaja demostrada**, y el motor asimétrico 2× necesita la dirección. Esto queda escrito para Sprint 4 (ver `docs/backlog.md`, Ítem 9): el `RiskManager` **solo abre posición con `UP`/`DOWN`; `STABLE` nunca abre, con cualquier confianza.**

---

*Próximo paso: PASO 3 (`serve.py`, cliente, `deploy/m1/`, `docs/despliegue.md`, grupos de dependencias con lockfile).*
