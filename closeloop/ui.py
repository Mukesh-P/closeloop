"""Visual layer for the Streamlit app: CSS, banner, section headers, chart and table styling."""
import html
import re

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

NAVY, CREAM, AMBER, BLUE, INK_2, GRID = "#0F1D2A", "#F7F5F0", "#F2A93B", "#2F6FD6", "#52606D", "#E4E0D6"

CSS = f"""
<style>
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.2rem; max-width: 1400px; }}

/* Sidebar */
[data-testid="stSidebar"] .stButton button[kind="primary"] {{ color: {NAVY}; font-weight: 600; border: none; }}
.cl-brand {{ font-family: 'Space Grotesk', sans-serif; font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em;
             color: #FFFFFF; margin-bottom: .1rem; }}
.cl-brand span {{ color: {AMBER}; }}
.cl-tag {{ color: #9FB1C1; font-size: .85rem; line-height: 1.4; margin-bottom: 1.2rem; }}
.cl-sys {{ display: flex; justify-content: space-between; padding: .45rem 0; border-bottom: 1px solid #22374A;
           font-size: .9rem; color: #C9D4DD; }}
.cl-sys span.lbl {{ display: inline-flex; align-items: center; }}
.cl-sys b {{ color: #FFFFFF; font-weight: 500; font-variant-numeric: tabular-nums; }}
.cl-label {{ text-transform: uppercase; letter-spacing: .12em; font-size: .7rem; color: {AMBER}; font-weight: 600;
             margin: 1.2rem 0 .3rem; }}
.cl-dot {{ display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; margin-right: .45rem; }}

/* Banner */
.cl-hero {{ background: radial-gradient(circle at 85% 20%, #1F3A55 0%, {NAVY} 55%); color: #FFFFFF;
            border-radius: 1.1rem; padding: 1.8rem 2.2rem; margin-bottom: 1.4rem; display: flex;
            justify-content: space-between; align-items: center; gap: 2rem; }}
.cl-hero .eyebrow {{ text-transform: uppercase; letter-spacing: .14em; font-size: .75rem; color: {AMBER}; font-weight: 600; }}
.cl-hero h1 {{ font-family: 'Space Grotesk', sans-serif; font-size: 2.6rem; line-height: 1.1; margin: .35rem 0 .4rem;
               color: #FFFFFF; padding: 0; }}
.cl-hero p {{ color: #B8C6D1; margin: 0; font-size: 1rem; }}
.cl-pill {{ padding: .55rem 1rem; border-radius: 999px; font-weight: 600; font-size: .9rem; white-space: nowrap; }}
.cl-pill.ok {{ background: #DDF3DD; color: #0A5C0A; }}
.cl-pill.block {{ background: #FCE3E3; color: #9B1F1F; }}

/* Section headers */
.cl-section {{ margin: 1.6rem 0 .6rem; }}
.cl-section .eyebrow {{ text-transform: uppercase; letter-spacing: .12em; font-size: .72rem; color: {BLUE}; font-weight: 600; }}
.cl-section h3 {{ font-family: 'Space Grotesk', sans-serif; font-size: 1.45rem; margin: .15rem 0 .1rem; padding: 0; }}
.cl-section p {{ color: {INK_2}; font-size: .92rem; margin: 0; }}

/* KPI cards */
[data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid #E6E1D6; border-radius: .9rem;
                             padding: 1rem 1.2rem; box-shadow: 0 1px 2px rgba(15, 29, 42, .04); height: 100%; }}
[data-testid="stMetricLabel"] p {{ color: {INK_2}; font-size: .85rem; font-weight: 500; }}
[data-testid="stMetricDelta"] {{ background: #F1EEE7 !important; color: {INK_2} !important; }}
[data-testid="stMetricValue"] {{ font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 2rem; }}
[class*="st-key-kpi_alert"] [data-testid="stMetric"] {{ border-left: 4px solid #D03B3B; }}
[class*="st-key-kpi_good"] [data-testid="stMetric"] {{ border-left: 4px solid {BLUE}; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: .35rem; border-bottom: 1px solid #E0DBD0; }}
.stTabs [data-baseweb="tab"] {{ padding: .55rem 1rem; border-radius: .6rem .6rem 0 0; font-weight: 500; }}
.stTabs [aria-selected="true"] {{ background: #FFFFFF; }}

/* Bordered containers act as cards */
[data-testid="stVerticalBlockBorderWrapper"] {{ background: #FFFFFF; }}
</style>
"""

SEVERITY_BADGE = {"HIGH": "🔴 High", "MEDIUM": "🟠 Medium", "LOW": "🟡 Low"}
STATUS_BADGE = {"OPEN": "Open", "AUTO_CLEARED": "✓ Auto-cleared"}
DECISION_BADGE = {"APPROVE_FIX": "✓ Approved", "REJECT_FIX": "✗ Rejected", "ESCALATE": "⚑ Escalated", "APPROVE": "✓ Approve",
                  "DECLINE": "✗ Decline", "REFER": "? Refer"}


def label(code: str) -> str:
    """'MISSING_IN_GL' -> 'Missing in GL'."""
    text = code.replace("_", " ").capitalize()
    return "OK" if text == "Ok" else re.sub(r"\b(gl|fx)\b", lambda m: m.group(1).upper(), text, flags=re.I)


def setup():
    st.markdown(CSS, unsafe_allow_html=True)
    pio.templates["closeloop"] = go.layout.Template(layout=go.Layout(
        font=dict(family="IBM Plex Sans, sans-serif", color=NAVY, size=13),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        colorway=[BLUE, "#EB6834", "#1BAF7A", "#EDA100", "#6250D6"],
        xaxis=dict(gridcolor=GRID, zerolinecolor="#C3C2B7", linecolor="#C3C2B7"),
        yaxis=dict(gridcolor=GRID, zerolinecolor="#C3C2B7", linecolor="#C3C2B7"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=dict(text="")),
        hoverlabel=dict(bgcolor="#FFFFFF", font_family="IBM Plex Sans, sans-serif"),
        margin=dict(l=0, r=10, t=30, b=0)))
    pio.templates.default = "plotly_white+closeloop"


def brand(volumes: dict, claude_on: bool):
    rows = "".join(f'<div class="cl-sys">{html.escape(k)}<b>{v:,}</b></div>' for k, v in volumes.items())
    st.markdown(f"""
        <div class="cl-brand">Close<span>Loop</span></div>
        <div class="cl-tag">Month-end close, exposure and credit decisions, computed inside Exasol.</div>
        <div class="cl-label">Source systems</div>{rows}""", unsafe_allow_html=True)


def status_line(claude_on: bool):
    dot = lambda on: f'<span class="cl-dot" style="background:{"#3CCB6B" if on else "#6F8597"}"></span>'
    st.markdown(f"""<div class="cl-label">Status</div>
        <div class="cl-sys"><span class="lbl">{dot(True)}Exasol</span><b>connected</b></div>
        <div class="cl-sys"><span class="lbl">{dot(claude_on)}Claude</span><b>{"on" if claude_on else "templates"}</b></div>""",
                unsafe_allow_html=True)


def hero(eyebrow: str, title: str, subtitle: str, pill: str | None = None, ok: bool = True):
    pill_html = f'<div class="cl-pill {"ok" if ok else "block"}">{html.escape(pill)}</div>' if pill else ""
    st.markdown(f"""<div class="cl-hero"><div><div class="eyebrow">{html.escape(eyebrow)}</div>
        <h1>{html.escape(title)}</h1><p>{subtitle}</p></div>{pill_html}</div>""", unsafe_allow_html=True)


def section(eyebrow: str, title: str, caption: str = ""):
    cap = f"<p>{caption}</p>" if caption else ""
    st.markdown(f'<div class="cl-section"><div class="eyebrow">{html.escape(eyebrow)}</div>'
                f'<h3>{html.escape(title)}</h3>{cap}</div>', unsafe_allow_html=True)


def kpi(col, label, value, delta=None, help=None, tone=None, key=None):
    """Metric card; tone 'alert' or 'good' adds a coloured edge (the label still says what it means)."""
    key = f"kpi_{tone or 'plain'}_{key or re.sub(r'[^a-z0-9]+', '_', label.lower())}"
    with col, st.container(key=key):
        st.metric(label, value, delta, help=help, delta_color="off", delta_arrow="off")


def heading(col: str) -> str:
    return col.replace("_INR", " (₹)").replace("_", " ").capitalize()


def pretty(df: pd.DataFrame) -> pd.DataFrame:
    """Display copy: badges for coded values and human column headings."""
    out = df.copy()
    for col, badges in [("SEVERITY", SEVERITY_BADGE), ("STATUS", STATUS_BADGE), ("DECISION", DECISION_BADGE),
                        ("LEGACY_DECISION", DECISION_BADGE)]:
        if col in out:
            out[col] = out[col].map(lambda v: badges.get(v, v) if isinstance(v, str) else "")
    for col in [c for c in ("BREAK_TYPE", "REASON_CODE", "PRODUCT") if c in out]:
        out[col] = out[col].map(lambda v: label(v) if isinstance(v, str) else v)
    return out.rename(columns=heading)


def column_config(df: pd.DataFrame) -> dict:
    cfg = {}
    for col, dtype in df.dtypes.items():
        name = heading(col)
        if "UTIL" in col:
            cfg[name] = st.column_config.NumberColumn(name, format="percent")
        elif col == "CONFIDENCE":
            cfg[name] = st.column_config.ProgressColumn(name, min_value=0, max_value=1, format="%.2f")
        elif pd.api.types.is_numeric_dtype(dtype):
            cfg[name] = st.column_config.NumberColumn(name, format="localized")
    return cfg


def table(df: pd.DataFrame, **kwargs):
    """st.dataframe with display formatting; selections index into the original df."""
    return st.dataframe(pretty(df), hide_index=True, width="stretch",
                        column_config=column_config(df) | kwargs.pop("column_config", {}), **kwargs)
