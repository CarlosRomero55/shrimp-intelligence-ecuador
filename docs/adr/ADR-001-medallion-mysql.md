# ADR-001: Arquitectura Medallion implementada en MySQL

- **Estado:** Aceptada
- **Fecha:** 2026-11-05 (Fase 0)

## Contexto
El proyecto combina fuentes oficiales que no coinciden entre sí (UN Comtrade, BCE, CNA) con
datos simulados de una finca que traen errores de calidad a propósito. Se necesita:
conservar cada fuente tal como llegó (para auditar diferencias), una versión limpia y única
de la verdad, y un modelo fácil de consumir para Power BI y la API. Todo debe correr en una
PC personal y sin costo.

## Decisión
Tres capas como esquemas de MySQL 8:
- **bronze:** copia fiel de cada fuente, una tabla por fuente, todo como texto, con columnas
  de auditoría (`source_name`, `batch_id`, `extracted_at`, `record_hash`). Inmutable: el
  usuario del ETL solo tiene SELECT e INSERT.
- **silver:** datos limpios, tipados y normalizados (3FN, ver ADR-002).
- **gold:** modelo estrella para consumo (ver ADR-002). Lo único que leen Power BI y la API.

Más un esquema **ops** para la bitácora de ejecuciones (`ops.etl_runs`).

## Alternativas consideradas
| Alternativa | Por qué no |
|---|---|
| Base única sin capas | Se pierde el dato original al limpiar; no se pueden auditar las diferencias entre fuentes. |
| Inmon (almacén corporativo normalizado) | Excesivo para un solo dominio; Medallion ya incluye una capa normalizada (silver). |
| Kimball puro (solo estrella) | Sin capa cruda ni capa limpia intermedia, la limpieza queda mezclada con el modelado. |
| Lakehouse (Microsoft Fabric, Databricks) | Requiere nube y licencias; el diseño es migrable (bronze/silver/gold → Lakehouse + tablas Delta). |
| Lambda / Kappa (streaming) | Las fuentes publican mensualmente: no hay necesidad de tiempo real. |
| ELT dentro de la base | Las reglas de limpieza (fechas, tallas, unidades) son más legibles y probables en Python. |

## Consecuencias
- (+) Cada cifra del dashboard se puede rastrear hasta la fila original en bronze.
- (+) Volver a ejecutar el pipeline no duplica datos (huella + upsert idempotente).
- (+) Migrable a Fabric: los tres esquemas corresponden a las tres capas de un Lakehouse.
- (−) Bronze crece con cada revisión de una fuente (se conserva el historial); silver debe
  elegir siempre la versión más reciente.
- (−) Más tablas que mantener; se mitiga con la prueba `tests/test_sql_scripts.py`.