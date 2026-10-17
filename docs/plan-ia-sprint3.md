# Plan de modelado predictivo — Sprint 3, Fase A

**Documento de pre-registración.** Todo lo que aparece aquí se fijó **antes** de
ejecutar el primer entrenamiento, para que no haya ninguna decisión tomada
mirando el resultado. Lo que se decide después de ver test no sirve como
evidencia, y este proyecto tiene un deadline de producción en noviembre de 2026:
no hay margen para descubrir al final que el modelo no sirve.

| Campo | Valor |
| :--- | :--- |
| Versión | 1.0 |
| Fecha | 17/10/2026 |
| Responsable | Naranja's Team |
| Requisitos | RF-301, RF-302, RF-303, RF-304 |
| Estado | **Congelado antes del entrenamiento** |

---

## 1. Etiqueta: banda STABLE fija en ±0,10 %

La clase se define sobre el retorno de las próximas **3 velas (15 min)**,
medido del cierre de la última vela de la ventana al cierre de la última vela
del horizonte:

| Condición | Clase |
| :--- | :--- |
| retorno > **+0,10 %** | `UP` |
| retorno < **−0,10 %** | `DOWN` |
| en otro caso | `STABLE` |

### Regla fija, no derivada de los datos

**La banda es una constante del sistema y no se recalcula en ningún
reentrenamiento.** Esto es deliberado:

1. **Reproducibilidad entre reentrenamientos (RF-302 + RF-303).** El modelo se
   reentrena **cada día**. Si la banda se derivara de los cuantiles del train
   del día, el *significado* de `STABLE` cambiaría cada jornada: una
   predicción de hoy y otra de ayer no serían comparables, y un auditor que
   cruce ambas vería clases con semánticas distintas bajo el mismo nombre.
2. **Comparabilidad de `ai_predictions` durante la retención de 5 años.** La
   tabla guarda `trend_prediction` como texto. Con banda derivada, dos filas
   iguales (`"STABLE"`) significarían cosas distintas según la fecha.
3. **Estabilidad del umbral de negocio.** El Motor Asimétrico y el RiskManager
   interpretan `UP`/`DOWN` como señal de entrada. Que la señal signifique algo
   distinto cada día haría no auditable el resultado de cualquier operación.

### ¿Por qué ±0,10 % y no otro valor?

Sobre el dataset completo (32.781 ventanas, 518 sesiones, retorno a 15 min con
σ = 0,1749 %):

| Banda | DOWN | STABLE | UP | Desbalance |
| --- | --- | --- | --- | --- |
| ±0,020 % | 41,2 % | 15,3 % | 43,4 % | 2,8x |
| ±0,050 % | 30,6 % | 36,0 % | 32,1 % | **1,2x** |
| ±0,075 % | 24,0 % | 50,0 % | 25,0 % | 2,1x |
| **±0,100 %** | 19,0 % | **61,6 %** | 19,4 % | 3,2x |
| ±0,125 % | 15,0 % | 69,8 % | 15,1 % | 4,6x |
| ±0,200 % | 7,9 % | 84,8 % | 7,4 % | 11,5x |

* **±0,10 % es económicamente interpretable**: 10 puntos básicos en 15 minutos,
  un umbral redondo que un operador entiende sin consultar el código.
* **±0,05 % equilibraría las clases (1,2x) pero etiqueta como "merece operar"
  un movimiento de 5 pb en 15 min = 0,29σ**, por debajo del suelo de ruido del
  instrumento. Con apalancamiento 2x y techo de pérdida de 1,25 % por jornada,
  actuar sobre ruido paga comisiones sin esperanza de cobrar el spread.
* **Ensancha la banda solo empeora el desbalance**: ancho de banda y dominancia
  de `STABLE` son monótonos. La única vía para equilibrar es estrechar.
* El desbalance de 3,2x es **moderado**, no catastrófico (eso sería 95/2/3) y
  es **intrinsicamente propio de los retornos intradía**, no un artefacto de la
  banda elegida.

### Consecuencia declarada

Con `STABLE` en 61,6 %, **la accuracy no es una métrica útil**: el baseline
mayoritario ya la alcanza sin hacer nada. **La métrica titular de este modelo
es el macro-F1**, acompañado de recall por clase y matriz de confusión.

---

## 2. Arquitectura e hiperparámetros (congelados)

| Parámetro | Valor | Motivo |
| :--- | :--- | :--- |
| Ventana | **12 velas (60 min)** | Ajuste del plan Sprint 3 |
| Horizonte | **3 velas (15 min)** | Ajuste del plan Sprint 3 |
| Features por vela | **10** (`src/ai/features.py`) | 4 de ellas dependen de CR-001 |
| Entrada aplanada | **120** | 12 × 10 |
| Arquitectura | **MLP 120 → 64 → 32 → 3**, ReLU | Ajuste del plan Sprint 3 |
| Parámetros | **9.923** | |
| Dropout | **0,20** en ambas capas ocultas | Regularización frente a 2,3 muestras/parámetro |
| Optimizador | **AdamW** | |
| Learning rate | **1e-3** | |
| Weight decay | **1e-4** | |
| Batch size | **256** | |
| Épocas máximas | **200** | |
| Early stopping | sobre **val macro-F1**, paciencia **20** | Espejo exacto de la métrica titular |
| Semilla | **20261017** | Reproducibilidad |
| Dispositivo de entrenamiento aquí | **CPU** (M2) | La GPU de M1 es el destino, no el banco de pruebas |

> **Lo que se permite ajustar y contra qué:** solo `weight_decay`, `dropout` y
> la paciencia de early stopping, y **solo contra `val`**. El conjunto de test
> se evalúa **una única vez**, al final, y sus números no retroalimentan
> ninguna decisión.

### Desbalance de clases: pérdida ponderada

Declarada **de antemano** (no es un ajuste posterior):

- Pérdida = **entropía cruzada con pesos por frecuencia inversa de clase**.
- Pesos = `N_train / (3 × n_c)` calculados **solo sobre train** y guardados en
  el artefacto, para que inferencia y reentrenamiento usen los mismos.
- No se usa sobremuestreo ni SMOTE: alteraría la distribución temporal y
  rompería la lectura honesta de la calibración.

---

## 3. Referencias de comparación (4 baselines, reportados TODOS)

La barra no es "superar el azar". Con 61,6 % de `STABLE`, un modelo que siempre
dice `STABLE` acierta 3 veces de cada 5 y aun así **macro-F1 ≈ 0,26**, por
debajo del azar ponderado por priors (**≈ 0,33**). Superar eso no demuestra
nada, así que el umbral real es más alto.

| # | Baseline | Cómo se define |
| :--- | :--- | :--- |
| 1 | **Mayoritario** | Predice siempre la clase más frecuente de **train** |
| 2 | **Azar por priors** | Muestra una clase según la distribución de **train**, con semilla fija (media de 1.000 corridas) |
| 3 | **Persistencia** | Clase del retorno de las **3 velas previas** al punto de predicción, con la **misma banda ±0,10 %**. Es la referencia real: "si ayer subió, hoy sube" |
| 4 | **Lineal** | Una sola capa `Linear(120 → 3)`, torch puro, **mismo procedimiento de entrenamiento** (misma optimización, mismas épocas, mismo early stopping) |

> **Regla de justificación:** el MLP debe superar al baseline **lineal** para
> justificar su complejidad. Si no lo supera, **se reporta tal cual** y se
> propone quedarse con el lineal, que además es más rápido, más pequeño y más
> fácil de auditar.

---

## 4. Intervalos de confianza: bootstrap por bloques de sesión

**No se usa intervalo binomial i.i.d.** Las muestras de una misma sesión se
construyen con ventanas desplazadas una vela: **se solapan en 11 de sus 12
velas**, así que no son independientes y un intervalo i.i.d. quedaría
estimado varios órdenes de magnitud demasiado estrecho.

| Aspecto | Decisión |
| :--- | :--- |
| Método | **Bootstrap por bloques de sesión**: se remuestrean **sesiones completas** con reemplazo |
| Réplicas | **≥ 1.000** (fijo: 2.000) |
| Intervalo | Percentiles **2,5 / 97,5** |
| Comparación contra cada baseline | **Bootstrap pareado**: mismas sesiones remuestreadas para el modelo y para el baseline, y se construye el intervalo de la **diferencia de macro-F1** |
| Regla de lectura | **Si el intervalo de la diferencia incluye el 0, no hay ventaja demostrada** y así se reporta, sin excepción |

---

## 5. Calibración y umbrales de perfil de riesgo

RF-304 exige un ratio de confianza 0–100 % y el SDD fija los umbrales de los
perfiles de riesgo. Se reportará:

1. **Calibración por tramos de confianza** `[0,50) [0,60) [0,70) [0,80) [0,90) [1,00]`,
   con nº de predicciones, confianza media y **precisión observada** en cada
   tramo. Si la precisión observada se aleja de la confianza declarada, el
   ratio no es usable como umbral de negocio.
2. **Tabla de umbrales** para los tres perfiles del SDD:

   | Perfil | Confianza mínima |
   | :--- | :--- |
   | `CONVENTIONAL` | 0,85 |
   | `MODERATE` (por defecto) | 0,75 |
   | `AGGRESSIVE` | 0,65 |

   Para cada umbral: cuántas predicciones de **test** lo superan (n y %) y
   **qué precisión tienen en `UP` y en `DOWN` por separado**, que es lo que
   importa para el Motor Asimétrico.

3. La confianza se define como **la probabilidad softmax de la clase ganadora**.

> **Si la calibración sale mala, se propone *temperature scaling* ajustado en
> val y se consulta antes de aplicarlo.** No se aplica por cuenta propia: cambia
> el número que el RiskManager compara contra sus umbrales.

---

## 6. Limitación conocida: desplazamiento de régimen

**El conjunto de test es más plano que train y val, y esto es una limitación
delimitada, no un defecto corregible.**

| Corte | Periodo | DOWN | STABLE | UP |
| :--- | :--- | :--- | --- | --- |
| train | 27/09/2024 → 05/03/2026 | 18,8 % | 62,2 % | 19,1 % |
| val | 06/03/2026 → 25/06/2026 | 21,5 % | **55,8 %** | 22,6 % |
| test | 26/06/2026 → 16/10/2026 | 17,4 % | **64,8 %** | 17,8 % |

En las features normalizadas, el test se desplaza respecto a train en
`range_vol` −0,14σ, `log_range` −0,13σ y `ret_vol` −0,10σ: **menos volatilidad
y menos amplitud de vela** en el periodo más reciente.

Esto es **desplazamiento real de mercado**, no un bug del pipeline. Consecuencias
que se declaran de antemano:

* Se espera que el macro-F1 de test sea **menor** que el de val. Una brecha
  negativa no es sorpresa; una brecha grande **sí** es hallazgo.
* Se reporta **macro-F1 por tercios cronológicos del test**, para ver si el
  deterioro es uniforme o concentrado en el tramo más reciente (que es el que
  anticipa la producción).
* No se recorta, repondera ni reetiqueta test para acercarlo a train: eso sería
  exactamente el ajuste contra test que este plan prohíbe.

---

## 7. Fuera de alcance de este documento

Pertenecen al PASO 3 (no se tocan hasta que este reporte sea revisado):

`src/ai/serve.py`, `src/ai/client.py`, `deploy/m1/`, `docs/despliegue.md`,
grupos de dependencias `base`/`ai` y el lockfile.
