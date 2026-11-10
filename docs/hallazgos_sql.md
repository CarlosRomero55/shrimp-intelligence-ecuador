# Hallazgos de las consultas SQL de negocio (Fase 9)

Gráficos de estos hallazgos: `notebooks/01_eda_exportaciones.ipynb` (ver `notebooks/README.md`).

Consultas en `sql/queries/` · se ejecutan con `python -m src.run_queries` · resultados en
`data/processed/queries/*.csv`. Cifras del 2026-11-10 (BCE publicado hasta agosto de 2026).

> **Cómo leer estas cifras**
> - El **total** de cada año es el del BCE (cifra oficial FOB). El detalle por país sale de
>   UN Comtrade: lo que declara Ecuador (X, FOB) y, en los años sin ese dato (2022–2023 y 2025),
>   lo que declaran los países compradores (espejo, CIF: incluye flete y seguro).
> - En **2025 el 53 %** del total no tiene país identificado ("Sin reporte por país") porque China
>   no reportó sus importaciones. Toda conclusión por país de 2025–2026 lleva esa advertencia.
> - La finca **"Acuícola Del Golfo" es SIMULADA** (q07–q10): el método es real, los datos no.

## Concentración de mercados

| # | Pregunta | Hallazgo |
|---|---|---|
| q01 | ¿Cuánto depende Ecuador de cada grupo de mercados? | China concentra **42–52 %** del valor de 2019 a 2024. EE. UU. pasa de **11,5 % (2019) a más del 20 %** desde 2021. Europa se mantiene en 15–23 %. |
| q02 | ¿Quiénes son los principales compradores? | China 1.º y EE. UU. 2.º en todos los años comparables (2023: 3.547 M y 1.445 M USD). Europa aporta 5–6 puestos del top 10, pero cada país pesa poco (España, el mayor, ~5 %). |
| q03 | ¿Qué tan concentrada está la demanda (HHI)? | **"Muy concentrada" (HHI > 2.500) en todos los años comparables de 2019 a 2024**; máximo 3.521 en 2023. Los 3 principales destinos suman **74–82 %**. 2025–2026 parecen "moderados" solo porque falta China (cobertura 47–57 %): **no comparables**. |

## Precio y tiempo

| # | Pregunta | Hallazgo |
|---|---|---|
| q04 | ¿Se vende más por menos? | **Sí en 2018–2020:** volumen identificado **+39 %** y precio **−11 %** (2,83 → 2,52 USD/lb FOB). 2021 recupera (+13,5 %). En 2023 el valor cae −1,2 % con más volumen identificado (indicativo: cambia la fuente). Control cruzado: BCE 2025 = 2,73 USD/lb FOB; espejo 2025 = 3,03 CIF (≈ +11 % por flete y seguro, coherente). |
| q05 | ¿Qué mercado paga más por libra? (solo FOB) | **China paga la libra más barata y cada vez menos: 2,65 (2021) → 2,25 (2024) → 2,13 (2026).** EE. UU. 3,30–3,49. La prima de EE. UU. sobre China pasa de **+9 % (2019) a +47 % (2024) y +64 % (ene–mar 2026)**. |
| q06 | ¿Hay estacionalidad? | **Enero es el mes más débil (índice 82) y mayo el más fuerte (116)**, luego noviembre (111). Temporada seca 102 vs lluviosa 98: estacionalidad **moderada** y variable entre años. |

## Finca (SIMULADO)

| # | Pregunta | Hallazgo |
|---|---|---|
| q07 | ¿Qué piscinas son más rentables? | Margen por piscina **37–48 %** (45 piscinas). La diferencia la explica el **precio por libra** (1,62–1,84; correlación con el margen r = 0,90) más que el **costo** (0,95–1,04): las mejores piscinas venden mejor (talla y momento). |
| q08 | ¿Quién convierte mejor el alimento? | FCR **1,24–1,35**, poca dispersión; casi no explica el margen (r = −0,14). El mejor FCR no coincide con el mejor margen: GY-P07 y SE-P11 empatan en 1.º (1,241) y quedan en los cuartiles 3 y 2 de margen. |
| q09 | ¿Cuánto vale la talla? | **La talla es el principal motor del margen:** 20/30 → índice de precio 127, margen 54,6 %; 40/50 → 93 y 37,2 %; 50/60 → 82,5 y 27,8 %. Las tallas 60/70 y 80/100 tienen 4 y 1 ciclos (referencia, no conclusión). Pasar de 40/50 a 30/40 cuesta ~8 días más y suma ~10 puntos de margen (controlando el año). |
| q10 | ¿Qué finca es más productiva? | Las tres son parecidas (4.800–6.300 lb/ha/ciclo, supervivencia 70–75 %, FCR 1,27–1,31). El margen sigue al **año** (bajo en 2023–2024, alto en 2025–2026): efecto del precio de mercado. |

## Conciliación de fuentes

| # | Pregunta | Hallazgo |
|---|---|---|
| q11 | ¿Qué tan cerca está cada fuente del BCE? | **Ecuador (X) es estable y mejora:** −7,7 % (2018) → −1,1 % (2024), con meses dentro de ±2 % en 2024. **El espejo sirve para el año, no para el mes:** en 2020 cierra −5,6 % pero sus meses van de −41 % a +48 % (desfase entre embarque y llegada). 2025–2026: −53 % y −62 % (China). |
| q12 | ¿Con qué países coinciden las fuentes? | De 38 países con ≥ 12 meses comparables: **9 coinciden** (±5 %: España, Italia, Corea, Chile, Canadá…), **4 son coherentes con CIF** (EE. UU. +7,3 %), **7 tienen el espejo mucho mayor** (Francia +22 %, Alemania +36 %) y **18 menor** (Vietnam −94 %, Bélgica −81 %). En EE. UU. la mediana (+17 %) supera a la ponderada (+7 %): los meses pequeños tienen más brecha. |

## Hipótesis por verificar (no son hechos)

1. **2018: Vietnam como vía hacia China.** Ese año el principal destino declarado fue Vietnam (37 %) y China solo 18 %; desde 2019 se invierte.
2. **Precio bajo de China por mezcla de producto** (camarón entero vs. cola o pelado). Las partidas HS 030617/1605 no separan la presentación.
3. **Asimetrías por país** (ADR-006): Bélgica y Países Bajos como puertas de entrada a la UE; Francia y Alemania reciben camarón ecuatoriano reexpedido; Vietnam clasifica parte del camarón bajo otra partida.
4. **Enero débil** podría relacionarse con el calendario del Año Nuevo chino (compras adelantadas en noviembre–diciembre).

## Limitaciones

- 2022–2023 y 2025 el detalle por país es del espejo (CIF): los rankings entre años con distinta fuente mezclan mercado y fuente.
- Los precios por mercado (q05) solo existen en años con dato de Ecuador: 2018–2021, 2024 y 2026 (ene–mar).
- En la finca simulada, FCR, supervivencia y costo por libra casi no varían entre años; el margen se mueve por precio y talla.