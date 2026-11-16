"""GrantLens dashboard.  uv run streamlit run app.py"""

from __future__ import annotations

import os

import streamlit as st

st.set_page_config(page_title="GrantLens", page_icon="🔎", layout="wide")

# On Streamlit Community Cloud these live in the app's secrets; locally, in the environment.
for key in ("ANTHROPIC_API_KEY", "APP_PASSCODE", "MAX_QUESTIONS_PER_SESSION", "GRANTLENS_READONLY"):
    try:
        if key not in os.environ and key in st.secrets:
            os.environ[key] = str(st.secrets[key])
    except Exception:  # no secrets file locally: use the environment
        pass

from views import page_ask, page_check, page_compare, page_explore  # noqa: E402

# ------------------------------------------------------------------ shell

with st.sidebar:
    st.markdown("### GrantLens")
    st.write("AI-assisted analysis of public grant data, with the AI's accuracy measured against each funder's own categories.")
    st.caption("Independent project by Maia Griffith. Uses the public grant records of the Mott Foundation "
               "([grants database](https://www.mott.org/grants/)) and the Gates Foundation "
               "([committed grants](https://www.gatesfoundation.org/about/committed-grants)). "
               "Not affiliated with or endorsed by either foundation.")

passcode = os.environ.get("APP_PASSCODE")
if passcode and st.session_state.get("unlocked") is not True:
    st.title("GrantLens")
    if st.text_input("Access code", type="password") == passcode:
        st.session_state.unlocked = True
        st.rerun()
    st.stop()

nav = st.navigation([
    st.Page(page_compare, title="Compare funders", icon="📊", url_path="compare", default=True),
    st.Page(page_explore, title="Explore a funder", icon="🔍", url_path="explore"),
    st.Page(page_check, title="Check the AI", icon="✅", url_path="check"),
    st.Page(page_ask, title="Ask", icon="💬", url_path="ask"),
])
nav.run()
