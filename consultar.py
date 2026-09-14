"""Ejecuta las consultas de queries.sql contra el modelo persistido."""

from pathlib import Path

import duckdb

BASE = Path(__file__).parent
con = duckdb.connect(str(BASE / "outputs" / "cafenorte.duckdb"))

for consulta in (BASE / "queries.sql").read_text(encoding="utf-8").split(";"):
    if not consulta.strip():
        continue
    print(f"\n{'=' * 70}\n{consulta.strip().splitlines()[0]}\n{'=' * 70}")
    print(con.execute(consulta).df().head(12).to_string(index=False))

con.close()
