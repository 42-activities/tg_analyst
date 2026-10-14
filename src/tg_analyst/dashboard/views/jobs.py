"""Jobs page: queue scraping and analysis tasks for the worker."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tg_analyst import jobs
from tg_analyst.config import get_settings
from tg_analyst.dashboard.common import escape_md, session_factory
from tg_analyst.ingest.groupref import GroupRefError, parse_group_ref
from tg_analyst.storage.models import ExcludedUser, Group, User


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

    _sync_controls()
    _job_table()
    _exclusions()


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
                "target": (j.params or {}).get("group", "")
                + (" (auto)" if (j.params or {}).get("scheduled") else ""),
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


def _sync_controls() -> None:
    settings = get_settings()
    st.subheader("Sync with Telegram")
    auto = []
    if settings.auto_refresh_minutes:
        auto.append(f"new messages every {settings.auto_refresh_minutes} min")
    if settings.auto_reconcile_hours:
        auto.append(
            f"edits and deletions from the last {settings.reconcile_days} days every "
            f"{settings.auto_reconcile_hours} h"
        )
    st.caption(
        ("The worker fetches " + " and ".join(auto) + " automatically, for every group. ")
        if auto
        else "Automatic sync is off (AUTO_REFRESH_MINUTES / AUTO_RECONCILE_HOURS). "
    )
    with session_factory()() as s:
        groups = s.execute(select(Group.group_id, Group.title)).all()
    if not groups:
        return
    with st.form("sync"):
        c1, c2 = st.columns([2, 2])
        group = c1.selectbox("Group", groups, format_func=lambda g: g.title or str(g.group_id))
        scope = c2.radio(
            "Re-check",
            ["Last 30 days", "Whole history"],
            horizontal=True,
            help="Whole history makes one Telegram request per 100 stored messages, plus one "
            "per message whose reactor list is incomplete. Run it once to import reactions "
            "for messages stored before reactions were collected.",
        )
        if st.form_submit_button("Check for edits, deletions and reactions now"):
            with session_factory()() as s:
                job = jobs.enqueue(
                    s,
                    "sync",
                    {
                        "group": str(group.group_id),
                        "days": 30 if scope == "Last 30 days" else None,
                    },
                )
            st.success(f"Queued job #{job.id}")


def _exclusions() -> None:
    st.subheader("Privacy: excluded members")
    st.caption(
        "Excluded members' messages are deleted from the database and skipped by every future "
        "back-fill, so they never appear in the analysis. Replies and mentions pointing at them "
        "are removed too. Use a numeric Telegram id or the @username of someone already seen."
    )
    with st.form("exclude", clear_on_submit=True):
        c1, c2 = st.columns([1, 2])
        who = c1.text_input("Member", placeholder="@username or numeric id")
        note = c2.text_input("Note (optional)", placeholder="e.g. my own account")
        if st.form_submit_button("Exclude member"):
            if not who.strip():
                st.error("Enter a username or id.")
            else:
                with session_factory()() as s:
                    job = jobs.enqueue(s, "exclude", {"user": who.strip(), "note": note or None})
                st.success(f"Queued job #{job.id}: the worker removes their data in a few seconds.")

    with session_factory()() as s:
        rows = s.execute(
            select(
                ExcludedUser.user_id,
                ExcludedUser.note,
                ExcludedUser.created_at,
                User.username,
                User.display_name,
            )
            .outerjoin(User, User.user_id == ExcludedUser.user_id)
            .order_by(ExcludedUser.created_at)
        ).all()
    if not rows:
        st.info("Nobody is excluded.")
        return
    for r in rows:
        c1, c2, c3 = st.columns([3, 3, 1])
        label = f"@{r.username}" if r.username else (r.display_name or "unknown name")
        c1.markdown(f"**{escape_md(label)}** · id `{r.user_id}`")
        c2.caption(f"{escape_md(r.note or '')} · since {r.created_at:%Y-%m-%d}")
        if c3.button(
            "Include again",
            key=f"include_{r.user_id}",
            help="Old messages return only with a full re-scrape",
        ):
            with session_factory()() as s:
                jobs.enqueue(s, "include", {"user": str(r.user_id)})
            st.toast("Queued: the exclusion will be lifted")


jobs_page()
