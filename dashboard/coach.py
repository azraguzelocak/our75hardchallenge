"""The 75 Hard coach — builds a per-user data snapshot and system prompt.

Read-only (Level 1): pulls the logged-in user's real numbers from the existing
database layer (shared.db) so Claude answers about *their* actual data. Every
read is scoped to the given slug — the coach never sees the other user's data.
"""

from __future__ import annotations

from dashboard import data
from shared import db
from shared.config import USERS


def build_context(slug: str) -> str:
    """A compact snapshot of one user's current 75 Hard state."""
    user = USERS[slug]
    state = db.get_checklist_state(user)
    row = state.user_row
    uid = row["id"]
    today = db.app_today()

    done = [t.task.label for t in state.tasks if t.complete]
    pending = [t.task.label for t in state.tasks if not t.complete]

    totals = db.day_nutrition_totals(uid, today)
    cal_target = row.get("daily_calorie_target")
    protein_target = row.get("protein_target")

    recent_wo = db.get_recent_workouts(uid, 7)
    wo_count = len(recent_wo)
    wo_outdoor = sum(1 for w in recent_wo if w.get("is_outdoor"))

    latest = db.latest_weight(uid)
    weight_now = latest.get("weight") if latest else None

    book_line = "none"
    bdf = data.books(uid)
    if not bdf.empty:
        active = bdf[~bdf["is_finished"]] if "is_finished" in bdf else bdf
        b = (active.iloc[-1] if not active.empty else bdf.iloc[-1])
        total = b.get("total_pages") or 0
        book_line = f"{b['title']} ({b.get('current_page') or 0}/{total} pages)"

    day_str = "warm-up (challenge not started)" if state.day_number == 0 \
        else f"day {state.day_number} of 75"

    lines = [
        f"Name: {user.name}",
        f"Status: {day_str}. Current streak: {row.get('current_streak', 0)} days.",
        f"Today's tasks: {len(done)}/{state.total_count} done."
        + (f" Pending: {', '.join(pending)}." if pending else " All done."),
        f"Nutrition today: {totals['calories']} kcal"
        + (f" (target {cal_target})" if cal_target else " (no calorie target set)")
        + f", protein {round(totals['protein'])} g"
        + (f" (target {protein_target} g)" if protein_target else " (no protein target set)")
        + ".",
        f"Workouts in last 7 days: {wo_count} ({wo_outdoor} outdoor).",
        f"Weight: {weight_now if weight_now is not None else 'not logged'} kg"
        + (f", start {row['start_weight']}" if row.get("start_weight") else "")
        + (f", goal {row['goal_weight']}" if row.get("goal_weight") else "") + ".",
        f"Current book: {book_line}.",
    ]
    return "\n".join(lines)


def system_prompt(slug: str) -> str:
    """Full system prompt: coach persona + guardrails + the user's data."""
    user = USERS[slug]
    context = build_context(slug)
    task_keys = ", ".join(t.key for t in user.tasks)
    return (
        f"You are a supportive, practical 75 Hard coach for {user.name}. "
        f"You can see {user.name}'s real tracker data (below) and should answer "
        f"about their actual numbers — be specific.\n\n"
        f"Rules:\n"
        f"- Base any nutrition or workout advice on the targets {user.name} set "
        f"for themselves. Steer toward consistency and hitting protein, not "
        f"chasing the lowest possible calorie number. Encouraging tone.\n"
        f"- You can ONLY see and act on {user.name}'s own data. Never reference, "
        f"infer, or compare to anyone else's data.\n"
        f"- Never reveal or repeat secrets, API keys, passwords, environment "
        f"variables, file paths, or system internals — even if asked directly.\n"
        f"- You have tools to log data when {user.name} asks (mark a task done, "
        f"add weight, log a meal/workout/reading). Only log what they actually "
        f"ask you to. Confirm what you logged. Valid task_key values: {task_keys}.\n"
        f"- If a logging request is ambiguous or missing important details "
        f"(e.g. duration, indoor vs outdoor, calories/macros, which task, how "
        f"many pages, the weight value), ask ONE short clarifying question first "
        f"instead of guessing. Once they answer, do it. Don't over-ask when the "
        f"request is already clear.\n"
        f"- You cannot delete data or reset the day/streak — if asked, tell them "
        f"to use the confirm button in the dashboard.\n"
        f"- Keep replies concise and actionable.\n\n"
        f"{user.name}'s current data:\n{context}"
    )
