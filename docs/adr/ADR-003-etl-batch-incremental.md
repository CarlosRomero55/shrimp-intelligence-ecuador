# ADR-003 — ETL por lotes, extracción incremental y orquestación en Python

- **Estado:** aceptada
- **Fecha:** 2026-11-09
- **Fase:** 8 (orquestación)
- **Relacionadas:** ADR-001 (Medallion en MySQL), ADR-006 (fuentes), ADR-007 (gold)

## Contexto

Hasta la Fase 7 cada capa se construía con un script distinto (`clean_exports`, `clean_farm`,
`reconcile`, `build_gold`, `quality_report`) que había que correr a mano y en el orden correcto.
Faltaba:

1. Un solo comando que corra todo en orden.
2. Un registro de cada ejecución (qué corrió, cuánto tardó, cuántas filas, qué falló).
3. Traer datos nuevos de UN Comtrade sin volver a descargar 9 años en cada corrida.

Las fuentes publican **mensualmente** y con atraso (Ecuador y los países importadores
corrigen cifras recientes). No hay necesidad de tiempo real.

## Decisiones

### 1. ETL por lotes (batch), no streaming
Los datos cambian una vez al mes. Un lote diario o semanal es suficiente; streaming
(Kafka, Lambda/Kappa) agregaría infraestructura sin beneficio.

### 2. Orquestación en Python puro (`src/pipeline.py`)
Una lista de etapas en orden, cada una envuelta en `track_step()`:

| # | Etapa | Depende de |
|---|---|---|
| 1 | `extract.comtrade` (solo con `--extract`) | API de UN Comtrade |
| 2 | `silver.export_monthly` | bronze |
| 3 | `silver.farm` | bronze |
| 4 | `silver.reconciliation` | 2 |
| 5 | `gold.build` | 2, 3, 4 |
| 6 | `docs.quality_report` | todo lo anterior |
| 7 | `quality_gate.pytest` | todo lo anterior |

**Alternativas descartadas:** Airflow y Prefect. Son el estándar para muchos flujos con
dependencias complejas, reintentos por tarea y varios servidores; para 7 etapas lineales
en una PC requieren instalar y operar un servicio adicional.
**Camino de migración:** cada etapa ya es una función con entrada y salida claras; pasar a
Prefect es envolverlas con `@task` y el orden con `@flow`; a Airflow, una tarea
`PythonOperator` por etapa. En Microsoft Fabric, un *Data Pipeline* con actividades de
notebook por etapa.

### 3. Fallar rápido + puerta de calidad
- Si una etapa falla, las siguientes **no corren** (no se construye gold sobre un silver a medias).
- La última etapa corre `pytest -m db` (52 pruebas sobre los datos). Si alguna falla, la
  ejecución queda como fallida aunque las cargas hayan terminado.
- Código de salida del proceso: 0 = OK, 1 = falló (sirve para programarlo y alertar).

### 4. Bitácora en `ops.etl_runs`
Una fila por etapa, con `batch_id` común a toda la ejecución: estado
(`running` → `success` | `skipped` | `failed`), inicio, fin, `duration_sec` (columna generada),
filas leídas / cargadas / excluidas y mensaje de error.
El registro se escribe en **su propia transacción**: si la etapa hace ROLLBACK, el registro
del fallo igual queda guardado.

### 5. Extracción incremental (`--extract`)
`extract_comtrade.extract_incremental()` pide a la API solo:

| Flujo | Ventana | Por qué |
|---|---|---|
| Exportaciones de Ecuador (X) | últimos **6** meses publicados + meses publicados que faltan en bronze | Ecuador corrige cifras recientes |
| Espejo (M) | últimos **12** meses cerrados + meses sin ninguna fila | cada país reporta con su propio atraso |

- Unas 4 llamadas por corrida, frente a ~18 de la carga completa.
- Idempotente: una fila con la misma huella (`record_hash`) no se inserta otra vez; una cifra
  corregida entra como versión nueva y silver toma la más reciente (`ROW_NUMBER`).
- Es **opcional y explícita** porque usa la llave personal de la API y su cuota diaria.
- El BCE sigue siendo descarga manual (`docs/fuentes/bce_descarga_manual.md`).

### 6. Recarga por capa
| Capa | Estrategia |
|---|---|
| bronze | solo INSERT (inmutable), deduplicado por huella |
| silver | upsert por llave de negocio |
| gold — dimensiones | upsert |
| gold — hechos | reconstrucción completa en una transacción (ADR-007) |

## Consecuencias

- **+** Un comando reconstruye y verifica todo en ~10 s (≈ 35 s con `--extract`).
- **+** Cada ejecución queda auditada y es reproducible (mismo resultado si los datos no cambian).
- **+** Sin dependencias nuevas.
- **−** No hay reintento automático por etapa ni panel de monitoreo (lo darían Prefect/Airflow).
- **−** La programación (diaria/semanal) depende del sistema operativo (Programador de tareas de
  Windows) o de un servicio externo.
- **−** Si una fuente cambia su formato, la etapa falla y requiere intervención (es lo deseado:
  fallar rápido en vez de cargar datos dudosos).

## Evidencia (2026-11-09)

```
etapa                  estado   segundos  leidas  cargadas  excluidas
extract.comtrade       success    21.426      16         0
silver.export_monthly  success     3.734   10392     10202        190
silver.farm            success     1.518     525       510         15
silver.reconciliation  success     2.198              3890       2162
gold.build             success     1.443             10816
docs.quality_report    success     0.237
quality_gate.pytest    success     5.550      52        52          0
```