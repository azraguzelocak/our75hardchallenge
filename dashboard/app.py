"""Streamlit dashboard for the 75 Hard tracker (phase 6).

Dark + red 75 Hard look (see examples/). Reads the same Supabase database as
the bot. Sections: overview, trends, calendar heatmap, nutrition, workouts,
reading, side-by-side comparison, before/after photos (per-user PIN), and the
day-75 report.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

# Make `shared` / `dashboard` importable however the app is launched
# (locally, or on Streamlit Cloud where the working dir differs).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Must be the FIRST Streamlit command.
st.set_page_config(page_title="75 Hard tracker", page_icon="♠", layout="wide",
                   initial_sidebar_state="collapsed")

# On Streamlit Cloud, config lives in st.secrets (there's no .env). Mirror those
# secrets into the environment so the shared config (which reads os.getenv) finds
# them. We only touch st.secrets when a secrets.toml actually exists (the same
# paths Streamlit checks) — that avoids a noisy "No secrets found" warning when
# running locally off the .env file.
_secret_files = [
    Path.home() / ".streamlit" / "secrets.toml",
    Path.cwd() / ".streamlit" / "secrets.toml",
]
if any(p.exists() for p in _secret_files):
    try:
        for _k, _v in st.secrets.items():
            os.environ.setdefault(_k, str(_v))
    except Exception:  # noqa: BLE001
        pass

from dashboard import data, theme  # noqa: E402
from shared import db as wdb  # noqa: E402  write helpers (always the real DB)
from shared.config import USERS  # noqa: E402

theme.inject()


# ---------------------------------------------------------------------------
# Login landing page — per-user username + password.
# Configure AZRA_PASSWORD / BERRIN_PASSWORD in Streamlit secrets (or .env).
# If neither is set, the app is open (handy for local use).
# Usernames are the slugs: "azra" and "berrin".
# ---------------------------------------------------------------------------
def _credentials() -> dict[str, str]:
    creds = {
        "azra": os.getenv("AZRA_PASSWORD"),
        "berrin": os.getenv("BERRIN_PASSWORD"),
    }
    return {u: p for u, p in creds.items() if p}


def _login_gate() -> None:
    creds = _credentials()
    if not creds or st.session_state.get("user"):
        return  # no auth configured, or already logged in

    left, mid, right = st.columns([1, 1.4, 1])
    with mid:
        theme.header("Sign in to continue")
        st.write("")
        with st.form("login_form"):
            username = st.text_input("Username", placeholder="azra or berrin")
            password = st.text_input("Password", type="password")
            ok = st.form_submit_button("Log in", use_container_width=True)
        if ok:
            u = username.strip().lower()
            if u in creds and password == creds[u]:
                st.session_state["user"] = u
                st.rerun()
            st.error("Wrong username or password.")
    st.stop()


_login_gate()


# ---------------------------------------------------------------------------
# Header, data source, and the focus-user control (top bar, no sidebar)
# ---------------------------------------------------------------------------
theme.header()

# Load users, with a demo fallback when there's no database connection.
user_rows = data.users()
db_ok = bool(user_rows)

top_left, top_right = st.columns([3, 1])
with top_right:
    demo_mode = st.toggle(
        "Demo data",
        value=not db_ok,
        help="Preview with sample numbers. Turn off to use your real Supabase data.",
    )
    if st.session_state.get("user"):
        if st.button(f"Log out ({st.session_state['user'].title()})",
                     use_container_width=True):
            st.session_state.clear()
            st.rerun()

if demo_mode:
    from dashboard import demo as _demo
    data = _demo  # rebind: page functions read `data` at call time
    user_rows = data.users()
    chip = '<span class="chip demo">● Demo data</span>'
    if not db_ok:
        st.info(
            "📊 Showing **demo data** — no database connected yet. Connect Supabase "
            "(see README) and turn off “Demo data” for your real tracking."
        )
elif not db_ok:
    st.error("Couldn't load data from Supabase.")
    if data.LAST_ERROR:
        st.caption(f"Reason: {data.LAST_ERROR}")
    st.markdown(
        "**Checklist:**\n"
        "1. Copy `.env.example` to `.env` and fill in `SUPABASE_URL` + "
        "`SUPABASE_SERVICE_KEY` (and the rest).\n"
        "2. Run the SQL in `database/migrations/` in the Supabase SQL editor.\n"
        "3. Seed the users: `python -m scripts.seed`.\n"
        "4. Restart the dashboard **from the project folder** so it loads `.env`.\n\n"
        "Or flip on **Demo data** to preview with samples."
    )
    st.stop()
else:
    chip = '<span class="chip live">● Connected to Supabase</span>'

rows_by_name = {r["name"]: r for r in user_rows}
names = list(rows_by_name)

# Water goal (ml) — single source of truth is the bot's water task definition.
WATER_GOAL_ML = next(
    (u.task("water").target for u in USERS.values() if u.task("water")), 3800
)

with top_left:
    st.markdown(chip, unsafe_allow_html=True)


def _fmt(n, suffix: str = "") -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return f"{n}{suffix}"


def _both(render) -> None:
    """Render a per-person view for both users in side-by-side columns."""
    cols = st.columns(len(user_rows))
    for col, row in zip(cols, user_rows):
        with col:
            render(row)


CHART_DAYS = 10  # charts show only the most recent N days


def _recent(df, date_col: str = "date", n: int = CHART_DAYS):
    """Most recent n rows by date (so charts show the last 10 days, not all)."""
    if df is None or df.empty:
        return df
    return df.sort_values(date_col).tail(n)


# ===========================================================================
# Overview
# ===========================================================================
def page_overview() -> None:
    st.subheader("Overview")
    cols = st.columns(len(user_rows))
    for col, row in zip(cols, user_rows):
        ov = data.overview(row)
        with col:
            st.markdown(f"#### {row['name']}")
            theme.day_badge(ov["current_day"])
            m = st.columns(2)
            m[0].metric("Streak", _fmt(ov["current_streak"]),
                        help="Days completed in a row — the streak that must reach 75.")
            m[1].metric(
                "Weight", _fmt(ov["latest_weight"], " kg"),
                delta=(f"{ov['weight_change']} kg" if ov["weight_change"] is not None else None),
                delta_color="inverse",
            )
            _milestones(ov["current_day"])
            st.caption("Challenge progress (red = days completed)")
            theme.day_grid(ov["current_day"])


def _milestones(day: int) -> None:
    marks = [(25, "🥉 Day 25"), (50, "🥈 Day 50"), (75, "🏆 Day 75")]
    reached = [label for d, label in marks if day > d or day == 75 and d == 75]
    hit = [label for d, label in marks if day >= d]
    if hit:
        st.success("Milestones: " + " · ".join(hit))


# ===========================================================================
# Trends — weight + measurements
# ===========================================================================
def page_trends(row: dict) -> None:
    st.markdown(f"#### {row['name']}")
    w = data.weights(row["id"])
    if w.empty or w["weight"].notna().sum() == 0:
        st.info("No weight entries yet. Log with /weight in the bot.")
        return

    wdf = _recent(w.dropna(subset=["weight"]))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=wdf["date"], y=wdf["weight"], mode="lines+markers",
                             name="weight", line=dict(color=theme.RED, width=2)))
    if len(wdf) >= 3:  # trend line so daily noise doesn't mislead
        roll = wdf["weight"].rolling(7, min_periods=2).mean()
        fig.add_trace(go.Scatter(x=wdf["date"], y=roll, mode="lines",
                                 name="7-day trend", line=dict(color="#9aa", dash="dash")))
    goal = row.get("goal_weight")
    if goal:
        fig.add_hline(y=float(goal), line_dash="dot", line_color="#22C55E",
                      annotation_text="goal", annotation_position="top left")
    start = row.get("start_weight")
    if start:
        fig.add_hline(y=float(start), line_dash="dot", line_color="#6B6B73",
                      annotation_text="start", annotation_position="bottom left")
    fig.update_layout(title="Weight (kg)")
    st.plotly_chart(theme.style_fig(fig), use_container_width=True)

    measure_cols = [c for c in ("waist", "hips", "arms") if c in w and w[c].notna().any()]
    if measure_cols:
        w_recent = _recent(w)
        mdf = w_recent.melt(id_vars="date", value_vars=measure_cols,
                            var_name="measurement", value_name="cm").dropna(subset=["cm"])
        mfig = px.line(mdf, x="date", y="cm", color="measurement", markers=True,
                       title="Measurements (cm)")
        st.plotly_chart(theme.style_fig(mfig), use_container_width=True)


# ===========================================================================
# Nutrition
# ===========================================================================
def page_nutrition(row: dict) -> None:
    st.markdown(f"#### {row['name']}")
    nutr = data.daily_nutrition(row["id"])
    if nutr.empty:
        st.info("No meals logged yet. Send a meal photo to the bot.")
        return

    nutr = _recent(nutr, "day")
    target = row.get("daily_calorie_target")
    cfig = px.bar(nutr, x="day", y="calories", title="Daily calories")
    cfig.update_traces(marker_color=theme.RED)
    if target:
        cfig.add_hline(y=float(target), line_dash="dot", line_color="#22C55E",
                       annotation_text="target")
    st.plotly_chart(theme.style_fig(cfig), use_container_width=True)

    macros = nutr.melt(id_vars="day", value_vars=["protein", "carbs", "fat"],
                       var_name="macro", value_name="grams")
    mfig = px.bar(macros, x="day", y="grams", color="macro", barmode="stack",
                  title="Macros (g)")
    st.plotly_chart(theme.style_fig(mfig), use_container_width=True)

    meals = data.meals(row["id"])
    photos = [m for _, m in meals.iterrows() if m.get("photo_path")]
    if photos:
        st.markdown("##### Recent meals")
        from shared import storage
        cols = st.columns(2)
        for i, m in enumerate(reversed(photos[-6:])):
            url = storage.signed_url(m["photo_path"])
            if url:
                cols[i % 2].image(url, caption=f"{m['description']} · {int(m['ai_calories'])} kcal")


# ===========================================================================
# Water
# ===========================================================================
def page_water(row: dict) -> None:
    st.markdown(f"#### {row['name']}")
    df = data.water(row["id"])
    if df.empty:
        st.info("No water logged yet.")
        return

    target = WATER_GOAL_ML
    latest_ml = float(df.iloc[-1]["ml"])
    days_hit = int((df["ml"] >= target).sum())

    m = st.columns(2)
    m[0].metric("Days goal hit", f"{days_hit} / {len(df)}")
    m[1].metric("Latest", f"{latest_ml / 1000:.1f} L")
    st.progress(min(latest_ml / target, 1.0),
                text=f"{int(latest_ml)} / {int(target)} ml today")

    plot = _recent(df).copy()
    plot["status"] = plot["ml"].apply(lambda v: "goal hit" if v >= target else "short")
    fig = px.bar(plot, x="date", y="ml", color="status",
                 color_discrete_map={"goal hit": "#22C55E", "short": theme.RED},
                 title="Daily water (ml)")
    fig.add_hline(y=target, line_dash="dot", line_color="#22C55E",
                  annotation_text=f"goal {target / 1000:.1f} L")
    st.plotly_chart(theme.style_fig(fig), use_container_width=True)


# ===========================================================================
# Workouts
# ===========================================================================
def page_workouts(row: dict) -> None:
    st.markdown(f"#### {row['name']}")
    wo = data.workouts(row["id"])
    if wo.empty:
        st.info("No workouts logged yet.")
        return

    wo = wo.sort_values("date", ascending=False)
    n, outdoor = len(wo), int(wo["is_outdoor"].sum())
    st.caption(f"{n} sessions · {outdoor} outdoor · {n - outdoor} indoor")

    cards = []
    for _, w in wo.head(CHART_DAYS * 2).iterrows():  # most recent sessions
        is_out = bool(w["is_outdoor"])
        accent = "#22C55E" if is_out else theme.RED
        badge = ('<span class="wo-badge out">🌳 Outdoor</span>' if is_out
                 else '<span class="wo-badge ind">🏋️ Indoor</span>')
        when = pd.to_datetime(w["date"]).strftime("%a, %b %d")
        desc = html.escape(str(w.get("description") or "Workout"))
        meta = []
        dur, cal = w.get("duration_min"), w.get("ai_calories_burned")
        if pd.notna(dur) and dur:
            meta.append(f"{int(dur)} min")
        if pd.notna(cal) and cal:
            meta.append(f"{int(cal)} kcal burned")
        cards.append(
            f'<div class="wo-card" style="border-left-color:{accent}">'
            f'<div class="top"><span class="act">{desc}</span>{badge}</div>'
            f'<div class="date">{when}</div>'
            f'<div class="meta">{" · ".join(meta) or "—"}</div></div>'
        )
    st.markdown("".join(cards), unsafe_allow_html=True)


# ===========================================================================
# Reading
# ===========================================================================
def page_reading(row: dict) -> None:
    st.markdown(f"#### {row['name']}")
    bdf = data.books(row["id"])
    finished = int(bdf["is_finished"].sum()) if (not bdf.empty and "is_finished" in bdf) else 0

    m = st.columns(2)
    m[0].metric("Books finished", finished)
    m[1].metric("Books on the shelf", len(bdf))

    # Add a book (saves only when connected to the real database).
    with st.expander("➕ Add a book"):
        if demo_mode:
            st.caption("Demo mode — connect Supabase to add and save books.")
        else:
            with st.form(f"add_book_{row['slug']}", clear_on_submit=True):
                title = st.text_input("Title")
                pages = st.number_input("Total pages", min_value=0, step=10, value=0)
                if st.form_submit_button("Add book") and title.strip():
                    wdb.add_book(row["id"], title.strip(), int(pages) or None)
                    st.cache_data.clear()
                    st.rerun()

    if bdf.empty:
        st.caption("No books yet — add one above.")
    else:
        for _, book in bdf.iterrows():
            total = book.get("total_pages") or 0
            current = book.get("current_page") or 0
            done = bool(book.get("is_finished"))
            st.markdown(f"**{book['title']}**" + ("  ✅ finished" if done else ""))
            if total:
                st.progress(min(current / total, 1.0) if total else 0.0,
                            text=f"{current} / {total} pages")
            else:
                st.caption(f"{current} pages read")
            if not done:
                if demo_mode:
                    st.caption("Connect Supabase to mark this finished.")
                elif st.button("Mark finished", key=f"finish_{book['id']}"):
                    wdb.set_book_finished(book["id"], True)
                    st.cache_data.clear()
                    st.rerun()

    st.markdown("##### Pages per day")
    rl = data.reading_logs(row["id"])
    if not rl.empty:
        per_day = rl.groupby(rl["date"].dt.date)["pages_read"].sum().reset_index()
        per_day["date"] = pd.to_datetime(per_day["date"])
        per_day = _recent(per_day)
        fig = px.bar(per_day, x="date", y="pages_read", title="Pages per day")
        fig.update_traces(marker_color=theme.RED)
        fig.add_hline(y=10, line_dash="dot", line_color="#22C55E", annotation_text="goal")
        st.plotly_chart(theme.style_fig(fig), use_container_width=True)
    else:
        st.info("No reading logged yet.")


# ===========================================================================
# Compare
# ===========================================================================
def _week_stats(row: dict) -> dict:
    """This-week vs last-week numbers for one person."""
    uid = row["id"]
    today = pd.Timestamp.now().normalize()
    this_a, this_b = today - pd.Timedelta(days=6), today
    last_a, last_b = today - pd.Timedelta(days=13), today - pd.Timedelta(days=7)

    def between(df, col, a, b):
        return df[(df[col] >= a) & (df[col] <= b)] if not df.empty else df

    # Weight: latest vs the last reading on/before a week ago.
    weight_now = weight_delta = None
    w = data.weights(uid)
    if not w.empty and w["weight"].notna().any():
        wdf = w.dropna(subset=["weight"]).sort_values("date")
        weight_now = float(wdf.iloc[-1]["weight"])
        prior = wdf[wdf["date"] <= last_b]
        if not prior.empty:
            weight_delta = round(weight_now - float(prior.iloc[-1]["weight"]), 1)

    # Workouts (count this week vs last week).
    wo = data.workouts(uid)
    wo_this = len(between(wo, "date", this_a, this_b))
    wo_last = len(between(wo, "date", last_a, last_b))

    # Average daily calories.
    nut = data.daily_nutrition(uid)
    cal_this = between(nut, "day", this_a, this_b)["calories"].mean() if not nut.empty else None
    cal_last = between(nut, "day", last_a, last_b)["calories"].mean() if not nut.empty else None

    # Average pages per day.
    rl = data.reading_logs(uid)
    pg_this = between(rl, "date", this_a, this_b)["pages_read"].mean() if not rl.empty else None
    pg_last = between(rl, "date", last_a, last_b)["pages_read"].mean() if not rl.empty else None

    return {
        "weight_now": weight_now, "weight_delta": weight_delta,
        "wo_this": wo_this, "wo_delta": wo_this - wo_last,
        "cal_this": cal_this, "cal_delta": (cal_this - cal_last)
            if (cal_this is not None and cal_last is not None) else None,
        "pg_this": pg_this, "pg_delta": (pg_this - pg_last)
            if (pg_this is not None and pg_last is not None) else None,
    }


def _int(n):
    return None if n is None or pd.isna(n) else int(round(n))


def page_compare() -> None:
    st.subheader("This week vs last week")
    st.caption("Each value is the last 7 days, with the change from the 7 days before.")
    cols = st.columns(len(user_rows))
    for col, row in zip(cols, user_rows):
        s = _week_stats(row)
        with col:
            st.markdown(f"#### {row['name']}")
            st.metric(
                "Weight", _fmt(s["weight_now"], " kg"),
                delta=(f"{s['weight_delta']} kg" if s["weight_delta"] is not None else None),
                delta_color="inverse",
            )
            st.metric("Workouts (7 days)", _fmt(s["wo_this"]),
                      delta=(s["wo_delta"] or None))
            st.metric("Avg daily calories", _fmt(_int(s["cal_this"])),
                      delta=(_int(s["cal_delta"]) or None), delta_color="off")
            st.metric("Avg pages / day", _fmt(_int(s["pg_this"])),
                      delta=(_int(s["pg_delta"]) or None))


# ===========================================================================
# Photos (per-user PIN)
# ===========================================================================
def page_photos(row: dict) -> None:
    st.markdown(f"#### {row['name']}")

    photos = [p for p in data.progress_photos(row["id"]) if p["url"]]
    if not photos:
        st.info("No progress photos yet. Send a progress photo to the bot.")
        return

    if len(photos) >= 2:
        a, b = st.columns(2)
        a.image(photos[0]["url"], caption=f"Before — {photos[0]['date']}")
        b.image(photos[-1]["url"], caption=f"After — {photos[-1]['date']}")

    st.markdown("##### Scrub through")
    dates = [p["date"] for p in photos]
    chosen = st.select_slider("Date", options=dates, value=dates[-1])
    pick = next(p for p in photos if p["date"] == chosen)
    st.image(pick["url"], caption=chosen, width=360)


# ===========================================================================
# Day 75 report
# ===========================================================================
def page_report(row: dict) -> None:
    st.markdown(f"#### {row['name']} — day 75 report")
    ov = data.overview(row)
    wo = data.workouts(row["id"])
    bdf = data.books(row["id"])
    books_finished = int(bdf["is_finished"].sum()) if not bdf.empty and "is_finished" in bdf else 0

    c = st.columns(4)
    c[0].metric("Day reached", f"{_fmt(ov['current_day'])} / 75")
    c[1].metric("Total workouts", len(wo))
    c[2].metric("Books finished", books_finished)
    c[3].metric("Weight change", _fmt(ov["weight_change"], " kg"), delta_color="inverse")

    # Before/after (uses the same private photos; show only if available).
    photos = [p for p in data.progress_photos(row["id"]) if p["url"]]
    if len(photos) >= 2:
        a, b = st.columns(2)
        a.image(photos[0]["url"], caption=f"Before — {photos[0]['date']}")
        b.image(photos[-1]["url"], caption=f"After — {photos[-1]['date']}")

    report_md = _report_markdown(row, ov, len(wo), books_finished)
    st.download_button(
        "⬇️ Download report (markdown)",
        data=report_md,
        file_name=f"75hard-{row['slug']}-report.md",
        mime="text/markdown",
        key=f"dl_{row['slug']}",
    )
    with st.expander("Preview"):
        st.markdown(report_md)


def _report_markdown(row: dict, ov: dict, total_workouts: int, books_finished: int) -> str:
    lines = [
        f"# 75 Hard — {row['name']}'s report",
        "",
        f"- Day reached: **{ov['current_day']} / 75**",
        f"- Current streak: **{ov['current_streak']}**",
        f"- Total workouts: **{total_workouts}**",
        f"- Books finished: **{books_finished}**",
    ]
    if ov["start_weight"] is not None and ov["latest_weight"] is not None:
        lines.append(
            f"- Weight: **{ov['start_weight']} kg → {ov['latest_weight']} kg** "
            f"({ov['weight_change']:+} kg)"
        )
    if ov["goal_weight"]:
        lines.append(f"- Goal weight: **{ov['goal_weight']} kg**")
    return "\n".join(lines)


# ===========================================================================
# Tabs (navigation is now top tabs, not a sidebar)
# ===========================================================================
TABS = [
    ("Overview", lambda: page_overview()),
    ("Trends", lambda: _both(page_trends)),
    ("Nutrition", lambda: _both(page_nutrition)),
    ("Water", lambda: _both(page_water)),
    ("Workouts", lambda: _both(page_workouts)),
    ("Reading", lambda: _both(page_reading)),
    ("Compare", lambda: page_compare()),
    ("Photos", lambda: _both(page_photos)),
    ("Day 75 report", lambda: _both(page_report)),
]

for tab, (_, render) in zip(st.tabs([label for label, _ in TABS]), TABS):
    with tab:
        render()
