"""Player Timeline — per-player history with heatmap and detail cards.

Player dropdown -> 12-week heatmap strip + per-week detail cards +
M+ score trend line + at-a-glance summary.
"""

from __future__ import annotations

import streamlit as st

from dashboard.components.status_badge import reason_display, status_label
from dashboard.data_loader import (
    get_active_players,
    get_failure_rate,
    get_player_history,
    get_player_notes,
)

# ---------------------------------------------------------------------------
# Page help
# ---------------------------------------------------------------------------

with st.expander("How this works"):
    st.markdown(
        "Select a player to see their week-by-week history. "
        "The heatmap shows pass/fail/flag at a glance. "
        "Cards below show details for each week including M+ runs, "
        "ilvl, failure reasons, and any officer notes."
    )

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

session = st.session_state.get("db_session")

if session is None:
    st.error("Database session not available.")
    st.stop()

st.markdown("# Player Timeline")

players = get_active_players(session)

if not players:
    st.info("No active players. Import a roster from Officer Tools.")
    st.stop()

# Player selector
player_names = {p.name: p for p in players}
selected_name = st.selectbox(
    "Select Player",
    options=sorted(player_names.keys()),
    help="Choose a player to view their weekly history.",
)

if not selected_name:
    st.stop()

player = player_names[selected_name]

# ---------------------------------------------------------------------------
# At-a-glance summary
# ---------------------------------------------------------------------------

rate = get_failure_rate(session, player.player_id, lookback_weeks=5)
st.markdown(
    f"**{player.name}** — {player.class_name} ({player.role.capitalize()}) "
    f"| Status: {player.status.capitalize()} "
    f"| Failed **{rate.failures}** of last **{rate.total_weeks}** weeks"
)

# ---------------------------------------------------------------------------
# Heatmap strip (12 weeks)
# ---------------------------------------------------------------------------

history = get_player_history(session, player.player_id, weeks=12)

if not history:
    st.info("No weekly data yet for this player.")
    st.stop()

st.markdown("### Weekly Heatmap")
st.caption("Most recent week on the right.")

# Reverse so oldest is on the left
heatmap_data = list(reversed(history))
cols = st.columns(len(heatmap_data))
for col, week_data in zip(cols, heatmap_data):
    label = status_label(week_data["status"])
    week_str = week_data["week_of"].strftime("%m/%d")
    col.markdown(f"**{week_str}**")
    col.markdown(label)

# ---------------------------------------------------------------------------
# Per-week detail cards
# ---------------------------------------------------------------------------

st.markdown("### Week Details")
st.caption("Most recent week first.")

for week_data in history:
    week_str = week_data["week_of"].strftime("%b %d, %Y")
    label = status_label(week_data["status"])

    with st.expander(f"{week_str} — {label}"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("M+ Runs at Level", week_data["mplus_runs_at_level"])
        c2.metric(
            "Highest Key",
            week_data["highest_key_level"] if week_data["highest_key_level"] else "-",
        )
        c3.metric(
            "iLvl",
            f"{week_data['item_level']:.1f}" if week_data["item_level"] else "-",
        )
        c4.metric("Vault Slots", week_data["vault_slots_earned"])

        if week_data["reasons"]:
            st.markdown(
                "**Reasons:** "
                + ", ".join(reason_display(r) for r in week_data["reasons"])
            )

        if week_data["override_by"]:
            st.markdown(f"**Overridden by:** {week_data['override_by']}")

        # Officer notes for this week
        notes = get_player_notes(session, player.player_id, week_data["week_of"])
        if notes:
            st.markdown("**Notes:**")
            for note in notes:
                st.markdown(
                    f"- {note['note_text']} "
                    f"— *{note['created_by']}*"
                )
