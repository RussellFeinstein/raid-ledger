"""Officer Tools — roster import, player management, benchmarks, collection, notes.

Requires officer name (entered in sidebar) for all write actions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import streamlit as st

from dashboard.async_helpers import run_async
from dashboard.data_loader import (
    get_all_benchmarks,
    get_all_players,
    get_collection_runs,
    get_most_recent_benchmark,
    get_player_notes,
)
from raid_ledger.api.wowaudit import WowauditClient
from raid_ledger.config import load_config
from raid_ledger.db.repositories import (
    BenchmarkRepo,
    NoteRepo,
    PlayerRepo,
)
from raid_ledger.engine.collector import NoBenchmarkError, WeeklyCollector
from raid_ledger.models.benchmark import WeeklyBenchmark
from raid_ledger.models.player import Player, PlayerStatus
from raid_ledger.utils import most_recent_tuesday

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.markdown("# Officer Tools")

with st.expander("How this works"):
    st.markdown(
        "Use these tools to manage your guild's roster, set weekly benchmarks, "
        "trigger data collection from wowaudit, and add officer notes. "
        "Your **officer name** (entered in the sidebar) is required for all write actions."
    )

session = st.session_state.get("db_session")
if session is None:
    st.error("Database session not available.")
    st.stop()

officer_name = st.session_state.get("officer_name", "").strip()
config = load_config()


def _require_officer() -> bool:
    """Check that officer name is set. Show warning if not."""
    if not officer_name:
        st.warning("Enter your name in the sidebar before making changes.")
        return False
    return True


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_roster, tab_players, tab_bench, tab_collect, tab_notes = st.tabs(
    ["Roster Import", "Players", "Benchmarks", "Collect", "Notes"]
)

# ---------------------------------------------------------------------------
# Tab 1: Roster Import
# ---------------------------------------------------------------------------

with tab_roster:
    st.markdown("## Import Roster from Wowaudit")

    if not config.wowaudit.api_key:
        st.warning(
            "No wowaudit API key configured. "
            "Set the `WOWAUDIT_API_KEY` environment variable. "
            "Generate a key at your team's API settings page on wowaudit.com."
        )
    else:
        if st.button("Fetch Roster from Wowaudit", key="fetch_roster"):
            try:
                with st.spinner("Fetching roster from wowaudit..."):
                    client = WowauditClient(
                        wowaudit_config=config.wowaudit,
                        collection_config=config.collection,
                    )
                    members = run_async(client.fetch_roster())
                st.session_state["fetched_roster"] = members
                st.success(f"Fetched {len(members)} characters from wowaudit.")
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                st.error(f"Failed to fetch roster: {exc}")

        fetched = st.session_state.get("fetched_roster")
        if fetched:
            player_repo = PlayerRepo(session)
            rows = []
            for m in fetched:
                existing = player_repo.get_by_name_realm_region(m.name, m.realm)
                rows.append({
                    "Name": m.name,
                    "Realm": m.realm,
                    "Class": m.class_name,
                    "Role": m.role.capitalize(),
                    "Rank": m.rank,
                    "In DB": "Yes" if existing else "",
                    "_member": m,
                    "_existing": existing is not None,
                })

            new_members = [r for r in rows if not r["_existing"]]
            existing_count = len(rows) - len(new_members)

            st.caption(
                f"{len(rows)} characters total. "
                f"{existing_count} already in database, "
                f"{len(new_members)} new."
            )

            if new_members:
                st.markdown("**New characters (not yet in database):**")
                display_rows = [
                    {k: v for k, v in r.items() if not k.startswith("_")}
                    for r in new_members
                ]
                st.dataframe(display_rows, width="stretch", hide_index=True)

                if st.button("Import All New Characters", key="import_roster"):
                    if not _require_officer():
                        st.stop()
                    imported = 0
                    for r in new_members:
                        m = r["_member"]
                        player_repo.create(Player(
                            name=m.name,
                            realm=m.realm,
                            region=config.guild.region or "us",
                            class_name=m.class_name,
                            role=m.role,
                            status=PlayerStatus.CORE,
                            joined_date=date.today(),
                        ))
                        imported += 1
                    session.commit()
                    st.success(f"Imported {imported} players.")
                    # Clear cached roster so next fetch reflects changes
                    del st.session_state["fetched_roster"]
                    st.rerun()
            else:
                st.info("All characters are already in the database.")

# ---------------------------------------------------------------------------
# Tab 2: Player Management
# ---------------------------------------------------------------------------

with tab_players:
    st.markdown("## Manage Players")

    players = get_all_players(session)
    if not players:
        st.info("No players in the database. Import a roster first.")
    else:
        status_options = [s.value for s in PlayerStatus]

        for p in players:
            col1, col2, col3 = st.columns([3, 2, 2])
            col1.text(f"{p.name} — {p.realm} ({p.class_name}, {p.role})")
            new_status = col2.selectbox(
                "Status",
                options=status_options,
                index=status_options.index(p.status),
                key=f"status_{p.player_id}",
                label_visibility="collapsed",
            )
            if new_status != p.status:
                if col3.button(f"Save {p.name}", key=f"save_{p.player_id}"):
                    if not _require_officer():
                        st.stop()
                    player_repo = PlayerRepo(session)
                    player_repo.update_status(p.player_id, PlayerStatus(new_status))
                    session.commit()
                    st.success(f"Updated {p.name} to {new_status}.")
                    st.rerun()

# ---------------------------------------------------------------------------
# Tab 3: Benchmark Editor
# ---------------------------------------------------------------------------

with tab_bench:
    st.markdown("## Weekly Benchmarks")

    current = get_most_recent_benchmark(session)
    if current:
        st.markdown(
            f"**Current benchmark** (week of {current.week_of}): "
            f"{current.min_mplus_runs} runs at +{current.min_key_level}, "
            f"{current.min_vault_slots} vault slots"
            + (f", {current.min_ilvl} ilvl" if current.min_ilvl else "")
            + f" (set by {current.set_by})"
        )
    else:
        st.info("No benchmarks set yet.")

    st.markdown("### Set Weekly Benchmark")
    st.caption("One benchmark per week. Setting a benchmark for an existing week will update it.")

    week_of = st.date_input(
        "Week of (Tuesday)",
        value=most_recent_tuesday(),
        key="bench_week",
    )
    min_runs = st.number_input(
        "Minimum M+ runs",
        min_value=0, max_value=20,
        value=config.benchmarks.default_min_mplus_runs,
        key="bench_runs",
    )
    min_key = st.number_input(
        "Minimum key level",
        min_value=0, max_value=30,
        value=config.benchmarks.default_min_key_level,
        key="bench_key",
    )
    use_ilvl = st.checkbox(
        "Enforce item level requirement (fetches ilvl from Raider.io)",
        key="bench_use_ilvl",
    )
    min_ilvl = None
    if use_ilvl:
        min_ilvl = st.number_input(
            "Minimum item level",
            min_value=0, max_value=700, value=250,
            key="bench_ilvl",
        )
    min_vault = st.number_input(
        "Minimum vault slots",
        min_value=0, max_value=3,
        value=config.benchmarks.default_min_vault_slots,
        key="bench_vault",
    )

    if st.button("Set Weekly Benchmark", key="bench_submit"):
        if not _require_officer():
            st.stop()

        # Snap to Tuesday if needed
        tuesday = most_recent_tuesday(week_of)
        if tuesday != week_of:
            st.warning(f"Adjusted date to Tuesday: {tuesday}")

        benchmark_repo = BenchmarkRepo(session)
        benchmark_repo.create_or_update(WeeklyBenchmark(
            week_of=tuesday,
            min_mplus_runs=min_runs,
            min_key_level=min_key,
            min_ilvl=min_ilvl,
            min_vault_slots=min_vault,
            set_by=officer_name,
            set_at=datetime.now(tz=UTC),
        ))
        session.commit()
        st.success(f"Benchmark set for week of {tuesday}.")
        st.rerun()

    # History
    all_benchmarks = get_all_benchmarks(session)
    if all_benchmarks:
        st.markdown("### Benchmark History")
        history_rows = [
            {
                "Week": b.week_of,
                "Runs": b.min_mplus_runs,
                "Key Level": b.min_key_level,
                "iLvl": b.min_ilvl or "-",
                "Vault": b.min_vault_slots,
                "Set By": b.set_by,
            }
            for b in all_benchmarks
        ]
        st.dataframe(history_rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 4: Collect Data
# ---------------------------------------------------------------------------

with tab_collect:
    st.markdown("## Collect Weekly Data")

    if not config.wowaudit.api_key:
        st.warning("No wowaudit API key configured. Set `WOWAUDIT_API_KEY` env var.")
        st.stop()

    week_of = st.date_input(
        "Week of (Tuesday)",
        value=most_recent_tuesday(),
        key="collect_week",
    )
    tuesday = most_recent_tuesday(week_of)
    if tuesday != week_of:
        st.caption(f"Adjusted to Tuesday: {tuesday}")

    # Pre-checks
    active = get_all_players(session)
    active_count = sum(
        1 for p in active if p.status in (PlayerStatus.CORE, PlayerStatus.TRIAL)
    )
    benchmark = get_most_recent_benchmark(session)

    if active_count == 0:
        st.warning("No active players. Import a roster first (Roster Import tab).")
    if benchmark is None:
        st.warning("No benchmarks set. Set one first (Benchmarks tab).")

    # Confirmation dialog
    if "confirm_collect" not in st.session_state:
        st.session_state.confirm_collect = False

    if active_count > 0 and benchmark is not None:
        if not st.session_state.confirm_collect:
            if st.button(f"Collect for week of {tuesday}", key="collect_btn"):
                if not _require_officer():
                    st.stop()
                st.session_state.confirm_collect = True
                st.rerun()
        else:
            st.warning(
                f"This will fetch data from wowaudit and evaluate {active_count} "
                f"active players for the week of {tuesday}."
            )
            col1, col2 = st.columns(2)
            if col1.button("Confirm Collection", key="confirm_yes", type="primary"):
                st.session_state.confirm_collect = False
                try:
                    with st.spinner("Collecting data from wowaudit..."):
                        factory = st.session_state.get("session_factory")
                        fresh_session = factory()
                        try:
                            client = WowauditClient(
                                wowaudit_config=config.wowaudit,
                                collection_config=config.collection,
                            )
                            collector = WeeklyCollector(fresh_session, client, config)
                            result = run_async(collector.collect(tuesday))
                        finally:
                            fresh_session.close()

                    if result.status == "completed":
                        st.success(
                            f"Collection complete: {result.players_collected} players collected."
                        )
                    elif result.status == "partial":
                        st.warning(
                            f"Partial collection: {result.players_collected} collected, "
                            f"{result.api_errors} errors."
                        )
                    else:
                        st.error(
                            f"Collection failed: {result.api_errors} errors.\n"
                            + "\n".join(result.errors)
                        )
                except NoBenchmarkError:
                    st.error("No benchmark set. Create one in the Benchmarks tab.")
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    st.error(f"Collection error: {exc}")
                    st.session_state.confirm_collect = False

            if col2.button("Cancel", key="confirm_no"):
                st.session_state.confirm_collect = False
                st.rerun()

    # Collection run history
    runs = get_collection_runs(session, tuesday)
    if runs:
        st.markdown("### Collection History")
        run_rows = [
            {
                "Status": r["status"],
                "Players": r["players_collected"],
                "Errors": r["api_errors"],
                "Started": r["started_at"],
                "Completed": r["completed_at"],
            }
            for r in runs
        ]
        st.dataframe(run_rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Tab 5: Officer Notes
# ---------------------------------------------------------------------------

with tab_notes:
    st.markdown("## Add Officer Note")

    players = get_all_players(session)
    if not players:
        st.info("No players in the database.")
        st.stop()

    player_options = {f"{p.name} — {p.realm} ({p.status})": p for p in players}
    selected_label = st.selectbox(
        "Player",
        options=sorted(player_options.keys()),
        key="note_player",
    )
    if not selected_label:
        st.stop()

    selected_player = player_options[selected_label]

    tie_to_week = st.checkbox("Tie to specific week", key="note_week_check")
    note_week = None
    if tie_to_week:
        note_week = st.date_input(
            "Week of",
            value=most_recent_tuesday(),
            key="note_week",
        )

    note_text = st.text_area("Note", key="note_text", placeholder="Enter your note...")

    if st.button("Add Note", key="add_note"):
        if not _require_officer():
            st.stop()
        if not note_text.strip():
            st.warning("Note text cannot be empty.")
        else:
            note_repo = NoteRepo(session)
            note_repo.create(
                player_id=selected_player.player_id,
                note_text=note_text.strip(),
                created_by=officer_name,
                week_of=note_week,
            )
            session.commit()
            st.success(f"Note added for {selected_player.name}.")

    # Show existing notes
    existing_notes = get_player_notes(session, selected_player.player_id)
    if existing_notes:
        st.markdown("### Existing Notes")
        for note in existing_notes:
            week_label = note["week_of"].strftime("%b %d") if note["week_of"] else "General"
            st.markdown(
                f"- **[{week_label}]** {note['note_text']} "
                f"— *{note['created_by']}* ({note['created_at'].strftime('%Y-%m-%d')})"
            )
