# Descarga manual: BCE — Exportaciones FOB por Grupos de Productos (cuadro 3.1.2)

**Por qué es manual:** el Banco Central del Ecuador (BCE) no ofrece una API pública para este
cuadro. La descarga se hace desde su portal de series estadísticas (BCEData) y el archivo
se carga con `src/extract_bce.py`.

## Qué aporta al proyecto
- Serie **"Camarón"**: total nacional de exportaciones de camarón, **FOB, miles de USD, mensual**.
- Es la fuente oficial del total nacional (ver `docs/adr/ADR-006-estrategia-fuentes.md`).
- No incluye países de destino ni toneladas (eso viene de UN Comtrade).
- El archivo trae 36 series (petroleras, no petroleras, tradicionales, etc.); se cargan todas a
  bronze para poder calcular la participación del camarón en las exportaciones totales.

## Pasos
1. Entrar a BCEData: 👉 COMPLETAR (URL exacta del portal)
2. Ir a **3.1 Comercio Exterior de Bienes → 3.1.2 Exportaciones FOB por Grupos de Productos**.
3. Elegir **Frecuencia: Mensual** y el rango desde **Ene 2018** hasta el último mes disponible.
4. Descargar en **Excel (.xlsx)**.
5. Guardar el archivo **sin renombrarlo** en `data/raw/bce/`.
   El nombre trae la fecha y hora de consulta: `BCEData-3.1.2-AAAAMMDDHHMM.xlsx`.
6. Cargar a bronze:
   ```powershell
   python -m src.extract_bce
   ```

## Qué esperar
- Log: `BCE 3.1.2: 36 series × N meses (Ene 2018 – <último mes>)`.
- Verificación automática: `Camarón 2025 ... USD 8,401.3 millones (oficial BCE: 8,401.3)`.
- Si el archivo no cambió desde la última carga: `no hay filas nuevas que cargar` (idempotente).
- Si el BCE **revisó** cifras antiguas (sus datos son provisionales), esas filas entran como
  versión nueva; bronze conserva el historial y silver usa la más reciente.

## Formato del archivo (para mantenimiento)
| Elemento | Ubicación / formato |
|---|---|
| Título del cuadro | primera celda que empieza con `3.1.2 ...` |
| Unidad | fila `Unidad del cuadro: Miles de USD` |
| Fecha de consulta | fila `Fecha de consulta: DD/MM/AAAA HH:MM (UTC-05:00)` → `extracted_at` |
| Encabezados | fila cuya primera celda es `Serie`; columnas `Ene 2018`, `Feb 2018`, ... |
| Valores | texto con formato ecuatoriano: `596.248,67089` (punto de miles, coma decimal) |
| Grupos | filas sin números (ej. `Tradicionales`) → columna `series_group` |

Si el BCE cambia este formato, `extract_bce.py` falla con un mensaje claro
(por ejemplo, "No se encontró la fila 'Serie'") en lugar de cargar datos incorrectos.

## Última descarga registrada
| Fecha de consulta | Archivo | Meses | Filas en bronze |
|---|---|---|---|
| 06/11/2026 11:34 | `BCEData-3.1.2-202611061134.xlsx` | Ene 2018 – Ago 2026 (104) | 3.744 |