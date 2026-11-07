
import re

import pytest

from src.config import ROOT_DIR
from src.db import read_sql

SQL_DIR = ROOT_DIR / "sql"
SCHEMAS = ("ops", "bronze", "silver", "gold")

# CREATE TABLE [IF NOT EXISTS] esquema.tabla (   — con o sin comillas invertidas
CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?\s*\.\s*`?(\w+)`?\s*\(",
    re.IGNORECASE,
)
# Líneas dentro del CREATE TABLE que NO son columnas
NOT_COLUMN = ("PRIMARY", "UNIQUE", "KEY", "INDEX", "CONSTRAINT",
              "CHECK", "FOREIGN", "FULLTEXT", "SPATIAL")


def _strip_comments(sql: str) -> str:
    """Quita comentarios /* ... */, -- ... y # ... (para no confundirlos con columnas)."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"(--|#)[^\n]*", " ", sql)


def _split_top_level(body: str) -> list[str]:
    """Divide el cuerpo del CREATE TABLE por comas que NO están dentro de paréntesis
    (así 'DECIMAL(14,2)' o 'CHECK (a, b)' no se parten)."""
    parts, depth, current = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def parse_tables(sql: str) -> dict[str, set[str]]:
    """{'esquema.tabla': {columnas}} de todos los CREATE TABLE de un script."""
    sql = _strip_comments(sql)
    tables = {}
    for match in CREATE_RE.finditer(sql):
        # Buscar el paréntesis que cierra el CREATE TABLE
        depth, i = 1, match.end()
        while depth and i < len(sql):
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        body = sql[match.end(): i - 1]

        columns = set()
        for part in _split_top_level(body):
            tokens = part.strip().split()
            if tokens and tokens[0].upper() not in NOT_COLUMN:
                columns.add(tokens[0].strip("`").lower())
        tables[f"{match.group(1).lower()}.{match.group(2).lower()}"] = columns
    return tables


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def script_tables() -> dict[str, set[str]]:
    """Tablas y columnas definidas en todos los sql/*.sql del repositorio."""
    files = sorted(SQL_DIR.glob("*.sql"))
    assert files, f"No hay scripts .sql en {SQL_DIR}"
    tables: dict[str, set[str]] = {}
    for path in files:
        tables.update(parse_tables(path.read_text(encoding="utf-8")))
    return tables


@pytest.fixture(scope="module")
def db_tables() -> dict[str, set[str]]:
    """Tablas REALES (no vistas) y sus columnas, según MySQL."""
    df = read_sql(
        """
        SELECT c.table_schema, c.table_name, c.column_name
        FROM information_schema.columns AS c
        JOIN information_schema.tables  AS t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema IN ('ops', 'bronze', 'silver', 'gold')
          AND t.table_type = 'BASE TABLE'
        """
    )
    df.columns = [c.lower() for c in df.columns]
    tables: dict[str, set[str]] = {}
    for row in df.itertuples(index=False):
        key = f"{row.table_schema}.{row.table_name}".lower()
        tables.setdefault(key, set()).add(row.column_name.lower())
    return tables


# ---------------------------------------------------------------- pruebas
def test_every_db_table_has_a_script(db_tables, script_tables):
    """Toda tabla de la base debe estar definida en algún sql/*.sql."""
    missing = sorted(set(db_tables) - set(script_tables))
    assert not missing, f"Tablas en la base SIN script en el repo: {missing}"


def test_every_script_table_exists_in_db(db_tables, script_tables):
    """Todo CREATE TABLE del repo debe existir en la base (de los 4 esquemas)."""
    in_scope = {t for t in script_tables if t.split(".")[0] in SCHEMAS}
    extra = sorted(in_scope - set(db_tables))
    assert not extra, f"Tablas en los scripts que NO existen en la base: {extra}"


def test_columns_match(db_tables, script_tables):
    """Para cada tabla presente en ambos lados, las columnas deben ser las mismas."""
    differences = []
    for table in sorted(set(db_tables) & set(script_tables)):
        only_db = db_tables[table] - script_tables[table]
        only_script = script_tables[table] - db_tables[table]
        if only_db or only_script:
            differences.append(
                f"{table}: solo en la base={sorted(only_db)} | "
                f"solo en el script={sorted(only_script)}"
            )
    assert not differences, "Columnas distintas:\n" + "\n".join(differences)