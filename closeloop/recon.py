"""Tiered reconciliation of CORE_TXN against GL_ENTRY, executed entirely in Exasol.

Each pass only considers records still unmatched by earlier passes, so the
cheap exact join clears ~97% of volume before the fuzzier passes run. Every
match carries a confidence; the leftovers become the break register, and
the break register becomes proposed journal entries.
"""
import time

from closeloop.db import query_df

UNMATCHED = """
    NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.TXN_ID = c.TXN_ID)
    AND NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.GL_ID = g.GL_ID)
"""

# Breaks that need no human decision: they explain themselves
AUTO_CLEAR = """
    (BREAK_TYPE = 'TIMING' AND MONTH(POST_DATE) = MONTH(VALUE_DATE))
    OR BREAK_TYPE = 'REF_TYPO'
    OR (BREAK_TYPE = 'FX_ROUNDING' AND ABS(AMOUNT_DIFF) <= 100)
"""

PASSES = [
    ("Prepare: rank GL postings, flag duplicates", [
        "CREATE OR REPLACE TABLE RECON_MATCH (TXN_ID VARCHAR(20), GL_ID VARCHAR(20), "
        "MATCH_TYPE VARCHAR(20), AMOUNT_DIFF DECIMAL(18,2), DAY_DIFF DECIMAL(5,0), CONFIDENCE DECIMAL(3,2))",
        """CREATE OR REPLACE TABLE GL_RANKED AS
           SELECT g.*, ROW_NUMBER() OVER (PARTITION BY REFERENCE, ACCOUNT_ID, AMOUNT ORDER BY GL_ID) AS RN
           FROM GL_ENTRY g""",
        """INSERT INTO RECON_MATCH
           SELECT NULL, GL_ID, 'DUPLICATE_IN_GL', AMOUNT, 0, 0.95 FROM GL_RANKED WHERE RN > 1""",
    ]),
    ("Pass 1: exact match (reference, account, amount, date)", [
        """INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID, 'EXACT', 0, 0, 1.00
           FROM CORE_TXN c JOIN GL_RANKED g
             ON c.REFERENCE = g.REFERENCE AND c.ACCOUNT_ID = g.ACCOUNT_ID
            AND c.AMOUNT = g.AMOUNT AND c.VALUE_DATE = g.POST_DATE AND g.RN = 1""",
    ]),
    ("Pass 2: timing differences (posted 1-5 days late)", [
        f"""INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID, 'TIMING', 0, DAYS_BETWEEN(g.POST_DATE, c.VALUE_DATE), 0.95
           FROM CORE_TXN c JOIN GL_RANKED g
             ON c.REFERENCE = g.REFERENCE AND c.ACCOUNT_ID = g.ACCOUNT_ID AND c.AMOUNT = g.AMOUNT
           WHERE g.RN = 1 AND ABS(DAYS_BETWEEN(g.POST_DATE, c.VALUE_DATE)) BETWEEN 1 AND 5
             AND {UNMATCHED}""",
    ]),
    ("Pass 3: FX / rounding within tolerance (any timing)", [
        f"""INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID,
                  CASE WHEN g.POST_DATE = c.VALUE_DATE THEN 'FX_ROUNDING' ELSE 'TIMING_AND_FX' END,
                  g.AMOUNT - c.AMOUNT, DAYS_BETWEEN(g.POST_DATE, c.VALUE_DATE),
                  CASE WHEN g.POST_DATE = c.VALUE_DATE THEN 0.90 ELSE 0.85 END
           FROM CORE_TXN c JOIN GL_RANKED g
             ON c.REFERENCE = g.REFERENCE AND c.ACCOUNT_ID = g.ACCOUNT_ID
           WHERE g.RN = 1 AND g.AMOUNT <> c.AMOUNT AND SIGN(g.AMOUNT) = SIGN(c.AMOUNT)
             AND ABS(g.AMOUNT - c.AMOUNT) <= GREATEST(1, ABS(c.AMOUNT) * 0.005)
             AND ABS(DAYS_BETWEEN(g.POST_DATE, c.VALUE_DATE)) <= 5
             AND {UNMATCHED}""",
    ]),
    ("Pass 4: sign flips (debit posted as credit)", [
        f"""INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID, 'SIGN_FLIP', g.AMOUNT - c.AMOUNT, 0, 0.90
           FROM CORE_TXN c JOIN GL_RANKED g
             ON c.REFERENCE = g.REFERENCE AND c.ACCOUNT_ID = g.ACCOUNT_ID AND g.AMOUNT = -c.AMOUNT
           WHERE g.RN = 1 AND {UNMATCHED}""",
    ]),
    ("Pass 5: split postings (many GL lines sum to one payment)", [
        """INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID, 'SPLIT_POSTING', 0, 0, 0.90
           FROM CORE_TXN c
           JOIN (SELECT REFERENCE, ACCOUNT_ID, SUM(AMOUNT) AS TOTAL
                 FROM GL_RANKED g
                 WHERE RN = 1 AND NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.GL_ID = g.GL_ID)
                 GROUP BY REFERENCE, ACCOUNT_ID HAVING COUNT(*) > 1) s
             ON s.REFERENCE = c.REFERENCE AND s.ACCOUNT_ID = c.ACCOUNT_ID AND s.TOTAL = c.AMOUNT
           JOIN GL_RANKED g ON g.REFERENCE = c.REFERENCE AND g.ACCOUNT_ID = c.ACCOUNT_ID AND g.RN = 1
           WHERE NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.TXN_ID = c.TXN_ID)
             AND NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.GL_ID = g.GL_ID)""",
    ]),
    ("Pass 6: fuzzy reference match (EDIT_DISTANCE <= 2)", [
        f"""INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, g.GL_ID, 'REF_TYPO', 0, 0,
                  0.85 - 0.05 * EDIT_DISTANCE(c.REFERENCE, g.REFERENCE)
           FROM CORE_TXN c JOIN GL_RANKED g
             ON c.ACCOUNT_ID = g.ACCOUNT_ID AND c.AMOUNT = g.AMOUNT AND c.VALUE_DATE = g.POST_DATE
           WHERE g.RN = 1 AND c.REFERENCE <> g.REFERENCE
             AND EDIT_DISTANCE(c.REFERENCE, g.REFERENCE) <= 2
             AND {UNMATCHED}""",
    ]),
    ("Pass 7: classify leftovers (missing / orphan)", [
        """INSERT INTO RECON_MATCH
           SELECT c.TXN_ID, NULL, 'MISSING_IN_GL', -c.AMOUNT, 0, 1.00 FROM CORE_TXN c
           WHERE NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.TXN_ID = c.TXN_ID)""",
        """INSERT INTO RECON_MATCH
           SELECT NULL, g.GL_ID, 'ORPHAN_IN_GL', g.AMOUNT, 0, 1.00 FROM GL_RANKED g
           WHERE NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.GL_ID = g.GL_ID)""",
    ]),
    ("Build break register: severity, status, suggested action", [
        f"""CREATE OR REPLACE TABLE BREAKS AS
           SELECT b.*, CASE WHEN {AUTO_CLEAR} THEN 'AUTO_CLEARED' ELSE 'OPEN' END AS STATUS
           FROM (
           SELECT COALESCE(m.TXN_ID, '-') || '/' || COALESCE(m.GL_ID, '-') AS BREAK_KEY,
                  m.MATCH_TYPE AS BREAK_TYPE, m.TXN_ID, m.GL_ID,
                  COALESCE(c.REFERENCE, g.REFERENCE) AS REFERENCE,
                  COALESCE(c.ACCOUNT_ID, g.ACCOUNT_ID) AS ACCOUNT_ID,
                  c.VALUE_DATE, g.POST_DATE,
                  c.AMOUNT AS CORE_AMOUNT, g.AMOUNT AS GL_AMOUNT,
                  COALESCE(c.CURRENCY, g.CURRENCY) AS CURRENCY,
                  m.AMOUNT_DIFF, m.DAY_DIFF, m.CONFIDENCE,
                  CASE
                    WHEN m.MATCH_TYPE IN ('MISSING_IN_GL', 'SIGN_FLIP', 'ORPHAN_IN_GL', 'DUPLICATE_IN_GL') THEN 'HIGH'
                    WHEN m.MATCH_TYPE IN ('TIMING', 'TIMING_AND_FX') AND MONTH(g.POST_DATE) <> MONTH(c.VALUE_DATE) THEN 'HIGH'
                    WHEN m.MATCH_TYPE IN ('FX_ROUNDING', 'TIMING_AND_FX') AND ABS(m.AMOUNT_DIFF) > 100 THEN 'MEDIUM'
                    ELSE 'LOW' END AS SEVERITY,
                  CASE m.MATCH_TYPE
                    WHEN 'MISSING_IN_GL' THEN 'Post missing journal to GL for this core transaction'
                    WHEN 'ORPHAN_IN_GL' THEN 'Investigate manual journal with no source transaction; reverse if unsupported'
                    WHEN 'DUPLICATE_IN_GL' THEN 'Reverse duplicate GL posting'
                    WHEN 'SIGN_FLIP' THEN 'Reverse and repost with correct debit/credit direction'
                    WHEN 'FX_ROUNDING' THEN 'Post FX revaluation adjustment for the difference'
                    WHEN 'TIMING_AND_FX' THEN 'Post FX adjustment; posting was also late'
                    WHEN 'TIMING' THEN CASE WHEN MONTH(g.POST_DATE) <> MONTH(c.VALUE_DATE)
                                            THEN 'Accrue in close period: posting slipped past month-end cut-off'
                                            ELSE 'No action: timing difference self-clears' END
                    WHEN 'REF_TYPO' THEN 'Auto-matched on fuzzy reference; correct reference in GL'
                  END AS SUGGESTED_ACTION
           FROM RECON_MATCH m
           LEFT JOIN CORE_TXN c ON c.TXN_ID = m.TXN_ID
           LEFT JOIN GL_RANKED g ON g.GL_ID = m.GL_ID
           WHERE m.MATCH_TYPE NOT IN ('EXACT', 'SPLIT_POSTING')) b""",
    ]),
    ("Generate proposed correcting journals", [
        """CREATE OR REPLACE TABLE PROPOSED_JOURNALS AS
           SELECT 'JE' || LPAD(ROW_NUMBER() OVER (ORDER BY BREAK_KEY), 7, '0') AS JOURNAL_ID,
                  BREAK_KEY, BREAK_TYPE,
                  LAST_DAY(COALESCE(VALUE_DATE, POST_DATE)) AS POSTING_DATE,
                  ACCOUNT_ID,
                  CASE WHEN BREAK_TYPE IN ('FX_ROUNDING', 'TIMING_AND_FX') THEN 'FX_REVALUATION'
                       WHEN BREAK_TYPE = 'TIMING' THEN 'ACCRUALS'
                       WHEN BREAK_TYPE = 'ORPHAN_IN_GL' THEN 'SUSPENSE_INVESTIGATION'
                       ELSE 'RECON_SUSPENSE' END AS CONTRA_ACCOUNT,
                  CASE WHEN BREAK_TYPE = 'TIMING' THEN CORE_AMOUNT
                       ELSE COALESCE(CORE_AMOUNT, 0) - COALESCE(GL_AMOUNT, 0) END AS AMOUNT,
                  CURRENCY,
                  BREAK_TYPE || ' ' || REFERENCE AS NARRATIVE,
                  CASE WHEN BREAK_TYPE = 'TIMING' THEN TRUE ELSE FALSE END AS REVERSE_NEXT_PERIOD,
                  CASE WHEN BREAK_TYPE = 'ORPHAN_IN_GL' THEN TRUE ELSE FALSE END AS NEEDS_EVIDENCE
           FROM BREAKS
           WHERE BREAK_TYPE IN ('MISSING_IN_GL', 'DUPLICATE_IN_GL', 'SIGN_FLIP', 'FX_ROUNDING',
                                'TIMING_AND_FX', 'ORPHAN_IN_GL')
              OR (BREAK_TYPE = 'TIMING' AND SEVERITY = 'HIGH')""",
    ]),
]


def run(conn, on_step=None):
    """Run all passes. Returns list of (label, seconds, rows_affected) and total seconds."""
    results = []
    total = time.time()
    for label, stmts in PASSES:
        t = time.time()
        rows = 0
        for s in stmts:
            st = conn.execute(s)
            rows += st.rowcount() or 0
        results.append((label, time.time() - t, rows))
        if on_step:
            on_step(label, time.time() - t, rows)
    return results, time.time() - total


def summary(conn):
    return query_df(conn, """
        SELECT MATCH_TYPE, COUNT(*) AS RECORDS, SUM(ABS(AMOUNT_DIFF)) AS ABS_IMPACT
        FROM RECON_MATCH GROUP BY MATCH_TYPE ORDER BY RECORDS DESC""")


def accuracy(conn):
    """Score the engine against the planted answer key.

    DETECTED: planted problem shows up in the break register under any type.
    CAUGHT: classified as exactly the planted type. PREDICTED: engine's count of that type.
    """
    return query_df(conn, """
        WITH p AS (SELECT DISTINCT COALESCE(TXN_ID, GL_ID) AS RID, MATCH_TYPE
                   FROM RECON_MATCH WHERE MATCH_TYPE <> 'EXACT'),
        types AS (SELECT TRUE_TYPE AS T FROM GROUND_TRUTH UNION SELECT MATCH_TYPE FROM p)
        SELECT types.T AS BREAK_TYPE,
               (SELECT COUNT(*) FROM GROUND_TRUTH t WHERE t.TRUE_TYPE = types.T) AS PLANTED,
               (SELECT COUNT(*) FROM GROUND_TRUTH t JOIN p ON p.RID = t.RECORD_ID
                 WHERE t.TRUE_TYPE = types.T) AS DETECTED,
               (SELECT COUNT(*) FROM GROUND_TRUTH t JOIN p ON p.RID = t.RECORD_ID AND p.MATCH_TYPE = t.TRUE_TYPE
                 WHERE t.TRUE_TYPE = types.T) AS CAUGHT,
               (SELECT COUNT(*) FROM p WHERE p.MATCH_TYPE = types.T) AS PREDICTED
        FROM types ORDER BY PLANTED DESC""")


def candidates(conn, break_key: str):
    """Possible GL matches for an unmatched core transaction (or vice versa), scored."""
    b = conn.execute("SELECT * FROM BREAKS WHERE BREAK_KEY = {k}", {"k": break_key}).fetchone()
    if not b or b["BREAK_TYPE"] not in ("MISSING_IN_GL", "ORPHAN_IN_GL"):
        return None
    if b["BREAK_TYPE"] == "MISSING_IN_GL":
        sql = """SELECT GL_ID AS CANDIDATE, REFERENCE, POST_DATE AS DATE_, GL_AMOUNT AS AMOUNT,
                        EDIT_DISTANCE(REFERENCE, {ref}) AS REF_DISTANCE,
                        ABS(DAYS_BETWEEN(POST_DATE, {d})) AS DAYS_APART,
                        ROUND(ABS(GL_AMOUNT - {a}) / NULLIF(ABS({a}), 0) * 100, 2) AS PCT_AMOUNT_DIFF
                 FROM BREAKS WHERE BREAK_TYPE = 'ORPHAN_IN_GL' AND ACCOUNT_ID = {acc}"""
        d, a = b["VALUE_DATE"], b["CORE_AMOUNT"]
    else:
        sql = """SELECT TXN_ID AS CANDIDATE, REFERENCE, VALUE_DATE AS DATE_, CORE_AMOUNT AS AMOUNT,
                        EDIT_DISTANCE(REFERENCE, {ref}) AS REF_DISTANCE,
                        ABS(DAYS_BETWEEN(VALUE_DATE, {d})) AS DAYS_APART,
                        ROUND(ABS(CORE_AMOUNT - {a}) / NULLIF(ABS({a}), 0) * 100, 2) AS PCT_AMOUNT_DIFF
                 FROM BREAKS WHERE BREAK_TYPE = 'MISSING_IN_GL' AND ACCOUNT_ID = {acc}"""
        d, a = b["POST_DATE"], b["GL_AMOUNT"]
    return query_df(conn, 
        f"SELECT * FROM ({sql}) ORDER BY REF_DISTANCE + DAYS_APART + PCT_AMOUNT_DIFF LIMIT 5",
        {"ref": b["REFERENCE"], "d": d, "a": a, "acc": b["ACCOUNT_ID"]})
