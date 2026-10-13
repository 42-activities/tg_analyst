"""Streamlit web UI: queue scraping jobs and explore community dashboards.

Run with ``tg-analyst web`` (or ``streamlit run src/tg_analyst/dashboard/app.py``).
Jobs are executed by a separate ``tg-analyst worker`` process.
"""

import streamlit as st

st.set_page_config(page_title="TG Analyst", page_icon="📊", layout="wide")

pg = st.navigation(
    [
        st.Page(
            "pages/dashboard.py", title="Dashboard", icon=":material/monitoring:", default=True
        ),
        st.Page("pages/jobs.py", title="Jobs", icon=":material/sync:", url_path="jobs"),
    ]
)
pg.run()
