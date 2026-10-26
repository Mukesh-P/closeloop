-- Entity resolution UDF: runs inside Exasol's parallel engine
CREATE OR REPLACE PYTHON3 SCALAR SCRIPT NORMALIZE_NAME (name VARCHAR(200))
RETURNS VARCHAR(200) AS
import re

NOISE = {"LTD", "LIMITED", "PVT", "PRIVATE", "CORP", "CORPORATION", "INC", "MS", "THE"}

def run(ctx):
    if ctx.name is None:
        return None
    s = re.sub(r"[^A-Z0-9 ]", " ", ctx.name.upper().replace("M/S", " "))
    return " ".join(t for t in s.split() if t not in NOISE)
