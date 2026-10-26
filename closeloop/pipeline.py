"""The full month-end close: reconciliation, exposure and credit decisions.

Usage: python -m closeloop.pipeline
"""
import time

from closeloop.db import query_df

from closeloop import credit, exposure, recon


def run_close(conn, on_step=None):
    """Runs every step inside Exasol, logs step timings to CLOSE_RUN_STEPS. Returns total seconds."""
    steps = []

    def step(label, secs, rows):
        steps.append((label, round(secs, 3), rows))
        if on_step:
            on_step(label, secs, rows)

    t = time.time()
    recon.run(conn, step)
    for label, fn in [("Resolve counterparties across 3 systems (Python UDF) and aggregate exposure", exposure.run),
                      ("Decide credit applications on true exposure", credit.run)]:
        step(label, fn(conn), 0)
    total = time.time() - t

    conn.execute("CREATE OR REPLACE TABLE CLOSE_RUN_STEPS "
                 "(STEP_NO DECIMAL(3,0), STEP VARCHAR(200), SECONDS DECIMAL(9,3), ROWS_AFFECTED DECIMAL(12,0))")
    conn.ext.insert_multi("CLOSE_RUN_STEPS", [(i + 1, *s) for i, s in enumerate(steps)])
    return total


def last_run(conn):
    """Step timings of the most recent close, or None if no close has run."""
    if conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = CURRENT_SCHEMA "
                    "AND TABLE_NAME = 'CLOSE_RUN_STEPS'").fetchval() == 0:
        return None
    return query_df(conn, "SELECT STEP, SECONDS, ROWS_AFFECTED FROM CLOSE_RUN_STEPS ORDER BY STEP_NO")


if __name__ == "__main__":
    import pandas as pd

    from closeloop.db import connect

    pd.set_option("display.width", 200)
    conn = connect()
    total = run_close(conn, lambda label, secs, rows: print(f"{secs:6.2f}s {rows:>10,}  {label}"))
    print(f"\nclose completed in {total:.1f}s\n")
    print(recon.accuracy(conn))
    print(exposure.status_counts(conn))
    print(credit.summary(conn))
