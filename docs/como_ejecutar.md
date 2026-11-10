# Cómo ejecutar Shrimp Intelligence Ecuador

Guía de operación del pipeline (Windows + PowerShell). Decisiones de diseño: `docs/adr/ADR-003-etl-batch-incremental.md`.

## 1. Preparar el entorno (una sola vez por sesión de PowerShell)

```powershell
cd D:\shrimp-intelligence-ecuador
.\.venv\Scripts\Activate.ps1
```

Requisitos ya configurados en la instalación:

- MySQL 8 encendido, con los esquemas `ops`, `bronze`, `silver` y `gold` (`sql/00_ops.sql` … `sql/03_gold.sql`).
- Archivo `.env` con la conexión y la llave de UN Comtrade (ver `.env.example`; nunca se sube a GitHub).
- Paquetes: `pip install -r requirements.txt`.

## 2. Correr el pipeline

| Comando | Qué hace | ¿Llama a la API? |
|---|---|---|
| `python -m src.pipeline` | silver → conciliación → gold → reporte de calidad → pruebas | No |
| `python -m src.pipeline --extract` | igual, pero primero trae de UN Comtrade los meses recientes y faltantes | **Sí** (~4 llamadas con tu llave) |
| `python -m src.pipeline --skip-tests` | sin la puerta de calidad (más rápido, para desarrollo) | No |
| `python -m src.pipeline --triggered-by scheduler` | marca la ejecución como programada en la bitácora | Según opciones |

Resultado esperado al final:

```
Resultado: OK en 9.5 s (código de salida 0)
```

Para ver el código de salida en PowerShell: `echo $LASTEXITCODE` (0 = OK, 1 = falló).

## 3. Datos del BCE (manual)

El cuadro 3.1.2 del Banco Central se descarga a mano y se carga con `python -m src.extract_bce`.
Pasos detallados en `docs/fuentes/bce_descarga_manual.md`. Después, correr el pipeline.

## 4. Revisar ejecuciones en `ops.etl_runs`

Última ejecución, etapa por etapa:

```sql
SELECT step_name, status, duration_sec, rows_read, rows_inserted, rows_rejected, error_message
FROM ops.etl_runs
WHERE batch_id = (SELECT batch_id FROM ops.etl_runs ORDER BY run_id DESC LIMIT 1)
ORDER BY run_id;
```

Historial de ejecuciones:

```sql
SELECT batch_id, MIN(started_at) AS inicio, SUM(duration_sec) AS segundos,
       SUM(status = 'failed') AS etapas_fallidas, MAX(triggered_by) AS origen
FROM ops.etl_runs
GROUP BY batch_id
ORDER BY inicio DESC
LIMIT 10;
```

## 5. Si una etapa falla

1. El pipeline se detiene en esa etapa; las siguientes no corren (gold y el reporte quedan como estaban).
2. El mensaje está en `ops.etl_runs.error_message`; la traza completa sale en la consola (y en el archivo de log, si `src/logging_config.py` lo tiene configurado).
3. Casos comunes:

| Etapa | Causa probable | Qué hacer |
|---|---|---|
| `extract.comtrade` | cuota diaria de la API agotada o llave vencida | esperar al día siguiente / revisar `.env`; correr sin `--extract` |
| `silver.*` | cambió el formato de una fuente | revisar la regla indicada en el error y `data/processed/quality_*.csv` |
| `gold.build` | un control de cuadre no pasó (ej. no cuadra con el BCE) | revisar silver: el mensaje indica el mes o la tabla |
| `quality_gate.pytest` | una prueba de datos falló | correr `python -m pytest -m db -v` para ver cuál y por qué |

4. Corregir y volver a correr: el pipeline es idempotente (correrlo otra vez no duplica nada).

## 6. Pruebas

```powershell
python -m pytest -q                # todas (115)
python -m pytest -m "not db" -q    # sin MySQL (63 unitarias)
python -m pytest -m db -v          # solo las que consultan la base
```

## 7. Programarlo (opcional): Programador de tareas de Windows

1. Abrir **Programador de tareas** → **Crear tarea básica**.
2. Desencadenador: **Semanalmente** (los datos de Comtrade cambian una vez al mes).
3. Acción: **Iniciar un programa**
   - Programa: `D:\shrimp-intelligence-ecuador\.venv\Scripts\python.exe`
   - Argumentos: `-m src.pipeline --extract --triggered-by scheduler`
   - Iniciar en: `D:\shrimp-intelligence-ecuador`
4. MySQL debe estar encendido a esa hora. El resultado queda en `ops.etl_runs`.