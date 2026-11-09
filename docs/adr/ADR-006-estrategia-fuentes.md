# ADR-006: Estrategia de fuentes — BCE para el total, Comtrade y espejo para el detalle

- **Estado:** Aceptada
- **Fecha:** 2026-11-07 (Fase 4)
- **Relacionada con:** ADR-005 (conciliación sin descartar fuentes)

## Contexto
El plan inicial usaba UN Comtrade como fuente de referencia para `gold.fact_exports`.
Al extraer los datos (2026-11-06/07) se encontró que Comtrade **no tiene publicados** de
Ecuador (reporter 218) los meses:
- Feb 2022 – Dic 2023 (23 meses)
- Abr 2024 (el mes existe, pero sin filas de los códigos HS de camarón)
- Ene – Dic 2025 (12 meses)
- Abr 2026 en adelante (aún no publicados)

Solo 63 de 104 meses (Ene 2018 – Ago 2026) tienen detalle por país reportado por Ecuador.
Restricción adicional: usar solo fuentes **gratuitas**.

## Fuentes evaluadas
| Fuente | Acceso | Total mensual | Por país | Peso | Cobertura 2018–2026 |
|---|---|---|---|---|---|
| BCE, cuadro 3.1.2 (BCEData) | Gratis, descarga manual de Excel | Sí | No | No | Completa (104 meses) |
| UN Comtrade, Ecuador reporta (flow X) | Gratis, API con llave | Sí | Sí | Sí | 63 meses |
| UN Comtrade espejo (flow M, importadores desde Ecuador) | Gratis, misma API | Sí | Sí | Sí | Todos los meses (57–74 países por año) |
| CNA | Reportes; sin API pública | Sí | Por mercado | Libras | No verificada |
| SENAE | De pago (solicitud y liquidación) | — | — | — | Descartada |

## Decisión
- **Total nacional oficial:** BCE 3.1.2, serie "Camarón" (FOB, miles de USD).
  Verificación: suma 2025 = USD 8.401,3 millones (coincide con la cifra oficial).
- **Detalle por país:** Comtrade flow X donde existe; **espejo** (flow M) en los meses faltantes,
  siempre marcado con su fuente (`source_name = 'UN Comtrade (espejo)'`).
- El espejo se extrae para **todos** los meses (no solo los huecos) para medir su sesgo frente
  a lo que Ecuador reporta en los meses con ambas fuentes.
- CNA queda como mejora futura.

## Evidencia
- Junio 2023 (mes sin dato de Ecuador): espejo USD 590,2 M (CIF) vs BCE USD 654,2 M (FOB) → −9,8 %.
  El espejo sirve para la **distribución** por país, no para reemplazar el total.
- Marzo 2026: Comtrade X USD 730,6 M vs BCE USD 744,0 M → 1,8 % (diferencia de cobertura de producto).

## Consecuencias
- (+) Total nacional sin huecos y detalle por país en todos los meses, con fuentes 100 % gratuitas.
- (+) Hallazgo de calidad documentado: las fuentes oficiales también tienen vacíos.
- (−) El espejo usa valoración CIF (incluye flete y seguro) y puede estar desfasado en el tiempo
  (fecha de llegada vs fecha de embarque). Nunca se mezcla con flow X sin indicar la fuente.
- (−) La distribución por país en los huecos es una **estimación**; la regla exacta
  (por ejemplo, total BCE × participación del espejo) se decide y documenta en las Fases 5 y 7.
- (−) El BCE requiere descarga manual (ver `docs/fuentes/bce_descarga_manual.md`).
## Adenda (2026-11-09): completitud del espejo

La conciliación a nivel total (silver.source_reconciliation) mostró que el espejo NO está
completo en todos los años, aunque tenga datos en todos los meses:

| Año | Espejo vs BCE | Comtrade X vs BCE |
|---|---|---|
| 2018 | −37,2 % | −7,7 % |
| 2019 | −12,9 % | −5,0 % |
| 2020 | −5,6 % | −4,5 % |
| 2021 | −1,7 % | −3,3 % |
| 2022 | −6,9 % | (1 mes) −2,5 % |
| 2023 | −9,6 % | sin dato |
| 2024 | −14,4 % | −1,1 % |
| 2025 | −53,1 % | sin dato |
| 2026 (ene–ago) | −62,4 % | (3 meses) −1,6 % |

Causa principal: **China no ha publicado en Comtrade sus importaciones de 2025 y 2026**
(en 2024 registró USD 3.089 M desde Ecuador). En 2018 China también aparece muy bajo
(USD 481 M vs 1.849 M en 2019); causa por verificar.

Decisión:
- El espejo SÍ se usa para el detalle por país en Feb 2022–Dic 2023 y Abr 2024.
- En 2025–2026 el detalle por país del espejo NO representa la distribución real: gold
  mostrará los países que reportaron y la diferencia con el BCE como
  "Sin reporte por país (incluye China)"; no se estima cuánto compró China.
- Regla en gold (Fase 7): mes con brecha de conciliación peor que −20 % → "detalle incompleto".
- Comtrade X es consistente con el BCE (brecha −1 % a −8 %, decreciente): diferencia de
  cobertura de producto.

Lección: verificar que haya datos no basta; hay que verificar que estén COMPLETOS,
y para eso hace falta una referencia independiente (el BCE).