"""Visual theme for the dashboard — dark + red 75 Hard look.

CSS injection, the spade "75" header, the 75-day completion grid, and a
GitHub-style calendar heatmap. Colors mirror the reference screenshots.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

RED = "#E50914"
BG = "#0B0B0D"
CARD = "#16161A"
MUTED = "#6B6B73"

_CSS = f"""
<style>
  .stApp {{ background: {BG}; }}
  /* Spade header */
  .hard-header {{ display:flex; align-items:center; gap:14px; margin-bottom:4px; }}
  .hard-spade {{
     font-size:2.4rem; color:{RED};
     filter: drop-shadow(0 0 8px rgba(229,9,20,0.5));
  }}
  .hard-title {{ font-size:2rem; font-weight:900; letter-spacing:2px; color:#fff;
     font-style:italic; text-transform:uppercase; }}
  .hard-sub {{ color:{MUTED}; letter-spacing:.18em; text-transform:uppercase;
     font-size:.75rem; }}
  /* Big day number */
  .day-badge {{ font-style:italic; font-weight:900; color:{RED};
     font-size:3.2rem; line-height:1; letter-spacing:1px; }}
  /* 75-day grid */
  .grid {{ display:grid; grid-template-columns:repeat(15, 1fr); gap:6px; max-width:520px; }}
  .cell {{ aspect-ratio:1; border-radius:5px; background:#202027;
     display:flex; align-items:center; justify-content:center;
     font-size:.6rem; color:{MUTED}; }}
  .cell.done {{ background:{RED}; color:#fff; font-weight:700;
     box-shadow:0 0 6px rgba(229,9,20,0.45); }}
  .cell.active {{ outline:2px solid {RED}; color:#fff; font-weight:700; }}
  /* Section dividers */
  h2, h3 {{ color:#fff; }}

  /* --- Sidebar brand --- */
  .side-brand {{ display:flex; align-items:center; gap:10px; padding:4px 2px 10px;
     font-size:1.5rem; font-weight:900; font-style:italic; letter-spacing:1px;
     color:#fff; text-transform:uppercase; }}
  .side-brand .sp {{ color:{RED}; filter:drop-shadow(0 0 6px rgba(229,9,20,.55)); }}
  .side-label {{ color:{MUTED}; text-transform:uppercase; letter-spacing:.14em;
     font-size:.7rem; margin:6px 2px 2px; }}

  /* --- Sidebar nav as accented pills --- */
  section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:2px; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
     width:100%; padding:9px 12px; border-radius:10px; cursor:pointer;
     border-left:3px solid transparent; transition:background .15s, border-color .15s; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
     background:#1B1B21; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{
     background:rgba(229,9,20,.14); border-left:3px solid {RED}; }}
  /* hide the round radio marker, keep just the labelled pill */
  section[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {{
     display:none; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] label p {{
     font-weight:600; font-size:.95rem; }}

  /* --- Status chip --- */
  .chip {{ display:inline-flex; align-items:center; gap:7px; padding:5px 11px;
     border-radius:999px; font-size:.78rem; font-weight:600; }}
  .chip.live {{ background:rgba(34,197,94,.15); color:#22C55E; }}
  .chip.demo {{ background:rgba(229,9,20,.15); color:{RED}; }}

  /* --- Workout activity cards --- */
  .wo-card {{ background:{CARD}; border-left:4px solid {RED}; border-radius:10px;
     padding:10px 14px; margin-bottom:8px; }}
  .wo-card .top {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
  .wo-card .act {{ font-weight:700; color:#fff; font-size:.98rem; }}
  .wo-card .date {{ color:{MUTED}; font-size:.76rem; margin-top:1px; }}
  .wo-card .meta {{ color:#9AA0AA; font-size:.82rem; margin-top:4px; }}
  .wo-badge {{ font-size:.68rem; font-weight:700; padding:2px 9px; border-radius:999px;
     white-space:nowrap; }}
  .wo-badge.out {{ background:rgba(34,197,94,.15); color:#22C55E; }}
  .wo-badge.ind {{ background:rgba(229,9,20,.15); color:{RED}; }}

  /* --- Bigger tabs --- */
  .stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid #26262c; }}
  .stTabs [data-baseweb="tab"] {{
     height:auto; padding:14px 22px; font-size:1.1rem; font-weight:700;
     color:{MUTED}; }}
  .stTabs [data-baseweb="tab"]:hover {{ color:#fff; background:#16161A; border-radius:8px 8px 0 0; }}
  .stTabs [aria-selected="true"] {{ color:#fff; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background-color:{RED}; height:3px; }}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def header(subtitle: str = "Two-person challenge tracker") -> None:
    st.markdown(
        f'<div class="hard-header"><span class="hard-spade">♠</span>'
        f'<span class="hard-title">75 Hard</span></div>'
        f'<div class="hard-sub">{subtitle}</div>',
        unsafe_allow_html=True,
    )


def day_badge(day: int) -> None:
    st.markdown(f'<div class="day-badge">DAY {day}</div>', unsafe_allow_html=True)


def day_grid(current_day: int) -> None:
    """Render the 75-day completion grid (red = completed, outline = today)."""
    cells = []
    for d in range(1, 76):
        if d < current_day:
            cls = "cell done"
        elif d == current_day:
            cls = "cell active"
        else:
            cls = "cell"
        cells.append(f'<div class="{cls}">{d}</div>')
    st.markdown(f'<div class="grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def calendar_heatmap(logs: pd.DataFrame, title: str) -> go.Figure | None:
    """GitHub-contributions-style heatmap: green where the day passed."""
    if logs.empty:
        return None

    df = logs.copy()
    df["date"] = pd.to_datetime(df["date"])
    start = df["date"].min().normalize()
    end = df["date"].max().normalize()
    all_days = pd.date_range(start, end, freq="D")
    passed = set(df.loc[df["day_passed"], "date"].dt.normalize())
    logged = set(df["date"].dt.normalize())

    # Grid: x = week index from start, y = weekday (Mon..Sun)
    weeks, weekdays, colors, hover = [], [], [], []
    for day in all_days:
        week = (day - start).days // 7
        weekday = day.weekday()
        if day in passed:
            val = 2
        elif day in logged:
            val = 1
        else:
            val = 0
        weeks.append(week)
        weekdays.append(weekday)
        colors.append(val)
        label = {2: "completed", 1: "logged, missed", 0: "no activity"}[val]
        hover.append(f"{day.date()} — {label}")

    fig = go.Figure(
        go.Scatter(
            x=weeks, y=weekdays, mode="markers",
            marker=dict(
                size=16, symbol="square",
                color=colors, cmin=0, cmax=2,
                colorscale=[[0, "#202027"], [0.5, "#5c1f24"], [1.0, "#22C55E"]],
            ),
            text=hover, hoverinfo="text",
        )
    )
    fig.update_yaxes(
        tickvals=[0, 1, 2, 3, 4, 5, 6],
        ticktext=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        autorange="reversed", showgrid=False, zeroline=False,
    )
    # Label each week column with the date it starts on (e.g. "Jun 01").
    week_ticks = sorted(set(weeks))
    week_labels = [(start + pd.Timedelta(days=w * 7)).strftime("%b %d") for w in week_ticks]
    fig.update_xaxes(
        tickvals=week_ticks, ticktext=week_labels,
        showgrid=False, zeroline=False, title=None,
    )
    fig.update_layout(
        title=title, template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=260, margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def style_fig(fig: go.Figure, height: int = 360) -> go.Figure:
    """Apply the dark transparent look to a plotly figure."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=height, margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", y=1.1),
    )
    return fig
