"""Raid Ledger — Streamlit dashboard entrypoint.

Run with: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os

import streamlit as st

from dashboard.auth import check_password
from raid_ledger.config import load_config
from raid_ledger.db.connection import get_engine, get_session_factory, init_db

st.set_page_config(
    page_title="Raid Ledger",
    page_icon="\u2694\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

if not check_password():
    st.stop()

# ---------------------------------------------------------------------------
# DB session (created once per Streamlit session)
# ---------------------------------------------------------------------------


@st.cache_resource
def _init_db():
    config = load_config()
    db_url = os.environ.get("DATABASE_URL", config.database_url)
    engine = get_engine(db_url)
    init_db(engine)
    return get_session_factory(engine)


session_factory = _init_db()

if "db_session" not in st.session_state:
    st.session_state.db_session = session_factory()

# Expose factory for pages that need fresh sessions (e.g., collection trigger)
st.session_state.session_factory = session_factory


def get_session():
    return st.session_state.db_session


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

from dashboard.data_loader import get_collected_weeks, get_weekly_summary  # noqa: E402

session = get_session()

with st.sidebar:
    st.markdown("## Raid Ledger")

    # Officer name — locked once set, with option to change
    if st.session_state.get("officer_name_locked"):
        name = st.session_state["officer_name"]
        st.success(f"Signed in as **{name}**")
        if st.button("Change name", key="change_officer"):
            st.session_state["officer_name_locked"] = False
            st.session_state["officer_name"] = ""
            st.rerun()
    else:
        name_input = st.text_input(
            "Officer Name (required for changes)",
            key="officer_name_input",
        )
        if name_input.strip():
            if st.button("Lock in", key="lock_officer"):
                st.session_state["officer_name"] = name_input.strip()
                st.session_state["officer_name_locked"] = True
                st.rerun()

    st.divider()

    # Week selector
    weeks = get_collected_weeks(session)
    if weeks:
        selected_week = st.selectbox(
            "Week",
            options=weeks,
            format_func=lambda d: d.strftime("%b %d, %Y"),
            help="Select the reset week to view.",
        )
    else:
        selected_week = None
        st.info("No data collected yet.")

    st.divider()

    # Status filter
    status_filter = st.multiselect(
        "Filter by Player Status",
        options=["core", "trial", "bench"],
        default=["core", "trial"],
    )

    role_filter = st.multiselect(
        "Filter by Role",
        options=["tank", "healer", "dps"],
        default=["tank", "healer", "dps"],
    )

    st.divider()

    # Quick stats
    if selected_week:
        summary = get_weekly_summary(session, selected_week)
        filtered = [
            s for s in summary
            if s.player_status in status_filter and s.role in role_filter
        ]
        total = len(filtered)
        passed = sum(1 for s in filtered if s.snapshot_status == "pass")
        if total > 0:
            pct = passed / total * 100
            st.metric("Passed This Week", f"{passed} / {total} ({pct:.0f}%)")
        else:
            st.metric("Passed This Week", "0 / 0")

# Store selections for pages to use
st.session_state.selected_week = selected_week
st.session_state.status_filter = status_filter
st.session_state.role_filter = role_filter

# ---------------------------------------------------------------------------
# Page navigation
# ---------------------------------------------------------------------------

weekly_overview = st.Page(
    "pages/weekly_overview.py", title="Weekly Overview", default=True,
)
player_timeline = st.Page("pages/player_timeline.py", title="Player Timeline")
officer_tools = st.Page("pages/officer_tools.py", title="Officer Tools")

pg = st.navigation([weekly_overview, player_timeline, officer_tools])
pg.run()
