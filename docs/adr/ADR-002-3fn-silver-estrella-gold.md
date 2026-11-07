# ADR-002: Silver normalizado (3FN) y gold en esquema estrella

- **Estado:** Aceptada
- **Fecha:** 2026-11-05 (Fase 2)

## Contexto
Silver debe ser la única versión de la verdad: sin duplicados ni contradicciones (por ejemplo,
el área de una piscina escrita distinta en dos ciclos). Gold debe ser rápido y simple para
Power BI y para consultas de negocio.

## Decisión
**Silver en tercera forma normal (3FN):**
- 14 tablas (regiones, países, alias de países, productos, tallas, fuentes, exportaciones
  mensuales, conciliación, fincas, piscinas, ciclos, costos, cosechas, ventas).
- Llaves sustitutas (`*_id`) y llaves de negocio con UNIQUE (por ejemplo `m49_region_code`);
  el upsert identifica las filas por la llave de negocio.
- Solo **hechos medidos**: FCR, supervivencia, densidad y lb/ha NO se guardan; se calculan en gold.
- Restricciones CHECK (rangos y formatos) para que un dato inválido no entre.

**Gold en esquema estrella (Kimball):**
- Dos procesos de negocio con **grano** distinto, en tablas de hechos separadas:
  `fact_exports` (país–producto–mes) y `fact_production` (ciclo de cultivo por piscina).
- Dimensiones: `dim_date` (2018–2030, 4.748 días, rol doble siembra/cosecha), `dim_country`,
  `dim_product`, `dim_size`, `dim_pond`, `dim_source`; cada una con miembro 0 = "Desconocido".
- Llaves de gold = IDs de silver (deterministas y estables entre recargas).
- Hechos solo **aditivos** (USD, kg, libras, costos); los ratios (USD/lb, FCR, margen) son
  medidas calculadas en DAX o en vistas.

## Alternativas consideradas
- **Silver ya desnormalizado:** más simple, pero repite datos y permite contradicciones.
- **Un solo hecho para exportaciones y producción:** mezcla granos distintos y produce sumas falsas.
- **Guardar ratios en los hechos:** un promedio de promedios es incorrecto al agregar
  (por ejemplo, el USD/lb anual no es el promedio de los USD/lb mensuales).

## Consecuencias
- (+) Las reglas de calidad viven en un solo lugar (silver).
- (+) Power BI trabaja con pocas relaciones y medidas correctas al agregar.
- (−) Transformación adicional silver → gold (Fase 7).