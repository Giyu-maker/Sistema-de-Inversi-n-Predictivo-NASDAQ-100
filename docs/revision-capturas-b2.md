# Revisión de las capturas de B2 (dashboard): lista de ajustes

Origen: revisión de tres capturas (mercado cerrado real a 1440 px en claro, señal operable con fixtures a 1440 px en oscuro y señal operable a 390 px en claro), más dos hallazgos propios de OpenCode.

Reglas de ejecución:
- P1 es obligatorio, P2 es claridad, P3 solo si sobra tiempo. Un commit por grupo.
- Tope de P1 + P2: medio día. Lo que no entre se anota en la lista de brechas de `docs/informe-b2-sprint3.md`.
- Ante cualquier conflicto, manda el SRS/SDD y el contrato de dirección (`.impeccable/surfaces/frontend-src-app-tsx.md`).
- Contexto de honestidad: el modelo no tiene ventaja direccional demostrada (acierto direccional 47,3 %), así que la interfaz no puede sugerir una señal que el sistema no tiene. Los colores semánticos (verde, ámbar, rojo) comunican el estado del sistema, nunca la dirección del mercado; el rojo queda reservado al Kill-Switch.

## P1: honestidad, claridad y consumo de cuota

1. **El veredicto es el titular de la tarjeta de señal.** Hoy el titular es "Tendencia al alza" en verde y el veredicto va en una frase pequeña. Titular: "Sin señal operable" (calmado, color de texto normal) o "Señal operable" (enmarcado). Debajo: "Tendencia al alza con baja confianza (27,6 % frente a un umbral de 75 %)". Para STABLE: "Sin señal operable · sin movimiento esperado".
2. **Dirección sin colores de estado.** "Al alza" y "A la baja" (tarjeta e historial) van en color de texto normal, con flecha y texto. Hoy "A la baja" sale en ámbar, el mismo ámbar de "Atención".
3. **Regla del umbral legible.** Ancla cada etiqueta a su marca (◆ 27,6 % sobre el rombo, | 75 % sobre el tick) o usa una leyenda con las mismas formas. Hoy las etiquetas de izquierda y derecha no coinciden con la posición de las marcas y parece que el umbral fuera 27,6 %.
4. **Zona horaria visible.** Rotula "hora de Colombia" en las marcas de tiempo y muestra la hora ET entre paréntesis en la última vela y en la franja de sesión, con el pie "Sesión regular". Hoy "14:55:00" convive con "16:00 ET" sin rótulo y parece que la última vela fue una hora antes del cierre (fue la de 15:55 ET).
5. **"Predicción de la sesión anterior" es incorrecto** cuando la predicción es de hace varias sesiones. Usa "Última predicción disponible" con su fecha y, si es anterior a la última sesión, indícalo.
6. **QQQ no es el índice.** Cambia "QQQ, el índice que sigue el sistema" por "QQQ, fondo cotizado que replica el NASDAQ-100 (referencia del sistema)" y "Variación del índice (QQQ)" por "Variación de QQQ". Quita "Las agregaciones superiores se guardan en la base de precios".
7. **Separar los dos "Atención".** "Mercado inestable: la IA espera movimiento en los próximos 15 minutos" frente a "Revisar el sistema: la IA no responde / datos desactualizados / base de datos no disponible". Ámbar en ambos (SDD §6.3), con texto e ícono distintos.
8. **Textos del backend** (fuente única, con test): porcentajes en vez de 0,28 y 0,75; "perfil Moderado" en vez de MODERATE (Conservador, Moderado, Agresivo, RF-403); "CPU" en mayúsculas.
9. **Gate de sesión en el scheduler** (hallazgo de OpenCode). Hoy consulta la API fuera de horario (~288 créditos al día, casi todos inútiles). Solo consulta de lunes a viernes durante la sesión regular (09:30 a 16:00 ET) con un margen corto tras el cierre para capturar la última vela (hasta ~16:10 ET), con lista mínima y configurable de feriados NYSE. La recuperación de huecos sigue corriendo una vez al arrancar y una vez después del cierre. La fecha y la hora de sesión se calculan siempre en `America/New_York`. Con tests: sesión de verano, sesión de invierno (posterior al 01/11/2026), fin de semana, feriado y el cruce vie 30/10 → lun 02/11.
10. **La confianza nunca se muestra como "100,0 %".** Si el redondeo llega a 100, mostrar "más de 99,9 %" (hallazgo de OpenCode).

## P2: claridad

1. **Resultado real "No aplica" engaña** en filas cuyo horizonte cae dentro de la sesión pero faltan velas. Usa "Sin datos para evaluar" y reserva "No aplica" para horizonte fuera de la sesión. Cuando haya resultado, muestra acierto o falla con ícono y texto.
2. **Encabezado con mercado cerrado:** "Mercado cerrado" aparece dos veces. Que la frase aporte algo, calculada con la fecha real (no escrita a mano): "Fin de semana · reabre el lunes DD/MM a las 09:30 ET (HH:MM hora de Colombia)".
3. **XAI:** lo visible en la tarjeta empieza por el método ("Atribución de la decisión (gradiente × entrada)…"). Reordena la plantilla en `src/ai/xai.py`: primera oración = conclusión en lenguaje llano ("Pesó más la amplitud de las velas y el volumen reciente") y el método al final. Conserva el contenido técnico de RF-303 y los tests; los registros antiguos se muestran tal cual dentro de "Ver detalle".
4. **Móvil (390 px):** con 78 velas el gráfico se vuelve una franja negra y pierde la distinción hueco/relleno. Por debajo de ~480 px dibuja la línea de cierres (conserva el resumen textual). Filas del historial con estructura fija de dos líneas (fecha y resultado / flecha y confianza).
5. **Fixtures con un "ahora" coherente:** hoy muestran "hace 22 h" y "faltan 1 h 24 min" a la vez.
6. **Marcador de la regla del umbral** que asoma ~7 px fuera de la pista (hallazgo de OpenCode): alinéalo dentro de la pista.

## P3: si queda tiempo

1. Con valores pequeños muestra 3 decimales: "+0,02 %" de variación con apalancamiento 2× y rendimiento "+0,03 %" parece un error aunque sea redondeo.
2. Si `/api/predictions/latest` ya trae las probabilidades de las tres clases, muéstralas dentro de "Ver detalle". Si no las trae, no cambies el contrato ahora; anótalo como mejora.
