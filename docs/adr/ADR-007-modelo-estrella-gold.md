# ADR-007 — Modelo estrella en gold: fuentes, cifra preferida y cuadre con el BCE

- **Estado:** aceptada
- **Fecha:** 2026-11-09
- **Fase:** 7 (silver → gold)
- **Relacionadas:** ADR-001 (Medallion en MySQL), ADR-002 (3FN en silver, estrella en gold), ADR-006 (estrategia de fuentes)

## Contexto

Power BI y la API leen solo gold. El modelo debía responder dos preguntas que tiran en
direcciones opuestas:

1. **¿Cuánto exporta Ecuador y a dónde?** Con una sola cifra por país y mes, y con un total
   que coincida con la cifra oficial del BCE.
2. **¿Por qué difieren las fuentes?** Sin esconder de dónde sale cada número.

Las fuentes de detalle por país están incompletas (ADR-006): Ecuador no publicó en UN Comtrade
en 2022–2023 ni en 2025, y China no reportó importaciones en 2025–2026.

## Decisiones

### 1. Construcción con SQL dentro de MySQL
`src/build_gold.py` ejecuta `INSERT … SELECT` desde silver. Los datos no salen de la base.
Las llaves de las dimensiones son los IDs de silver (deterministas), así las relaciones de
Power BI no se rompen al recargar.

### 2. Grano de los hechos
| Tabla | Grano | Llave primaria |
|---|---|---|
| `fact_exports` | mes × país × partida HS × **fuente** | `(date_key, country_key, product_key, source_key)` |
| `fact_production` | un ciclo de cultivo (SIMULADO) | `cycle_key` |

### 3. Varias fuentes en `fact_exports` con una cifra preferida
- Se guardan **Comtrade X** (Ecuador, FOB) y el **espejo** (importadores, CIF).
- `is_preferred = 1` marca la cifra que usa el dashboard:
  - X es la preferida en todos sus meses;
  - el espejo es preferido **solo** en meses sin X **y** que el BCE ya publicó (un mes sin total
    oficial sería parcial).
- Por cada mes del BCE se agrega una fila **"Sin reporte por país"** (`country_key = 9999`,
  `product_key = 9999`, fuente BCE) = **total BCE − detalle preferido**.
- **Resultado:** la suma de las cifras preferidas es **exactamente el total del BCE** en los
  104 meses publicados (2018-01 a 2026-08).

### 4. Miembros especiales 9999 (distintos de "Desconocido" = 0)
El 0 representa un dato faltante. El 9999 representa una parte **conocida** del total oficial
que ninguna fuente detalla por país ni partida. Mezclarlos ocultaría el hueco de información
más importante del proyecto (53 % del total de 2025, por la ausencia de China en el espejo).

### 5. Valores negativos solo en "Sin reporte por país"
En 7 meses (5 en 2022, 2 en 2023; el peor, −105,6 M USD) el detalle del espejo supera al BCE,
por la valoración CIF y el desfase entre embarque y llegada. La regla
`chk_fexp_value CHECK (value_usd >= 0 OR country_key = 9999)` permite el negativo **solo** ahí.
Se descartó poner 0 (rompe el cuadre con el BCE) y convertir CIF a FOB con un factor
(inventa un ajuste sobre datos oficiales y no corrige el desfase).

### 6. Recarga
- **Dimensiones:** upsert (`INSERT … ON DUPLICATE KEY UPDATE`), sin borrar.
- **Hechos:** reconstrucción completa (`DELETE` + `INSERT`) en una transacción, con controles
  que deshacen todo si fallan. Gold se deriva al 100 % de silver y es pequeño (~11 mil filas);
  reconstruir nunca deja filas obsoletas.

### 7. Ratios como medidas, no como columnas
FCR, supervivencia, lb/ha, costo por libra, USD/lb y margen se calculan en Power BI como
Σ numerador ÷ Σ denominador. `fact_production` guarda solo hechos aditivos (alimento, kg,
animales, larvas, área, costos, ingreso).

### 8. Conciliación como vista
`gold.v_reconciliation` muestra `silver.source_reconciliation` con nombres de gold, sin copiar
datos, y agrega `is_comparable` (0 si falta un lado o |brecha| > 1.000 %).

## Consecuencias

- **+** El total del dashboard coincide con la cifra oficial del BCE en todos los meses.
- **+** Cada cifra conserva su fuente; la página de conciliación usa las mismas tablas.
- **+** Las garantías están cubiertas por `tests/test_gold_data.py` (11 pruebas).
- **−** Las medidas de Power BI deben filtrar `is_preferred = 1` por defecto; sin el filtro se
  sumarían dos fuentes. Se documentará en la guía del dashboard.
- **−** En 7 meses "Sin reporte por país" es negativo y requiere una nota explicativa.
- **−** La finca simulada tiene FCR, supervivencia y costo por libra casi constantes entre años;
  el margen varía solo por precio (limitación documentada, mejora futura de la simulación).

## Cifras de control (2026-11-09)

| Año | Ecuador (X) | Espejo | Sin reporte | Total = BCE |
|---|---|---|---|---|
| 2018 | 2.944,7 | — | 245,1 | 3.189,7 |
| 2021 | 5.146,1 | — | 177,2 | 5.323,3 |
| 2023 | — | 6.513,2 | 692,0 | 7.205,2 |
| 2025 | — | 3.938,7 | 4.462,6 | 8.401,3 |

Millones de USD. Fuentes: UN Comtrade (X y espejo) y Banco Central del Ecuador (cuadro 3.1.2).