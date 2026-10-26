"""End-to-end tests against a running Exasol, in an isolated CLOSELOOP_TEST schema."""
import pytest

from closeloop import credit, explain, exposure, generate, pipeline, recon, review
from closeloop.db import connect, query_df
from closeloop.load import load

SCHEMA = "CLOSELOOP_TEST"


@pytest.fixture(scope="module")
def conn():
    c = connect(schema_name=SCHEMA)
    load(c, rows=50_000, counterparties=500, verbose=False)
    pipeline.run_close(c)
    yield c
    c.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    c.close()


def test_generator_is_deterministic():
    a = generate.generate_transactions(1000, seed=1)
    b = generate.generate_transactions(1000, seed=1)
    assert a[1].equals(b[1]) and a[2].equals(b[2])


def test_split_postings_sum_to_payment():
    core, gl, truth = generate.generate_transactions(20_000)
    split_ids = truth.loc[truth.TRUE_TYPE == "SPLIT_POSTING", "RECORD_ID"]
    txn = core[core.TXN_ID.isin(split_ids)].iloc[0]
    parts = gl[gl.REFERENCE == txn.REFERENCE]
    assert len(parts) >= 2 and round(parts.AMOUNT.sum(), 2) == txn.AMOUNT


def test_every_core_transaction_is_accounted_for(conn):
    unaccounted = conn.execute("""SELECT COUNT(*) FROM CORE_TXN c
                                  WHERE NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.TXN_ID = c.TXN_ID)""").fetchval()
    assert unaccounted == 0


def test_every_gl_posting_is_accounted_for(conn):
    unaccounted = conn.execute("""SELECT COUNT(*) FROM GL_ENTRY g
                                  WHERE NOT EXISTS (SELECT 1 FROM RECON_MATCH m WHERE m.GL_ID = g.GL_ID)""").fetchval()
    assert unaccounted == 0


def test_planted_breaks_are_detected(conn):
    acc = recon.accuracy(conn)
    assert acc.DETECTED.sum() == acc.PLANTED.sum()


@pytest.mark.parametrize("break_type", ["TIMING", "MISSING_IN_GL", "DUPLICATE_IN_GL", "SIGN_FLIP",
                                        "REF_TYPO", "FX_ROUNDING", "TIMING_AND_FX", "ORPHAN_IN_GL"])
def test_rule_based_types_have_full_recall(conn, break_type):
    row = recon.accuracy(conn).set_index("BREAK_TYPE").loc[break_type]
    assert row.CAUGHT == row.PLANTED


def test_unresolvable_cases_are_not_auto_matched(conn):
    """Typo + late + amount off must go to a human, never be silently matched."""
    wrongly_matched = conn.execute("""
        SELECT COUNT(*) FROM GROUND_TRUTH t JOIN RECON_MATCH m ON m.TXN_ID = t.RECORD_ID
        WHERE t.TRUE_TYPE = 'UNRESOLVABLE' AND m.MATCH_TYPE <> 'MISSING_IN_GL'""").fetchval()
    assert wrongly_matched == 0


def test_journals_correct_the_difference(conn):
    bad = conn.execute("""SELECT COUNT(*) FROM PROPOSED_JOURNALS j JOIN BREAKS b ON b.BREAK_KEY = j.BREAK_KEY
                          WHERE b.BREAK_TYPE <> 'TIMING'
                            AND COALESCE(b.GL_AMOUNT, 0) + j.AMOUNT <> COALESCE(b.CORE_AMOUNT, 0)""").fetchval()
    assert bad == 0


def test_candidates_find_the_true_match_for_unresolvable(conn):
    key, true_gl = conn.execute("""
        SELECT b.BREAK_KEY, 'GL' || SUBSTR(t.RECORD_ID, 3) FROM GROUND_TRUTH t
        JOIN BREAKS b ON b.TXN_ID = t.RECORD_ID WHERE t.TRUE_TYPE = 'UNRESOLVABLE' LIMIT 1""").fetchone().values()
    assert recon.candidates(conn, key).iloc[0].CANDIDATE == true_gl


def test_entity_resolution_links_every_name_variant(conn):
    assert exposure.unresolved(conn).ENTITIES.sum() == 0


def test_hidden_breach_is_over_limit_but_reported_within(conn):
    df = query_df(conn, "SELECT * FROM ENTITY_EXPOSURE WHERE STATUS = 'HIDDEN_BREACH'")
    assert not df.empty
    assert (df.TRUE_EXPOSURE_INR > df.APPROVED_LIMIT_INR).all()
    assert (df.REPORTED_EXPOSURE_INR <= df.APPROVED_LIMIT_INR).all()


def test_declines_never_exceed_limit_and_offers_fit(conn):
    df = query_df(conn, "SELECT * FROM CREDIT_DECISIONS")
    approved = df[df.DECISION == "APPROVE"]
    assert (approved.TRUE_EXPOSURE_INR + approved.REQUESTED_INR <= approved.APPROVED_LIMIT_INR).all()
    offers = df.dropna(subset=["COUNTER_OFFER_INR"])
    assert (offers.TRUE_EXPOSURE_INR + offers.COUNTER_OFFER_INR <= offers.APPROVED_LIMIT_INR).all()


def test_review_decisions_update_readiness(conn):
    before = review.readiness(conn)
    conn.execute(review.bulk_approve_sql("BREAK_TYPE = 'DUPLICATE_IN_GL'"))
    after = review.readiness(conn)
    assert after["REVIEWED"] > before["REVIEWED"]
    assert "# Month-end close pack" in review.close_pack(conn)


def test_decline_letter_has_no_internal_details(conn):
    d = query_df(conn, "SELECT * FROM CREDIT_DECISIONS WHERE DECISION = 'DECLINE' LIMIT 1").iloc[0].to_dict()
    text, _ = explain.letter(d)
    assert d["RISK_ID"] not in text and "risk system" not in text.lower()


@pytest.mark.parametrize("sql", ["DELETE FROM BREAKS", "SELECT 1; DROP TABLE BREAKS",
                                 "DROP TABLE BREAKS", "WITH x AS (SELECT 1) DELETE FROM BREAKS"])
def test_ask_rejects_writes(sql):
    assert explain.check_read_only(sql) is not None


def test_ask_presets_run(conn):
    for sql in explain.PRESET_QUESTIONS.values():
        assert explain.check_read_only(sql) is None
        query_df(conn, sql)
