# ADR-004 — FastAPI como capa de servicio sobre gold

- **Estado:** Aceptado
- **Fecha:** 2026-11-10
- **Fase:** 11 (API REST)

## Contexto

Los datos limpios y conciliados viven en la capa gold de MySQL. Hasta la Fase 10 solo los consumían el propio proyecto (consultas de `sql/queries/` y el notebook del EDA). Otros consumidores posibles necesitan los mismos números: un dashboard, otra aplicación, un analista con Python o Excel.

Darles acceso directo a MySQL implica:

- compartir credenciales de la base;
- que cada consumidor reescriba la lógica de negocio: qué cifra es la preferida, cómo se calcula el USD/lb (Σ valor ÷ Σ libras), cuándo un año es comparable. Cada versión puede terminar en un número distinto;
- acoplar a todos los consumidores al modelo físico: un cambio de tabla rompe a todos.

## Decisión

Exponer gold mediante una **API REST de solo lectura con FastAPI** (`src/api/`), con estas reglas:

1. **Una vista por endpoint** (`sql/04_api_views.sql`: `gold.api_*`). La lógica de negocio vive en SQL, reutilizando las consultas validadas de la Fase 9. La API solo hace `SELECT … WHERE` con los filtros del usuario.
2. **Usuario de solo lectura `api_reader`**, con `SELECT` únicamente sobre las 6 vistas (mínimo privilegio). Las vistas leen las tablas con los permisos de su creador (`SQL SECURITY DEFINER`).
3. **Parámetros enlazados y lista blanca:** los valores del usuario nunca se concatenan en el SQL; los nombres de vista y columnas salen del código.
4. **Validación en la entrada** con Pydantic (tipos, rangos, formato ISO3). Un parámetro inválido responde 422 sin consultar la base.
5. **Contrato documentado automáticamente** (OpenAPI en `/docs`) con modelos de respuesta por endpoint.
6. **Errores controlados:** 404 si un filtro no tiene datos; 503 si la base no responde, sin exponer detalles internos.

Endpoints: `/health`, `/kpis`, `/exports`, `/prices`, `/concentration`, `/ponds/ranking`, `/reconciliation`.

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Acceso directo a MySQL para cada consumidor | Comparte credenciales, duplica la lógica de negocio y acopla a todos al esquema físico. |
| Publicar CSV (`data/processed/queries/`) | Simple, pero sin filtros y desactualizado hasta volver a correr `run_queries`; no sirve para aplicaciones. |
| Flask | Funciona, pero hay que añadir aparte la validación y la documentación OpenAPI, que FastAPI trae integradas con los type hints. |
| Lógica de negocio en Python (pandas) dentro de la API | Duplicaría lo que ya está validado en SQL y haría que la API, Power BI y el notebook pudieran dar cifras distintas. |
| Endpoints que ejecutan los archivos `sql/queries/q*.sql` | Esas consultas no aceptan filtros (país, año); las vistas sí, con `WHERE` y parámetros. |

## Consecuencias

**Positivas**

- Un solo lugar para cada cálculo (las vistas). La API, Power BI y cualquier cliente obtienen el mismo número.
- Seguridad por diseño: aunque el código de la API tuviera un error, `api_reader` no puede escribir ni leer otras tablas. Lo prueban `tests/test_api.py` con el error 1142 de MySQL.
- Contrato estable: si cambian las tablas de gold, se ajusta la vista y los consumidores no se enteran.
- 26 pruebas: 20 unitarias con SQLite en memoria y 6 de datos contra MySQL, que verifican que `/kpis` = BCE en todos los años.

**Negativas / pendientes**

- Las vistas repiten la lógica de algunas consultas de `sql/queries/`; un cambio de regla debe aplicarse en ambos lugares. Mitigación futura: que las consultas lean de las vistas.
- Las vistas se calculan en cada petición (sin caché). Con el volumen actual (~10 mil filas) responden al instante; con más datos se podría usar caché o tablas materializadas.
- Uso local, sin autenticación. Para publicarla haría falta HTTPS, API key u OAuth2, límite de peticiones y CORS.
- Crear o cambiar las vistas requiere root (como `v_reconciliation` en la Fase 7).
