# Reporte de calidad de datos — Shrimp Intelligence Ecuador

_Generado automáticamente por `src/quality_report.py` el 2026-11-09 16:28. No editar a mano: volver a ejecutar el script después de cada carga._

Datos **reales**: UN Comtrade (Ecuador y espejo) y Banco Central del Ecuador. Datos **SIMULADOS**: finca "Acuícola Del Golfo S.A." (semilla fija 42).

## 1. Antes vs. después (bronze → silver)

Bronze guarda todo tal como llegó (incluye versiones antiguas y filas que no son datos, como el total 'World'). Silver guarda solo filas limpias y únicas.

| dato | bronze | silver | diferencia | por qué cambia |
|---|---|---|---|---|
| Exportaciones de Ecuador (Comtrade X) | 3.103 | 2.914 | -189 | Se excluye la fila 'World' (total, no es un país) y versiones antiguas. |
| Importaciones desde Ecuador (espejo) | 7.289 | 7.288 | -1 | Se excluyen valores vacíos o en cero y versiones antiguas. |
| BCE serie 'Camarón' (cuadro 3.1.2) | 104 | 208 | 104 | No va a export_monthly (no tiene país): es la referencia nacional. Cada mes se compara con 2 fuentes → 2 filas por mes. |
| Finca SIMULADA: ciclos | 525 | 510 | -15 | Se excluyen duplicados exactos. Cada ciclo genera además 1 cosecha, 1 venta y 4 costos. |

**Filas por tabla de silver:**

| tabla silver | filas |
|---|---|
| `regions` | 6 |
| `countries` | 110 |
| `country_aliases` | 312 |
| `products` | 3 |
| `sizes` | 8 |
| `data_sources` | 5 |
| `export_monthly` | 10.202 |
| `source_reconciliation` | 3.890 |
| `farms` | 3 |
| `ponds` | 45 |
| `production_cycles` | 510 |
| `harvests` | 510 |
| `cycle_costs` | 2.040 |
| `sales` | 510 |

## 2. Reglas de limpieza aplicadas

Solo se listan las reglas que afectaron al menos una fila. El registro completo (incluidas las reglas con 0 filas) está en `data/processed/quality_*.csv`.

### Exportaciones (Comtrade X y espejo) → `export_monthly`

| regla | filas | acción | detalle |
|---|---|---|---|
| excluir_total_world | 189 | excluidas | partner_code 0 en flow X |
| valor_invalido | 1 | excluidas | primary_value vacío, cero o negativo (en centavos) |
| peso_vacio | 1 | conservadas con NULL | sin peso: cuentan en USD, no en USD/lb |

### Finca SIMULADA → `farms`, `ponds`, `production_cycles`, `harvests`, `cycle_costs`, `sales`

| regla | filas | acción | detalle |
|---|---|---|---|
| area_coma_decimal | 53 | corregidas | '9,95' → 9.95 |
| duplicado_exacto | 15 | excluidas | filas idénticas en todo su contenido |
| fecha_formato_stocking | 128 | convertidas | formato distinto de AAAA-MM-DD |
| fecha_formato_harvest | 128 | convertidas | formato distinto de AAAA-MM-DD |
| unidad_kg | 76 | se conservan en kg | las demás: lb ÷ 2,20462 |
| talla_formato | 153 | normalizadas | '20-30' / '20 30' → '20/30' |
| coma_decimal_avg_weight_g | 46 | corregidas | ',' → '.' |
| larvas_imputadas_por_cosecha | 10 | imputadas | densidad nula: piezas cosechadas ÷ supervivencia |
| peso_imputado_por_biomasa | 9 | imputados | kg ÷ (larvas × supervivencia) |
| fcr_reportado_inconsistente | 5 | solo reporte (no se guarda FCR) | GY-P10-C05, GY-P02-C01, GY-P15-C04, GY-P02-C12, GY-P05-C10 |
| costo_imputado_energy | 10 | imputados | mediana USD/ha-día de la finca × área × días |
| coma_decimal_precio | 50 | corregidas | ',' → '.' |
| pais_variante_resuelta | 65 | resueltas por alias | CHINA, China , EEUU, Estados Unidos de América, U.S.A., USA, china, usa  |
| pais_nulo | 9 | a NULL | destino desconocido (columna opcional) |

### Conciliación de fuentes → `source_reconciliation`

| regla | filas | acción | detalle |
|---|---|---|---|
| sin_referencia_bce | 1 | excluidas | meses de Comtrade aún no publicados por el BCE |
| sin_dato_un_comtrade | 41 | conservadas con NULL | mes del BCE sin dato en la fuente comparada |
| espejo_mes_sin_x | 2.161 | excluidas | 42 meses sin dato de Ecuador (ya documentados en el nivel 'total') |
| pais_sin_espejo | 680 | conservadas con NULL | el importador no reportó el mes |
| pais_sin_x | 1.180 | conservadas con NULL | Ecuador no detalló el país en el mes |

## 3. Imputaciones y verificación (finca SIMULADA)

Política (decisión del 2026-11-09): un dato obligatorio nulo se **imputa y se marca**, en vez de descartar el ciclo. Como la finca es simulada, existe la verdad sin errores (`farm_clean_truth.csv`) y se puede medir el error de cada imputación.

**Qué se imputó y cómo:**

| regla | filas | detalle |
|---|---|---|
| larvas_imputadas_por_cosecha | 10 | densidad nula: piezas cosechadas ÷ supervivencia |
| peso_imputado_por_biomasa | 9 | kg ÷ (larvas × supervivencia) |
| costo_imputado_energy | 10 | mediana USD/ha-día de la finca × área × días |

**Verificación contra la verdad de la simulación:**

| control | resultado | nota |
|---|---|---|
| ciclos en silver | 510/510 | deben ser 510 |
| fecha de siembra exacta | 510/510 |  |
| fecha de cosecha exacta | 510/510 |  |
| kg cosechados (±0,5 %) | 510/510 |  |
| larvas medidas (±0,1 %) | 500/500 |  |
| larvas imputadas: error medio | 0.2 % | 10 imputadas |
| peso medido exacto | 501/501 |  |
| peso imputado: error medio | 0.2 % | 9 imputados |
| alimento exacto | 510/510 |  |
| costos en silver | 2040/2040 | 4 por ciclo |
| costos medidos exactos | 2030/2030 |  |
| costos imputados: error medio | 0.0 % | 10 imputados |
| ventas en silver | 510/510 |  |
| precio exacto | 510/510 |  |
| país correcto (destino conocido) | 501/501 | 9 con destino nulo en bronze |

> Nota: el error de 0,0 % en costos imputados se debe a que la simulación calcula la energía de forma proporcional a hectáreas × días. Con datos reales el error sería mayor; el método es correcto, la precisión depende de la regularidad del dato.

## 4. Cobertura de fuentes y huecos conocidos

**Detalle por país en `export_monthly`:**

| fuente | filas | paises | meses | desde | hasta | usd_millones |
|---|---|---|---|---|---|---|
| COMTRADE_MIRROR | 7.288 | 92 | 105 | 2018-01-01 | 2026-09-01 | 39.931,8 |
| UN_COMTRADE | 2.914 | 85 | 63 | 2018-01-01 | 2026-03-01 | 24.344,6 |

**Meses del BCE sin dato en la fuente de detalle** (nivel total):

| fuente | anio | meses_sin_dato | meses_del_bce |
|---|---|---|---|
| UN_COMTRADE | 2022 | 11 | 12 |
| UN_COMTRADE | 2023 | 12 | 12 |
| UN_COMTRADE | 2024 | 1 | 12 |
| UN_COMTRADE | 2025 | 12 | 12 |
| UN_COMTRADE | 2026 | 5 | 8 |

**Destinos principales con meses sin espejo** (el importador no reportó, en meses en que Ecuador sí publicó):

| iso3 | pais | meses_sin_espejo | meses_con_dato_de_ecuador |
|---|---|---|---|
| CHN | China | 3 | 63 |
| VNM | Vietnam | 23 | 60 |
| FRA | Francia | 3 | 63 |
| RUS | Rusia | 15 | 63 |
| KOR | Corea del Sur | 3 | 63 |
| COL | Colombia | 3 | 63 |
| GTM | Guatemala | 14 | 61 |
| ARE | Emiratos Árabes Unidos | 43 | 45 |

Interpretación y estrategia de fuentes: ver `docs/adr/` (ADR-006).

## 5. Pruebas automáticas

| Archivo | Tipo | Qué protege |
|---|---|---|
| `tests/test_cleaning_rules.py` | Unitarias (sin MySQL) | Funciones de limpieza: fechas en 4 formatos, decimales con coma, formato numérico del BCE, meses en español, tallas, alias de países |
| `tests/test_silver_data.py` | Datos (`db`) | Llaves únicas, sin huérfanos, FCR 0,8–3, supervivencia 0–100 %, peso ≤ 60 g, montos positivos, 4 costos por ciclo, libras vendidas = cosechadas, conciliación coherente |
| `tests/test_db.py` | Integración (`db`) | Conexión, parámetros enlazados, rollback, idempotencia de `upsert`, bronze protegido |
| `tests/test_sql_scripts.py` | Esquema (`db`) | Los scripts `sql/*.sql` coinciden con las tablas reales (schema drift) |

```powershell
python -m pytest -q                # todo
python -m pytest -m "not db" -q    # sin MySQL
python -m pytest -m db -v          # solo las que consultan la base
```
