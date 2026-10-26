"""Analyst review of breaks, close readiness and the close pack."""
from datetime import datetime

from closeloop.db import query_df

DECISIONS = ["APPROVE_FIX", "REJECT_FIX", "ESCALATE"]


def record(conn, break_keys: list[str], decision: str, note: str = "", user: str = "analyst"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [(k, decision, note[:500], user, now) for k in break_keys]
    conn.execute("DELETE FROM BREAK_REVIEW WHERE BREAK_KEY IN ({keys})", {"keys": break_keys})
    conn.ext.insert_multi("BREAK_REVIEW", rows,
                          columns=["BREAK_KEY", "DECISION", "NOTE", "DECIDED_BY", "DECIDED_AT"])


def bulk_approve_sql(where: str) -> str:
    return f"""INSERT INTO BREAK_REVIEW (BREAK_KEY, DECISION, NOTE, DECIDED_BY)
               SELECT BREAK_KEY, 'APPROVE_FIX', 'Bulk approval', 'analyst' FROM BREAKS b
               WHERE STATUS = 'OPEN' AND ({where})
                 AND NOT EXISTS (SELECT 1 FROM BREAK_REVIEW r WHERE r.BREAK_KEY = b.BREAK_KEY)"""


def readiness(conn) -> dict:
    return conn.execute("""
        SELECT COUNT(*) AS TOTAL,
               SUM(CASE WHEN b.STATUS = 'AUTO_CLEARED' THEN 1 ELSE 0 END) AS AUTO_CLEARED,
               SUM(CASE WHEN r.DECISION IS NOT NULL THEN 1 ELSE 0 END) AS REVIEWED,
               SUM(CASE WHEN b.STATUS = 'OPEN' AND r.DECISION IS NULL THEN 1 ELSE 0 END) AS AWAITING,
               SUM(CASE WHEN b.STATUS = 'OPEN' AND r.DECISION IS NULL AND b.SEVERITY = 'HIGH' THEN 1 ELSE 0 END)
                   AS HIGH_AWAITING,
               SUM(CASE WHEN r.DECISION = 'ESCALATE' THEN 1 ELSE 0 END) AS ESCALATED
        FROM BREAKS b LEFT JOIN BREAK_REVIEW r ON r.BREAK_KEY = b.BREAK_KEY""").fetchone()


def close_pack(conn, close_seconds: float | None = None) -> str:
    """Markdown summary for the close file."""
    r = readiness(conn)
    by_type = query_df(conn, """
        SELECT b.BREAK_TYPE, b.SEVERITY, COUNT(*) AS BREAKS, SUM(ABS(b.AMOUNT_DIFF)) AS AMOUNT,
               SUM(CASE WHEN b.STATUS = 'AUTO_CLEARED' THEN 1 ELSE 0 END) AS AUTO_CLEARED,
               SUM(CASE WHEN r.DECISION = 'APPROVE_FIX' THEN 1 ELSE 0 END) AS APPROVED
        FROM BREAKS b LEFT JOIN BREAK_REVIEW r ON r.BREAK_KEY = b.BREAK_KEY
        GROUP BY b.BREAK_TYPE, b.SEVERITY ORDER BY AMOUNT DESC""")
    je = conn.execute("""SELECT COUNT(*) AS N, SUM(ABS(AMOUNT)) AS TOTAL,
                                SUM(CASE WHEN NEEDS_EVIDENCE THEN 1 ELSE 0 END) AS EVIDENCE
                         FROM PROPOSED_JOURNALS""").fetchone()
    exp = conn.execute("""SELECT COUNT(*) AS N, SUM(TRUE_EXPOSURE_INR - REPORTED_EXPOSURE_INR) AS GAP
                          FROM ENTITY_EXPOSURE WHERE STATUS = 'HIDDEN_BREACH'""").fetchone()
    ready = "READY TO SIGN OFF" if r["HIGH_AWAITING"] == 0 else f"BLOCKED: {r['HIGH_AWAITING']:,} high-severity breaks awaiting review"
    lines = [
        f"# Month-end close pack — generated {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"**Status: {ready}**",
        "",
        f"- Breaks: {r['TOTAL']:,} total, {r['AUTO_CLEARED']:,} auto-cleared, {r['REVIEWED']:,} reviewed, "
        f"{r['AWAITING']:,} awaiting review, {r['ESCALATED']:,} escalated",
        f"- Proposed journals: {je['N']:,} totalling {float(je['TOTAL'] or 0):,.2f} (absolute), "
        f"{je['EVIDENCE']:,} need supporting evidence",
        f"- Hidden limit breaches: {exp['N']:,} counterparties, ₹{float(exp['GAP'] or 0) / 1e7:,.1f} Cr unreported exposure",
    ]
    if close_seconds:
        lines.append(f"- Reconciliation run time: {close_seconds:.1f} s")
    lines += ["", "## Breaks by type", "", by_type.to_markdown(index=False, floatfmt=",.2f")]
    return "\n".join(lines)
