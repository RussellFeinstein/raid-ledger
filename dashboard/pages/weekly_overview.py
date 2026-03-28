"""Weekly Overview — color-coded roster table for the selected week.

Flagged players shown first (yellow), then failures (red), then passes (green).
Includes CSV export and first-run onboarding card.
"""

from __future__ import annotations

import csv
import io

import streamlit as st

from dashboard.components.filters import apply_filters
from dashboard.components.status_badge import reason_display, status_label
from dashboard.data_loader import get_weekly_summary

# ---------------------------------------------------------------------------
# Page help
# ---------------------------------------------------------------------------

with st.expander("How this works"):
    st.markdown(
        "This table shows every active raider's performance for the selected week. "
        "**Pass** (green checkmark) = met all requirements. "
        "**Fail** (red X) = failed at least one. "
        "**Flag** (orange warning) = needs your attention (missing data or pre-flagged). "
        "Use the sidebar filters to narrow by role or status."
    )

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

session = st.session_state.get("db_session")
selected_week = st.session_state.get("selected_week")
status_filter = st.session_state.get("status_filter", ["core", "trial"])
role_filter = st.session_state.get("role_filter", ["tank", "healer", "dps"])

if session is None:
    st.error("Database session not available.")
    st.stop()

# ---------------------------------------------------------------------------
# First-run onboarding
# ---------------------------------------------------------------------------

if selected_week is None:
    st.markdown("# Weekly Overview")
    st.info(
        "**Welcome to Raid Ledger**\n\n"
        "To get started:\n"
        "1. Open the **Settings** page and enter your guild name and realm\n"
        "2. Open **Officer Tools** and import your roster\n"
        "3. Set this week's benchmarks (M+ count, key level, ilvl)\n"
        "4. Click **Collect This Week** to pull data from wowaudit"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Weekly table
# ---------------------------------------------------------------------------

st.markdown(f"# Weekly Overview — {selected_week.strftime('%b %d, %Y')}")

summary = get_weekly_summary(session, selected_week)
filtered = apply_filters(summary, status_filter, role_filter)

if not filtered:
    st.warning("No data for the selected week and filters.")
    st.stop()

# Build table data
table_rows = []
for s in filtered:
    table_rows.append({
        "Status": status_label(s.snapshot_status),
        "Name": s.name,
        "Class": s.class_name,
        "Role": s.role.capitalize(),
        "M+ Runs": s.mplus_runs_at_level,
        "Highest Key": str(s.highest_key_level) if s.highest_key_level else "-",
        "Vault": s.vault_slots_earned,
        "Reasons": ", ".join(reason_display(r) for r in s.reasons) if s.reasons else "",
    })

st.dataframe(
    table_rows,
    width="stretch",
    hide_index=True,
    column_config={
        "Status": st.column_config.TextColumn("Status", help="Pass/Fail/Flag verdict"),
        "Name": st.column_config.TextColumn("Name"),
        "Class": st.column_config.TextColumn("Class"),
        "Role": st.column_config.TextColumn("Role"),
        "M+ Runs": st.column_config.NumberColumn(
            "M+ Runs",
            help="Mythic+ dungeons completed at or above the minimum key level",
        ),
        "Highest Key": st.column_config.TextColumn(
            "Highest Key",
            help="Highest keystone level completed this week",
        ),
        "Vault": st.column_config.NumberColumn(
            "Vault",
            help="Great Vault M+ slots earned (from wowaudit vault data)",
        ),
        "Reasons": st.column_config.TextColumn(
            "Reasons",
            help="Why this player failed or was flagged",
        ),
    },
)

# Field descriptions as visible text (WCAG 1.4.13 — tooltip content also as text)
st.caption(
    "M+ Runs = dungeons at or above minimum key level. "
    "Vault = Great Vault slots (1/4/8 runs = 1/2/3 slots). "
    "Status: Pass = met all requirements, Fail = didn't meet at least one, "
    "Flag = needs officer review."
)

# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

csv_buffer = io.StringIO()
writer = csv.DictWriter(csv_buffer, fieldnames=table_rows[0].keys())
writer.writeheader()
writer.writerows(table_rows)

st.download_button(
    label="Export CSV",
    data=csv_buffer.getvalue().encode("utf-8-sig"),  # BOM for Excel
    file_name=f"raid_ledger_{selected_week}.csv",
    mime="text/csv",
)
