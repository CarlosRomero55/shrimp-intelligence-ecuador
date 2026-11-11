# Insights — Exportaciones de camarón del Ecuador (2018–2026)

Qué encontramos, por qué importa, qué recomendamos y qué se puede construir a partir de aquí.

**Cómo leer este documento**
- **Datos del proyecto:** cifras calculadas en este proyecto y validadas en tres capas: consultas SQL (`docs/hallazgos_sql.md`), API (`docs/api.md`) y Power BI (`docs/powerbi_guia.md`). Total anual = Banco Central del Ecuador (BCE, FOB); detalle por país = UN Comtrade.
- **Contexto externo:** información de prensa especializada y analistas, citada al final con su fecha. No se mezcla con los datos del proyecto.
- **Hipótesis:** explicaciones posibles que los datos disponibles no alcanzan a probar.
- La finca "Acuícola Del Golfo" es **SIMULADA**: el método es real; las cifras no describen a ninguna empresa.
- No es asesoría financiera ni comercial.

---

## Resumen ejecutivo

Ecuador exportó **8.401 M USD** de camarón en 2025, **2,6 veces** lo de 2018 (3.190 M USD). De enero a agosto de 2026 lleva **6.561 M USD** (+18,1 % frente a los mismos meses de 2025). El crecimiento es real, pero tiene tres debilidades que los datos muestran con claridad:

1. **Depende de un solo comprador:** China compra entre 42 % y 52 % del valor.
2. **Ese comprador paga cada vez menos por libra:** 2,73 USD en 2018 frente a 2,13 en 2026. EE. UU. paga **47 % más** (2024) y **64 % más** (2026, 3 meses).
3. **El sector creció por volumen, no por precio:** entre 2018 y 2020 vendió 39 % más libras a un precio 11 % menor.

La conclusión central: **el próximo salto del sector no está en producir más, sino en vender mejor cada libra** (más valor agregado, mejores mercados y mejores tallas) y en **gestionar el riesgo de concentración con datos**.

| # | Insight | Cifra clave (datos del proyecto) |
|---|---|---|
| 1 | Ecuador depende de un solo comprador | China = 42–52 % del valor; HHI máx. 3.521 (2023) |
| 2 | El mayor comprador es el que paga menos | EE. UU. paga 47 % más por libra que China (2024) |
| 3 | Se creció por volumen, no por precio | 2018–2020: +39 % de libras, −11 % de precio |
| 4 | El año tiene un mes débil y uno fuerte | Enero 82, mayo 116 (100 = mes promedio) |
| 5 | La talla de cosecha decide el margen *(SIMULADO)* | 20/30: 54,6 % frente a 50/60: 27,8 % |
| 6 | Cada fuente sirve para una pregunta distinta | Ecuador vs BCE: −1,1 % (2024); espejo mensual: hasta ±50 % |

---

## 1. Ecuador depende de un solo comprador

**Qué encontramos**
- China compró entre el **42 % y el 52 %** del valor exportado cada año de 2019 a 2024 (**44,9 %** en 2024). El segundo, EE. UU., tuvo 20,6 %.
- El índice de concentración HHI superó **2.500** (umbral de "muy concentrado") en **todos** los años comparables, con un máximo de **3.521 en 2023**.
- Los **3 principales destinos** suman entre **74 % y 82 %** del valor.
- **Simulador (2024):** si China pagara **10 % menos**, Ecuador dejaría de recibir **314 M USD (−4,5 % del total)**; con **20 % menos**, **627 M USD (−9,0 %)**. Si además comprara 5 % menos, el impacto con −10 % de precio llega a **−455 M USD (−6,5 %)**.

**Por qué importa**
Una sola decisión de un país (precio, aranceles, requisitos sanitarios o su propia producción) mueve casi la mitad del ingreso del sector. Ya pasó: según la prensa, China suspendió **14 plantas** ecuatorianas entre octubre de 2025 y enero de 2026 por residuos de metabisulfito; reintegró 8 en agosto de 2026 y a octubre **6 seguían suspendidas**, con un costo estimado de **45 M USD** para el sector en 2026 (contexto externo [3]). La concentración no es solo un porcentaje: es un riesgo con monto.

**Qué recomendamos y por qué**
1. **Medir el riesgo, no solo el volumen.** Incluir en el tablero del sector el HHI, la participación del principal comprador y un escenario de estrés como el del simulador. *Por qué:* lo que no se mide no se gestiona; hoy el riesgo se ve recién cuando ocurre.
2. **Diversificar hacia mercados que ya compran y pagan más** (EE. UU. y Europa), con metas graduales; por ejemplo, que ningún destino supere el 40 % del valor. *Por qué:* no se trata de abandonar China, sino de que un shock allí no golpee a medio sector.
3. **Blindar el cumplimiento sanitario por mercado:** trazabilidad de lotes y resultados de laboratorio por planta, con los criterios de cada importador. *Por qué:* el riesgo con China ya no es solo de precio, también de acceso.

**Cómo medirlo:** HHI anual < 2.500; participación del principal comprador; impacto del escenario "China −10 %" como porcentaje del total.

*Límite:* 2025–2026 no se comparan por país, porque China no reportó sus importaciones y más de la mitad del valor quedó sin país identificado.

## 2. El mayor comprador es el que paga menos

**Qué encontramos** (precio FOB por libra declarado por Ecuador)

| Año | China | EE. UU. | EE. UU. vs China |
|---|---|---|---|
| 2018 | 2,73 | 3,18 | +17 % |
| 2019 | 2,67 | 2,89 | +9 % |
| 2020 | 2,37 | 2,96 | +25 % |
| 2021 | 2,65 | 3,37 | +27 % |
| 2024 | 2,25 | 3,30 | **+47 %** |
| 2026 (3 meses) | 2,13 | 3,49 | **+64 %** |

*Sin dato FOB en 2022, 2023 y 2025. Europa queda en el medio (2,88 USD/lb en 2024).*

China paga cada vez menos y la brecha con EE. UU. se abre. Cada libra que va a China rinde cerca de un tercio menos que una que va a EE. UU.

**Por qué China rinde menos que EE. UU.**
Los datos del proyecto muestran la brecha, pero no su causa: las partidas arancelarias usadas (HS 030617 y 1605) no separan el tipo de producto. El contexto externo apunta a cuatro razones, que hay que tratar como **hipótesis por verificar**:
1. **Qué compra cada uno.** El producto más común hacia Asia es el camarón **entero, con cabeza y cáscara**, de menor valor por libra. Europa y EE. UU. demandan cada vez más **cocido, pelado y procesado** (contexto externo [5]).
2. **China compra por oportunidad, no por necesidad.** Analistas de S&P Global describen la demanda china como "oportunista, no garantizada": en el primer semestre de 2026 importó ~22 % más volumen **a menor valor**, presiona precios y depende de si su propia producción (~2,5 millones de toneladas previstas para 2026) le resulta más barata que importar (contexto externo [2]).
3. **Exceso de oferta mundial.** India e Indonesia también presionan precios; la oferta crece más rápido que la demanda (contexto externo [5]).
4. **Riesgo sanitario y de acceso** (insight 1).

**¿Entonces China "no sirve"?** No. China sigue siendo **indispensable por volumen**: compra casi la mitad y absorbe la producción que otros mercados no toman. El punto es que **no puede ser el motor del crecimiento en valor**. La propia Cámara Nacional de Acuacultura atribuye los resultados récord de 2025 al dinamismo del mercado estadounidense (contexto externo [4]).

**Qué recomendamos y por qué**
1. **Separar el efecto mercado del efecto producto** antes de decidir: conseguir datos por tipo de producto (entero, cola, pelado, cocido) por destino; por ejemplo, de la aduana ecuatoriana (SENAE) con partidas de más dígitos. *Por qué:* si la brecha es de producto, la solución es procesar más; si es de mercado, es diversificar.
2. **Invertir en valor agregado** (pelado, cocido, porcionado, listo para cocinar) orientado a EE. UU. y Europa. *Por qué:* es la forma de capturar el precio que hoy ya pagan esos mercados; los analistas señalan que Ecuador ya está moviéndose en esa dirección (contexto externo [2]).
3. **Negociar con China por calidad y talla**, no solo por volumen. *Por qué:* las tallas grandes sostuvieron los precios en 2026 cuando escasearon (contexto externo [5]).

**Cómo medirlo:** USD por libra por mercado; prima de EE. UU. sobre China; participación del valor agregado en las exportaciones.

## 3. Se creció por volumen, no por precio

**Qué encontramos**
- Entre 2018 y 2020, las libras con país identificado crecieron **39 %** (de 1.042 a 1.449 millones), mientras el precio FOB cayó **11 %** (de 2,83 a 2,52 USD/lb). El valor creció menos que el volumen.
- En 2021 el precio se recuperó (+13,5 %, a 2,86 USD/lb).
- Control con el BCE: **2,73 USD/lb FOB en 2025** (8.401 M USD ÷ 3.074 M lb).

**Por qué importa**
Es el patrón de un producto que compite por costo: más oferta presiona el precio hacia abajo, sobre todo en el mercado principal (insight 2). Un año récord en dólares puede esconder un precio en caída. Los analistas advierten que el modelo de alto volumen se vuelve difícil de sostener con sobrecapacidad y costos de alimento en alza (contexto externo [2]).

**Qué recomendamos y por qué**
1. **Reportar siempre el precio por libra junto al valor total**, y separar el crecimiento en dos partes: volumen y precio. *Por qué:* evita celebrar crecimientos que en realidad son pérdida de margen.
2. **Planificar la producción con la demanda, no solo con la capacidad.** *Por qué:* cada libra extra vendida a menor precio arrastra el precio de todas las demás.

**Cómo medirlo:** variación anual del precio por libra; descomposición del crecimiento (volumen × precio).

*Límite:* desde 2022 el precio anual mezcla fuentes (FOB y CIF) y no se compara directamente con los años anteriores.

## 4. El año tiene un mes débil y uno fuerte

**Qué encontramos**
- Índice estacional (100 = mes promedio, años completos 2018–2025): **enero 82** (el más débil), **mayo 116** (el más fuerte) y **noviembre 110,5**.
- Temporada seca (junio–noviembre) **102** frente a lluviosa (diciembre–mayo) **98**: la estacionalidad es moderada.
- El patrón varía entre años: el índice de enero osciló entre 56 y 94.

**Por qué importa**
No hay una temporada "muerta", pero enero es predecible y se repite. Cosechar y embarcar fuerte en enero es salir a vender cuando el mercado compra menos.

**Qué recomendamos y por qué**
1. **Escalonar siembras** para que las cosechas fuertes coincidan con mayo y noviembre. *Por qué:* mejor precio y menos inventario.
2. **Planificar liquidez y logística** para el bache de enero (contratos, frío, fletes). *Por qué:* el mes débil es conocido; sorprenderse con él es evitable.

**Cómo medirlo:** índice estacional por año; participación de enero en el total anual.

*Hipótesis:* el enero débil podría relacionarse con el **Año Nuevo chino** (compras adelantadas en noviembre–diciembre). Se podría probar comparando el índice por mercado (China frente a EE. UU.).

## 5. La talla de cosecha decide el margen *(datos SIMULADOS)*

**Qué encontramos** (finca simulada: 3 sedes, 45 piscinas, 510 ciclos, 2023–2026)

| Talla | Índice de precio | Margen |
|---|---|---|
| 20/30 | 127 | **54,6 %** |
| 30/40 | — | 47,3 % |
| 40/50 | 93 | 37,2 % |
| 50/60 | 82,5 | **27,8 %** |

- Por piscina, el margen se explica casi por completo por el **precio de venta** (correlación **r = 0,90**) y casi nada por la conversión de alimento (FCR, **r = −0,14**).
- Las dos piscinas con mejor FCR (GY-P07 y SE-P11, 1,24) no están entre las más rentables.
- Pasar de 40/50 a 30/40 cuesta unos **8 días más** de cultivo y suma unos **10 puntos de margen**.

**Por qué importa**
Con costos por libra parecidos entre piscinas (0,95–1,04 USD), **vender mejor pesa más que producir un poco más barato**. Esto conecta con los insights 2 y 3: el valor está en la talla y el producto, no solo en el volumen.

**Qué recomendamos y por qué** (para una finca real)
1. **Decidir la cosecha por margen esperado según talla**, no solo por días de cultivo o FCR. *Por qué:* unos días más pueden valer más que el alimento que cuestan.
2. **Revisar las piscinas que cosechan antes de tiempo** y entender por qué (enfermedad, oxígeno, liquidez). *Por qué:* las tallas pequeñas son las que hunden el margen.
3. **Medir talla y peso durante el ciclo** (muestreos, visión por computadora). *Por qué:* sin ese dato no se puede decidir cuándo cosechar.

*Límite:* los datos son simulados y en la simulación el FCR casi no varía. En una finca real el FCR pesaría más; el método se repite igual con los registros de una camaronera.

## 6. Cada fuente sirve para una pregunta distinta

**Qué encontramos**
- Lo que **declara Ecuador** a UN Comtrade se acerca al total del BCE: **−7,7 % en 2018 → −1,1 % en 2024**.
- Lo que **declaran los compradores** (estadística espejo) cuadra en el año (−5,6 % en 2020), pero **mes a mes se aleja hasta ±50 %**: el barco sale en un mes y llega en otro.
- En **2025**, el **53 %** del valor no tiene país, porque China no reportó.
- Por país, solo **9 de 38** coinciden dentro de ±5 %. EE. UU. registra +7 % (coherente con el flete del CIF); **Vietnam −94 %**, **Bélgica −81 %**, **Alemania +36 %** y **Francia +22 %**.
- El total del proyecto coincide con lo que publica la prensa citando al BCE: **8.401 M USD en 2025 (+20,2 %)** (contexto externo [7]) y **6.560 M USD de enero a agosto de 2026 (+18 %)** (contexto externo [3]).
- Un reporte de la industria da **7.475 M USD** para 2025 (contexto externo [1]), con un volumen de 1.394.411 toneladas, que equivale a **3.074 M de libras: el mismo volumen del BCE**. La diferencia está solo en el valor, lo que apunta a una metodología de valoración distinta; no se investigó la causa.

**Por qué importa**
Ninguna fuente sola alcanza. Un análisis con la fuente equivocada lleva a conclusiones falsas; por ejemplo, "en 2025 China dejó de comprar", cuando en realidad no reportó.

**Qué recomendamos y por qué**
1. **BCE para totales, lo que declara Ecuador para el detalle por país y el espejo solo como respaldo anual.** *Por qué:* cada una es confiable para una pregunta distinta.
2. **Mostrar siempre la cobertura** (qué parte del total tiene país) junto a cualquier ranking. *Por qué:* así nadie confunde un hueco de datos con un cambio de mercado.
3. **Automatizar la conciliación** con alertas cuando una fuente se aleje más de 5 % del BCE. *Por qué:* ya está construida en este proyecto (vista `v_reconciliation`, página de Power BI).

*Hipótesis (ADR-006):* Vietnam y Bélgica podrían ser **puertas de entrada** (hacia China y la UE) o clasificar el producto en otra partida; Francia y Alemania podrían recibir camarón ecuatoriano **reexpedido** desde otro país europeo.

---

## 7. Qué demuestra este proyecto

**Sobre el sector**
- Que el crecimiento del camarón ecuatoriano se puede descomponer en **mercado, precio, volumen y temporada** con datos públicos y gratuitos.
- Que el riesgo de concentración se puede **cuantificar en dólares** (simulador), no solo en porcentajes.
- Que las fuentes oficiales y las internacionales **no coinciden**, y que se puede medir cuánto y dónde.

**Sobre el trabajo técnico**
- Un pipeline de datos de punta a punta: extracción (API de UN Comtrade y BCE), arquitectura Medallion en MySQL (bronze, silver y gold), modelo estrella, orquestación con bitácora y **141 pruebas automáticas**.
- **Calidad verificable:** el total preferido cuadra con el BCE en los 104 meses, y la API y Power BI dan las mismas cifras.
- SQL analítico (window functions, HHI, medianas), EDA en Python, una API REST con seguridad de mínimo privilegio y un dashboard con ~40 medidas DAX y un simulador What-If.
- **Honestidad con los datos:** límites y avisos visibles, hipótesis separadas de hallazgos, y lo simulado marcado como tal.

## 8. Puntos a mejorar del proyecto

| Limitación | Efecto | Cómo mejorarlo |
|---|---|---|
| Las partidas HS no separan el tipo de producto | No se puede probar por qué China paga menos | Datos de SENAE con partidas de más dígitos, o datos de la industria por presentación |
| No se cargaron datos de la CNA | La conciliación tiene 3 fuentes, no 4; la diferencia de valor de los reportes de la industria (insight 6) queda sin explicar | Incorporar las estadísticas mensuales de la CNA |
| Precios nominales | No se distingue inflación de cambio real de precio | Deflactar con un índice de precios (por ejemplo, el IPC de EE. UU.) |
| El espejo es CIF | Los precios de 2022, 2023 y 2025 no se comparan con los FOB | Estimar el flete por ruta para convertir CIF a FOB |
| Finca simulada | El insight 5 es un método, no un hallazgo de mercado | Aplicarlo con registros reales de una camaronera |
| Descarga del BCE manual | El pipeline no es 100 % automático | Automatizarla si el BCE publica una API o un archivo estable |
| Ejecución local | El dashboard y la API dependen de la PC | Llevarlo a la nube (por ejemplo, Microsoft Fabric) con actualización programada |
| Sin datos de costos ni clima | No se explican cambios de oferta | Precios de alimento, fenómeno de El Niño, temperatura del mar |

## 9. Qué se puede construir con estos datos (trabajo futuro)

Los datos ya limpios (104 meses, ~10 mil filas de exportación por país y 510 ciclos de finca) permiten pasar de **describir** a **predecir y decidir**:

| Modelo | Pregunta que responde | Datos que usa | Técnica sugerida |
|---|---|---|---|
| **Pronóstico de exportaciones** | ¿Cuánto se exportará en los próximos 6–12 meses? | Serie mensual del BCE + índice estacional | SARIMA o Prophet, validado con los últimos 12 meses |
| **Precio por mercado** | ¿Qué precio por libra esperar por destino? | USD/lb por país y año + talla y producto (cuando existan) | Regresión con efectos por mercado |
| **Elasticidad volumen–precio** | ¿Cuánto baja el precio si Ecuador exporta 10 % más? | Volumen y precio anuales y mensuales | Regresión log-log |
| **Riesgo de concentración** | ¿Cuál es la pérdida probable en un año malo para China? | Variabilidad histórica del precio y la participación china | Simulación Monte Carlo (extensión del simulador) |
| **Alertas de conciliación** | ¿Qué mes o país tiene un dato anómalo? | Brechas entre fuentes | Reglas (±5 %) y detección de anomalías |
| **Decisión de cosecha** | ¿Cuándo conviene cosechar cada piscina? | Talla, días, precio por talla, costo diario | Optimización (margen esperado por día extra) |
| **Predicción de FCR y supervivencia** | ¿Qué piscina va a rendir mal antes de que pase? | Sensores (oxígeno, temperatura), alimentación, densidad | Machine learning (gradient boosting) |

## 10. Qué puede aprender el sector ecuatoriano para liderar en tecnología acuícola

Ecuador ya es líder en volumen; los datos sugieren que el liderazgo siguiente es en **valor e información**:

1. **De producir más a vender mejor.** El precio por libra y la mezcla de mercados importan tanto como el volumen (insights 2 y 3). La tecnología de procesamiento (pelado, cocido y empaque listo para consumo) es lo que permite capturar el precio de EE. UU. y Europa.
2. **Acuicultura de precisión en la finca.** Ya existen en Ecuador alimentadores automáticos y acústicos, sensores de oxígeno y temperatura, y conteo de larvas con visión por computadora. Un caso reporta alimentadores acústicos instalados en ~30.000 hectáreas (contexto externo [6]). El siguiente paso es **unir esos datos con los comerciales**: decidir la cosecha según el precio por talla del mercado (insight 5).
3. **Trazabilidad como ventaja comercial.** Las suspensiones de plantas por China muestran que el acceso a un mercado depende de demostrar calidad con datos: laboratorio, lote y planta, con trazabilidad de punta a punta.
4. **Datos sectoriales compartidos y conciliados.** Las fuentes oficiales, internacionales y de la industria publican cifras que no coinciden (insight 6). Una plataforma común, con reglas claras de qué fuente usar para qué, permitiría al sector y al Estado negociar y planificar con el mismo número.
5. **Gestión del riesgo con analítica.** Tableros con HHI, escenarios de estrés y pronósticos para anticipar shocks de mercado, en vez de reaccionar a ellos.

---

## Hipótesis por verificar

1. **2018: Vietnam como vía hacia China.** Ese año Vietnam fue el principal destino declarado y China solo tuvo el 18 %; desde 2019 se invierte.
2. **Mezcla de producto** (entero frente a pelado) como causa principal del precio bajo de China.
3. **Asimetrías por país:** puertas de entrada (Vietnam, Bélgica, Países Bajos) y reexportación (Francia, Alemania).
4. **Enero débil** por el calendario del Año Nuevo chino.
5. **Diferencia de valor entre el reporte de la industria (7.475 M USD) y el BCE (8.401 M USD)** en 2025, con el mismo volumen.

## Fuentes

**Datos del proyecto:** Banco Central del Ecuador (cuadro 3.1.2, total FOB) y UN Comtrade (Ecuador como exportador y estadística espejo), partidas HS 030617 y 1605, enero 2018–agosto 2026. Consultas `sql/queries/q01`–`q12`, notebook `notebooks/01_eda_exportaciones.ipynb`, API (`docs/api.md`) y dashboard (`docs/powerbi_guia.md`).

**Contexto externo** (consultado el 11 de noviembre de 2026):
1. Shrimp Insights, *Ecuador shrimp exports 2025: +15% volume, +23% value as US and EU offset softer China* — https://www.shrimpinsights.com/byte/ecuador-shrimp-exports-2025-15-volume-23-value-us-and-eu-offset-softer-china
2. AgNavigator, *China remains critical to shrimp trade but is no longer the guaranteed demand engine* (10 de septiembre de 2026) — https://www.agnavigator.com/Article/2026/09/10/china-remains-critical-to-shrimp-trade-but-is-no-longer-the-guaranteed-demand-engine/
3. The Rio Times, *Ecuador shrimp: China keeps 6 plants suspended* (octubre de 2026) — https://www.riotimesonline.com/ecuador-shrimp-china-suspension-6-plants-2026
4. SeafoodSource, *Ecuador's aquaculture chamber credits record-breaking 2025 results to dynamic US market* — https://www.seafoodsource.com/news/aquaculture/ecuador-s-aquaculture-chamber-credits-record-breaking-2025-results-to-dynamic-us-market
5. S&P Global, *Record Ecuadorian exports challenge the narrative of weak shrimp demand* (junio de 2026) — https://www.spglobal.com/energy/en/news-research/latest-news/agriculture/061926-record-ecuadorian-exports-challenge-the-narrative-of-weak-shrimp-demand
6. Vistazo, *Con tecnología se maximiza la producción acuícola en Ecuador* (noviembre de 2021) — https://www.vistazo.com/negocios/con-tecnologia-se-maximiza-la-produccion-acuicola-en-ecuador-AB1047992
7. Primicias, *Un nuevo mapa exportador de Ecuador: camarón supera al petróleo y el cacao al banano, en 2025* — https://www.primicias.ec/economia/exportaciones-ecuador-camaron-petroleo-cacao-banano-2025-115886/
