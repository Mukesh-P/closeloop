"""Synthetic bank data with planted breaks.

Produces two datasets:
  1. Transaction reconciliation: CORE_TXN (core banking) vs GL_ENTRY (general ledger)
     with known break types injected, plus a hidden GROUND_TRUTH table so we can
     measure how accurately the engine classifies them.
  2. Counterparty exposure: the same legal entities booked in LOAN_BOOK, TREASURY
     and RISK_LIMITS under different IDs and name spellings.
"""
import numpy as np
import pandas as pd

CURRENCIES = np.array(["INR", "USD", "EUR", "GBP", "SGD", "AED"])
CCY_WEIGHTS = np.array([0.70, 0.12, 0.08, 0.04, 0.03, 0.03])

# Planted break rates (fraction of core transactions)
BREAK_RATES = {
    "MISSING_IN_GL": 0.008,
    "DUPLICATE_IN_GL": 0.003,
    "TIMING": 0.010,
    "FX_ROUNDING": 0.005,
    "SIGN_FLIP": 0.001,
    "REF_TYPO": 0.003,
    "SPLIT_POSTING": 0.004,    # one payment posted as 2-3 GL lines (a valid match, not a break)
    "TIMING_AND_FX": 0.002,    # posted late AND at a different rate
    "UNRESOLVABLE": 0.0005,    # typo + late + amount off: no rule should auto-match this
}
FX_ONLY = {"FX_ROUNDING", "TIMING_AND_FX"}
ORPHAN_RATE = 0.002  # GL entries with no core transaction (manual journals)

PREFIXES = ["Arcadia", "Bluewater", "Crescent", "Deccan", "Everest", "Falcon", "Granite",
            "Horizon", "Indus", "Juniper", "Kaveri", "Lotus", "Meridian", "Nilgiri", "Orion",
            "Peninsula", "Quantum", "Redwood", "Saffron", "Trident", "Unity", "Vertex",
            "Westbridge", "Zenith", "Aurora", "Banyan", "Cobalt", "Delta", "Emerald", "Fortune"]
SECTORS = ["Steel", "Textiles", "Logistics", "Infra", "Pharma", "Foods", "Power", "Motors",
           "Chemicals", "Realty", "Fintech", "Agro", "Cement", "Shipping", "Telecom", "Retail"]
SUFFIXES = ["Ltd", "Limited", "Pvt Ltd", "Private Limited", "Industries Ltd", "Corp"]


def _typo(s: str, rng: np.random.Generator) -> str:
    """Replace one character with a different alphanumeric."""
    i = rng.integers(3, len(s))
    c = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[rng.integers(0, 34)]
    if c == s[i]:
        c = "X" if c != "X" else "Y"
    return s[:i] + c + s[i + 1:]


def generate_transactions(n: int, seed: int = 42, period: str = "2026-09"):
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(f"{period}-01")
    days_in_month = start.days_in_month

    txn_ids = np.char.add("TX", np.char.zfill(np.arange(1, n + 1).astype(str), 10))
    refs = np.char.add("REF", np.char.zfill(rng.integers(0, 10**12, n).astype(str), 12))
    # make refs unique
    refs = np.char.add(refs, np.char.zfill(np.arange(n).astype(str), 8))
    accounts = np.char.add("AC", np.char.zfill(rng.integers(1, 200_000, n).astype(str), 8))
    day_offsets = rng.integers(0, days_in_month, n)
    value_dates = start + pd.to_timedelta(day_offsets, unit="D")
    ccy = rng.choice(CURRENCIES, n, p=CCY_WEIGHTS)
    amounts = np.round(np.exp(rng.normal(9.5, 1.6, n)), 2)  # lognormal, median ~13k
    sign = np.where(rng.random(n) < 0.55, 1, -1)
    amounts = amounts * sign
    cpty_idx = rng.integers(0, 5000, n)

    core = pd.DataFrame({
        "TXN_ID": txn_ids,
        "REFERENCE": refs,
        "ACCOUNT_ID": accounts,
        "VALUE_DATE": value_dates.date,
        "AMOUNT": amounts,
        "CURRENCY": ccy,
        "COUNTERPARTY_ID": cpty_idx,
    })

    # Assign one break type (or none) to each core txn
    labels = np.full(n, "MATCHED", dtype=object)
    r = rng.random(n)
    lo = 0.0
    for name, rate in BREAK_RATES.items():
        mask = (r >= lo) & (r < lo + rate)
        labels[mask] = name
        lo += rate
    # FX differences only make sense for non-INR
    fx_mask = np.isin(labels, list(FX_ONLY)) & (ccy == "INR")
    labels[fx_mask] = "MATCHED"

    gl = pd.DataFrame({
        "GL_ID": np.char.add("GL", np.char.zfill(np.arange(1, n + 1).astype(str), 10)),
        "REFERENCE": refs.copy(),
        "ACCOUNT_ID": accounts,
        "POST_DATE": value_dates.date,
        "AMOUNT": amounts.copy(),
        "CURRENCY": ccy,
        "SOURCE_TXN": txn_ids,  # hidden, dropped before load
    })

    def shift_dates(m):
        shift = rng.integers(1, 4, m.sum())
        gl.loc[m, "POST_DATE"] = (pd.to_datetime(gl.loc[m, "POST_DATE"]) + pd.to_timedelta(shift, unit="D")).dt.date

    def drift_amounts(m, lo, hi):
        drift = rng.uniform(lo, hi, m.sum()) * rng.choice([-1, 1], m.sum())
        gl.loc[m, "AMOUNT"] = np.round(gl.loc[m, "AMOUNT"] * (1 + drift), 2)

    def typo_refs(m):
        gl.loc[m, "REFERENCE"] = [_typo(s, rng) for s in gl.loc[m, "REFERENCE"]]

    shift_dates(labels == "TIMING")
    drift_amounts(labels == "FX_ROUNDING", 0.0005, 0.004)
    m = labels == "TIMING_AND_FX"
    shift_dates(m)
    drift_amounts(m, 0.0005, 0.004)
    m = labels == "SIGN_FLIP"
    gl.loc[m, "AMOUNT"] = -gl.loc[m, "AMOUNT"]
    typo_refs(labels == "REF_TYPO")
    m = labels == "UNRESOLVABLE"
    typo_refs(m)
    shift_dates(m)
    drift_amounts(m, 0.02, 0.05)

    # Split postings: replace the GL line with 2-3 lines that sum to the original amount
    m = labels == "SPLIT_POSTING"
    parts = []
    for row in gl[m].itertuples(index=False):
        k = int(rng.integers(2, 4))
        cents = int(round(row.AMOUNT * 100))
        cuts = np.sort(rng.choice(np.arange(1, abs(cents)), k - 1, replace=False)) if abs(cents) > k else []
        pieces = np.diff(np.concatenate([[0], cuts, [abs(cents)]])) * (1 if cents > 0 else -1)
        for j, p in enumerate(pieces):
            parts.append(row._replace(GL_ID=f"GLS{row.GL_ID[2:]}{j}", AMOUNT=p / 100))
    split = pd.DataFrame(parts, columns=gl.columns)
    gl = gl[~m]

    dup = gl[gl["SOURCE_TXN"].isin(txn_ids[labels == "DUPLICATE_IN_GL"])].copy()
    dup["GL_ID"] = np.char.add("GLD", np.char.zfill(np.arange(1, len(dup) + 1).astype(str), 9))

    gl = gl[~gl["SOURCE_TXN"].isin(txn_ids[labels == "MISSING_IN_GL"])]

    n_orphan = int(n * ORPHAN_RATE)
    orphan = pd.DataFrame({
        "GL_ID": np.char.add("GLM", np.char.zfill(np.arange(1, n_orphan + 1).astype(str), 9)),
        "REFERENCE": np.char.add("MJ", np.char.zfill(rng.integers(0, 10**10, n_orphan).astype(str), 10)),
        "ACCOUNT_ID": np.char.add("AC", np.char.zfill(rng.integers(1, 200_000, n_orphan).astype(str), 8)),
        "POST_DATE": (start + pd.to_timedelta(rng.integers(0, days_in_month, n_orphan), unit="D")).date,
        "AMOUNT": np.round(np.exp(rng.normal(9, 1.5, n_orphan)), 2),
        "CURRENCY": "INR",
        "SOURCE_TXN": None,
    })

    gl = pd.concat([gl, split, dup, orphan], ignore_index=True)
    gl = gl.sample(frac=1, random_state=seed).reset_index(drop=True)

    # The original of a duplicated posting matches cleanly; only the extra GL row is a break
    core_labels = np.where(labels == "DUPLICATE_IN_GL", "MATCHED", labels)
    truth_core = pd.DataFrame({"RECORD_ID": txn_ids, "SIDE": "CORE", "TRUE_TYPE": core_labels})
    truth_dup = pd.DataFrame({"RECORD_ID": dup["GL_ID"], "SIDE": "GL", "TRUE_TYPE": "DUPLICATE_IN_GL"})
    truth_orphan = pd.DataFrame({"RECORD_ID": orphan["GL_ID"], "SIDE": "GL", "TRUE_TYPE": "ORPHAN_IN_GL"})
    truth = pd.concat([truth_core, truth_dup, truth_orphan], ignore_index=True)
    truth = truth[truth["TRUE_TYPE"] != "MATCHED"]

    return core, gl.drop(columns=["SOURCE_TXN"]), truth


def _name_variant(canonical: str, rng: np.random.Generator) -> str:
    """Return the same entity name the way a different system might store it."""
    base, _, suffix = canonical.rpartition(" | ")
    v = rng.integers(0, 6)
    if v == 0:
        return f"{base} {suffix}".upper()
    if v == 1:
        return f"{base}, {suffix.replace('Limited', 'Ltd').replace('Private', 'Pvt')}"
    if v == 2:
        return f"{base} {suffix.replace('Ltd', 'Limited')}."
    if v == 3:
        return f"M/s {base} {suffix}"
    if v == 4:
        return f"{base.replace(' ', '  ')} {suffix}".lower()
    return f"{base} {suffix}"


def generate_exposure(n_cpty: int = 5000, seed: int = 7):
    rng = np.random.default_rng(seed)
    names = []
    seen = set()
    while len(names) < n_cpty:
        nm = f"{rng.choice(PREFIXES)} {rng.choice(SECTORS)}"
        if rng.random() < 0.5:
            nm = f"{rng.choice(PREFIXES)} {nm}"
        if nm in seen:
            continue
        seen.add(nm)
        names.append(f"{nm} | {rng.choice(SUFFIXES)}")

    loans, trades, limits = [], [], []
    for i, canonical in enumerate(names):
        limit = float(np.round(np.exp(rng.normal(19, 1.0)), -5))  # ~ crores
        loan_util = rng.uniform(0.1, 0.75)
        has_treasury = rng.random() < 0.45
        tr_util = rng.uniform(0.05, 0.6) if has_treasury else 0.0
        loan_amt = np.round(limit * loan_util, 2)
        # loans may be split across facilities
        n_fac = rng.integers(1, 4)
        parts = rng.dirichlet(np.ones(n_fac)) * loan_amt
        for j, p in enumerate(parts):
            loans.append({
                "FACILITY_ID": f"LN{i:05d}{j}",
                "BORROWER_CODE": f"L-{i:05d}",
                "BORROWER_NAME": _name_variant(canonical, rng),
                "OUTSTANDING_INR": round(float(p), 2),
            })
        if has_treasury:
            n_tr = rng.integers(1, 6)
            parts = rng.dirichlet(np.ones(n_tr)) * limit * tr_util
            for j, p in enumerate(parts):
                trades.append({
                    "TRADE_ID": f"TR{i:05d}{j}",
                    "CPTY_NAME": _name_variant(canonical, rng),
                    "PRODUCT": rng.choice(["FX_FWD", "IRS", "CCS", "FX_OPT"]),
                    "MTM_EXPOSURE_INR": round(float(p), 2),
                })
        # Risk system only sees loans and a stale treasury figure
        stale_tr = limit * tr_util * rng.uniform(0.0, 0.5)
        limits.append({
            "RISK_ID": f"CR{100000 + i}",
            "LEGAL_NAME": _name_variant(canonical, rng),
            "APPROVED_LIMIT_INR": limit,
            "REPORTED_EXPOSURE_INR": round(float(loan_amt + stale_tr), 2),
        })
    # New credit applications this month, filed under yet another spelling of the name
    apps = []
    for k, i in enumerate(rng.choice(n_cpty, n_cpty // 4, replace=False)):
        limit = limits[i]["APPROVED_LIMIT_INR"]
        apps.append({
            "APP_ID": f"APP{k + 1:05d}",
            "APPLICANT_NAME": _name_variant(names[i], rng),
            "PRODUCT": rng.choice(["TERM_LOAN", "WORKING_CAPITAL", "TRADE_FINANCE"]),
            "REQUESTED_INR": float(np.round(limit * rng.uniform(0.05, 0.35), -4)),
        })
    return pd.DataFrame(loans), pd.DataFrame(trades), pd.DataFrame(limits), pd.DataFrame(apps)
