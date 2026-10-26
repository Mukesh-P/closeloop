"""Generate synthetic source-system data and bulk load it into Exasol.

Usage: python -m closeloop.load --rows 2000000
"""
import argparse
import time
from pathlib import Path

from closeloop import generate
from closeloop.db import connect

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def create_schema(conn):
    for stmt in (SQL_DIR / "schema.sql").read_text().split(";"):
        body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
        if body.strip():
            conn.execute(body)
    conn.execute((SQL_DIR / "normalize_name.sql").read_text())


def load(conn, rows: int, counterparties: int = 5000, verbose: bool = True):
    create_schema(conn)
    t = time.time()
    core, gl, truth = generate.generate_transactions(rows)
    loans, trades, limits, apps = generate.generate_exposure(counterparties)
    if verbose:
        print(f"generated in {time.time() - t:.1f}s: {len(core):,} core, {len(gl):,} GL, "
              f"{len(truth):,} planted breaks")
    for table, df in [("CORE_TXN", core), ("GL_ENTRY", gl), ("GROUND_TRUTH", truth),
                      ("LOAN_BOOK", loans), ("TREASURY", trades), ("RISK_LIMITS", limits),
                      ("APPLICATIONS", apps)]:
        t = time.time()
        conn.import_from_pandas(df, table)
        if verbose:
            print(f"loaded {table:<13} {len(df):>10,} rows in {time.time() - t:.1f}s")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=2_000_000, help="core banking transactions")
    p.add_argument("--counterparties", type=int, default=5000)
    args = p.parse_args()

    conn = connect()
    load(conn, args.rows, args.counterparties)
    conn.close()


if __name__ == "__main__":
    main()
