import os
import ssl

import pandas as pd
import pyexasol
from dotenv import load_dotenv

load_dotenv()

SCHEMA = os.getenv("EXASOL_SCHEMA", "CLOSELOOP")


def connect(schema: bool = True, schema_name: str | None = None) -> pyexasol.ExaConnection:
    conn = pyexasol.connect(
        dsn=os.getenv("EXASOL_DSN", "localhost:8563"),
        user=os.getenv("EXASOL_USER", "sys"),
        password=os.getenv("EXASOL_PASSWORD", "exasol"),
        encryption=True,
        # Exasol Personal ships a self-signed certificate
        websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
        compression=True,
        fetch_dict=True,
    )
    if schema:
        name = schema_name or SCHEMA
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
        conn.execute(f"OPEN SCHEMA {name}")
    return conn


def query_df(conn: pyexasol.ExaConnection, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Small result sets over the websocket (no HTTP callback), with numeric columns converted."""
    stmt = conn.execute(sql, params)
    cols = stmt.columns()
    df = pd.DataFrame(stmt.fetchall(), columns=list(cols))
    for name, meta in cols.items():
        if meta["type"] in ("DECIMAL", "DOUBLE"):
            df[name] = pd.to_numeric(df[name])
    return df
