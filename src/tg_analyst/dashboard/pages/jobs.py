"""Jobs page: queue scraping and analysis tasks for the worker."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import streamlit as st

from tg_analyst import jobs
from tg_analyst.dashboard.common import session_factory
from tg_analyst.ingest.groupref import GroupRefError, parse_group_ref


def jobs_page() -> None:
    st.title("Scraping jobs")
    st.caption(
        "Jobs are queued here and run by the worker (`tg-analyst worker`). "
        "The worker needs a logged-in Telegram session: run `tg-analyst login` once."
    )

    with st.form("backfill", clear_on_submit=False):
        st.subheader("Fetch group history")
        group = st.text_input(
            "Group",
            placeholder="@username, t.me/… link, Telegram Web link or numeric id",
            help="Your Telegram account must be a member of the group.",
        )
        c1, c2, c3 = st.columns(3)
        use_since = c1.checkbox("Only messages since…", value=True)
        since = c1.date_input(
            "Since",
            value=date.today() - timedelta(days=90),
            disabled=not use_since,
            label_visibility="collapsed",
        )
        limit = c2.number_input("Max messages (0 = no limit)", min_value=0, value=5000, step=1000)
        enrich_after = c3.checkbox("Run sentiment analysis afterwards", value=True)
        st.caption("Re-running a group resumes from the last fetched message.")
        if st.form_submit_button("Queue back-fill", type="primary"):
            try:
                name = parse_group_ref(group)
            except GroupRefError as exc:
                name = None
                st.error(str(exc))
            if name:
                with session_factory()() as s:
                    job = jobs.enqueue(
                        s,
                        "backfill",
                        {
                            "group": name,
                            "since": since.isoformat() if use_since else None,
                            "limit": int(limit) or None,
                            "enrich_after": enrich_after,
                        },
                    )
                st.success(f"Queued job #{job.id} for {name}")

    st.subheader("Other tasks")
    c1, c2, c3 = st.columns(3)
    with session_factory()() as s:
        if c1.button("Run sentiment analysis", help="Scores new or outdated messages"):
            st.toast(f"Queued enrichment job #{jobs.enqueue(s, 'enrich').id}")
        if c2.button("Rebuild interactions"):
            st.toast(f"Queued job #{jobs.enqueue(s, 'build_edges').id}")
        if c3.button("Load demo data", help="Synthetic community, no Telegram needed"):
            st.toast(f"Queued demo data job #{jobs.enqueue(s, 'seed').id}")

    _job_table()


@st.fragment(run_every=5)
def _job_table() -> None:
    st.subheader("Recent jobs")
    with session_factory()() as s:
        rows = jobs.recent(s)
    if not rows:
        st.info("No jobs yet.")
        return
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    if any(j.status == jobs.QUEUED and j.created_at < cutoff for j in rows):
        st.warning("Jobs have been queued for over 30 s. Is the worker running?")
    table = pd.DataFrame(
        [
            {
                "#": j.id,
                "status": j.status,
                "task": j.kind,
                "target": (j.params or {}).get("group", ""),
                "created (UTC)": j.created_at,
                "duration": (j.finished_at - j.started_at)
                if j.finished_at and j.started_at
                else None,
                "result": j.result or (j.error or "").split("\n")[0],
            }
            for j in rows
        ]
    )
    colors = {
        "done": "color: #1e8449",
        "failed": "color: #c0392b; font-weight: 600",
        "running": "color: #2471a3; font-weight: 600",
    }
    st.dataframe(
        table.style.map(lambda v: colors.get(v, ""), subset=["status"]),
        hide_index=True,
        use_container_width=True,
    )
    failed = [j for j in rows if j.status == jobs.FAILED and j.error]
    if failed:
        with st.expander(f"Error details for job #{failed[0].id}"):
            st.code(failed[0].error)


jobs_page()
