"""Phase 3 handlers — AI food/workout logging, suggestions, summaries.

Photos are sent to the bot, which asks whether they're a meal or a workout,
then calls the Anthropic API for an estimate. Text workouts go through
/logworkout. /workout asks the AI for today's plan; /summary writes the
end-of-day recap; /target lets each user set their own nutrition numbers.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import ai
from bot.auth import restricted
from shared import db, storage
from shared.config import UserConfig, the_other_user

log = logging.getLogger("bot.ai_handlers")


# ---------------------------------------------------------------------------
# Photo received -> ask what it is
# ---------------------------------------------------------------------------
@restricted
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """Stash the photo and ask whether it's a meal or a workout."""
    photo = update.message.photo[-1]  # largest size
    context.user_data["pending_photo"] = {
        "file_id": photo.file_id,
        "caption": update.message.caption,
    }
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🍽 Meal", callback_data="photo|meal"),
                InlineKeyboardButton("🏋️ Workout", callback_data="photo|workout"),
            ],
            [InlineKeyboardButton("📸 Progress photo", callback_data="photo|progress")],
        ]
    )
    await update.message.reply_text("What's in this photo?", reply_markup=keyboard)


async def _download(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


# ---------------------------------------------------------------------------
# Callback router for AI features (patterns: photo| meal| wk|)
# ---------------------------------------------------------------------------
@restricted
async def on_ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    head = parts[0]

    if head == "photo":
        await _handle_photo_choice(update, context, user, parts[1])
    elif head == "meal":
        await _handle_meal_action(update, context, user, parts[1], parts[2] if len(parts) > 2 else "")
    elif head == "wk":
        await _handle_workout_save(update, context, user, parts[2])
    else:
        await query.answer()


# ---- meal / workout from a photo -----------------------------------------
async def _handle_photo_choice(update, context, user, kind: str) -> None:
    query = update.callback_query
    pending = context.user_data.get("pending_photo")
    if not pending:
        await query.answer("Please send the photo again.", show_alert=True)
        return

    await query.answer("Working on it…")
    busy = "📸 Saving your progress photo…" if kind == "progress" else "🔎 Analyzing your photo…"
    await query.edit_message_text(busy)
    image_bytes = await _download(context, pending["file_id"])
    user_id = await asyncio.to_thread(db.get_user_id, user.slug)

    if kind == "meal":
        await _log_meal(query, user, user_id, image_bytes, caption=pending.get("caption"))
    elif kind == "progress":
        await _log_progress(query, context, user, user_id, image_bytes, pending["file_id"])
    else:
        await _prepare_workout(query, context, image_bytes=image_bytes)

    context.user_data.pop("pending_photo", None)


async def _log_meal(query, user, user_id, image_bytes, *, caption) -> None:
    est = await asyncio.to_thread(ai.estimate_meal, image_bytes, caption=caption)

    # Best-effort photo upload (works without storage configured — returns None).
    today = db.app_today()
    path = f"{user.slug}/meals/{today.isoformat()}-{query.message.message_id}.jpg"
    photo_path = await asyncio.to_thread(storage.upload_photo, image_bytes, path)

    meal = await asyncio.to_thread(
        db.add_meal, user_id, today,
        description=est["description"], calories=est["calories"],
        protein=est["protein"], carbs=est["carbs"], fat=est["fat"],
        photo_path=photo_path,
    )
    totals = await asyncio.to_thread(db.day_nutrition_totals, user_id, today)
    user_row = await asyncio.to_thread(db.get_user_row, user.slug)
    target = user_row.get("daily_calorie_target")

    text = _meal_card(est, totals, target)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⭐ Save as favorite", callback_data=f"meal|fav|{meal['id']}")]]
    )
    await query.edit_message_text(text, reply_markup=keyboard)


async def _log_progress(query, context, user, user_id, image_bytes, file_id) -> None:
    """Save the daily progress photo, tick the task, and share with the other user."""
    today = db.app_today()
    path = f"{user.slug}/progress/{today.isoformat()}-{query.message.message_id}.jpg"
    photo_path = await asyncio.to_thread(storage.upload_photo, image_bytes, path)

    await asyncio.to_thread(db.add_progress_photo, user_id, today, photo_path)

    # Tick the progress-photo task for today.
    state = await asyncio.to_thread(db.get_checklist_state, user)
    await asyncio.to_thread(
        db.upsert_completion, state.log["id"], "progress_photo", completed=True, value=None
    )
    day_number = state.day_number

    # Forward to the other user so we keep each other accountable.
    other = the_other_user(user.slug)
    shared_line = ""
    if other.telegram_user_id:
        try:
            await context.bot.send_photo(
                chat_id=other.telegram_user_id,
                photo=file_id,
                caption=f"📸 {user.name}'s progress photo — day {day_number} of 75 💪",
            )
            shared_line = f"\nShared with {other.name} ✅"
        except Exception as exc:  # noqa: BLE001 - other user may not have started the bot
            log.warning("Could not forward progress photo to %s: %s", other.name, exc)
            shared_line = f"\n(Couldn't reach {other.name} — have they messaged the bot?)"

    await query.edit_message_text(
        f"📸 Progress photo saved — day {day_number}.\n"
        f"Progress photo task ✓{shared_line}"
    )


async def _prepare_workout(query, context, *, image_bytes=None, description=None) -> None:
    est = await asyncio.to_thread(
        ai.estimate_workout, description=description, image_bytes=image_bytes
    )
    context.user_data["pending_workout"] = est
    guess = "outdoor" if est["is_outdoor"] else "indoor"
    text = (
        f"🏋️ {est['summary']}\n"
        f"~{est['duration_min']} min · ~{est['calories_burned']} kcal burned\n\n"
        f"Was this indoor or outdoor? (looks {guess})"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🏋️ Indoor", callback_data="wk|save|in"),
            InlineKeyboardButton("🌳 Outdoor", callback_data="wk|save|out"),
        ]]
    )
    await query.edit_message_text(text, reply_markup=keyboard)


# ---- favorite + re-log ----------------------------------------------------
async def _handle_meal_action(update, context, user, action: str, meal_id: str) -> None:
    query = update.callback_query
    if action == "fav":
        await asyncio.to_thread(db.set_meal_favorite, meal_id, True)
        await query.answer("Saved to favorites ⭐")
        return

    if action == "relog":
        src = await asyncio.to_thread(db.get_meal, meal_id)
        if not src:
            await query.answer("That favorite is gone.", show_alert=True)
            return
        user_id = await asyncio.to_thread(db.get_user_id, user.slug)
        today = db.app_today()
        await asyncio.to_thread(
            db.add_meal, user_id, today,
            description=src["description"], calories=src["ai_calories"] or 0,
            protein=src["ai_protein"] or 0, carbs=src["ai_carbs"] or 0,
            fat=src["ai_fat"] or 0, is_favorite=True,
        )
        totals = await asyncio.to_thread(db.day_nutrition_totals, user_id, today)
        user_row = await asyncio.to_thread(db.get_user_row, user.slug)
        await query.answer(f"Logged {src['description']} ✅")
        await query.edit_message_text(
            f"⭐ Re-logged: {src['description']}\n"
            f"{src['ai_calories'] or 0} kcal\n\n"
            f"Today: {totals['calories']} kcal"
            + _target_suffix(totals['calories'], user_row.get('daily_calorie_target'))
        )


# ---- workout save (indoor/outdoor) ---------------------------------------
async def _handle_workout_save(update, context, user, in_out: str) -> None:
    query = update.callback_query
    est = context.user_data.get("pending_workout")
    if not est:
        await query.answer("Please log the workout again.", show_alert=True)
        return
    is_outdoor = in_out == "out"
    user_id = await asyncio.to_thread(db.get_user_id, user.slug)
    today = db.app_today()
    await asyncio.to_thread(
        db.log_workout, user_id, today,
        is_outdoor=is_outdoor, description=est["summary"],
        duration_min=est["duration_min"], ai_calories_burned=est["calories_burned"],
    )
    total, outdoor = await asyncio.to_thread(db.get_workout_counts, user_id, today)
    context.user_data.pop("pending_workout", None)
    await query.answer("Workout logged 🏋️")

    where = "outdoor" if is_outdoor else "indoor"
    outdoor_note = "✅ outdoor done" if outdoor >= 1 else "still need one outdoors"
    await query.edit_message_text(
        f"🏋️ Logged {where} workout: {est['summary']}\n"
        f"~{est['duration_min']} min · ~{est['calories_burned']} kcal burned\n\n"
        f"Workouts today: {total}/2 ({outdoor_note})"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@restricted
async def logworkout(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/logworkout <description> — log a workout from text."""
    description = " ".join(context.args).strip()
    if not description:
        await update.message.reply_text(
            "Tell me about the workout, e.g.\n/logworkout 5 km run in the park"
        )
        return
    msg = await update.message.reply_text("🔎 Estimating your workout…")
    # Reuse the photo-flow prep by faking a query-like object via a fresh message edit.
    est = await asyncio.to_thread(ai.estimate_workout, description=description)
    context.user_data["pending_workout"] = est
    guess = "outdoor" if est["is_outdoor"] else "indoor"
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🏋️ Indoor", callback_data="wk|save|in"),
            InlineKeyboardButton("🌳 Outdoor", callback_data="wk|save|out"),
        ]]
    )
    await msg.edit_text(
        f"🏋️ {est['summary']}\n"
        f"~{est['duration_min']} min · ~{est['calories_burned']} kcal burned\n\n"
        f"Was this indoor or outdoor? (looks {guess})",
        reply_markup=keyboard,
    )


@restricted
async def meals(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/meals — quick-pick favorites to re-log in one tap."""
    user_id = await asyncio.to_thread(db.get_user_id, user.slug)
    favorites = await asyncio.to_thread(db.get_favorite_meals, user_id)
    if not favorites:
        await update.message.reply_text(
            "No favorites yet. Log a meal photo, then tap “⭐ Save as favorite”."
        )
        return
    rows = [
        [InlineKeyboardButton(
            f"⭐ {m['description']} ({m['ai_calories'] or 0} kcal)",
            callback_data=f"meal|relog|{m['id']}",
        )]
        for m in favorites
    ]
    await update.message.reply_text(
        "Your favorite meals — tap to re-log today:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@restricted
async def workout(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/workout — AI plan: one indoor + one outdoor, rotating muscle groups."""
    msg = await update.message.reply_text("🧠 Building today's workout plan…")
    user_id = await asyncio.to_thread(db.get_user_id, user.slug)
    recent = await asyncio.to_thread(db.get_recent_workouts, user_id, 3)
    recent_descriptions = [
        f"{r['date']}: {r.get('description') or 'workout'}"
        f"{' (outdoor)' if r.get('is_outdoor') else ''}"
        for r in recent
    ]
    plan = await asyncio.to_thread(
        ai.suggest_workout, name=user.name, recent=recent_descriptions
    )
    await msg.edit_text(f"🏋️ Today's plan for {user.name}\n\n{plan}")


@restricted
async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/summary — short AI end-of-day recap."""
    msg = await update.message.reply_text("📋 Putting together your summary…")
    state = await asyncio.to_thread(db.get_checklist_state, user)
    user_id = state.user_row["id"]
    today = db.app_today()
    totals = await asyncio.to_thread(db.day_nutrition_totals, user_id, today)
    total_workouts, _ = await asyncio.to_thread(db.get_workout_counts, user_id, today)
    pending = [ts.task.label for ts in state.tasks if not ts.complete]

    text = await asyncio.to_thread(
        ai.daily_summary,
        name=user.name, day_number=state.day_number,
        tasks_done=state.completed_count, tasks_total=state.total_count,
        day_passed=state.all_complete, calories=totals["calories"],
        calorie_target=state.user_row.get("daily_calorie_target"),
        workouts=total_workouts, pending=pending,
    )
    await msg.edit_text(text)


@restricted
async def target(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/target <calories> [protein] — set your own nutrition targets."""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Set your own targets, e.g.\n/target 2000 150\n"
            "(daily calories, optional protein grams)"
        )
        return
    calories = int(args[0])
    protein = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await asyncio.to_thread(
        db.set_targets, user.slug, calorie_target=calories, protein_target=protein
    )
    extra = f", protein {protein} g" if protein else ""
    await update.message.reply_text(f"Targets saved: {calories} kcal/day{extra}. ✅")


# ---------------------------------------------------------------------------
# Card formatting (plain text — AI descriptions may contain markdown chars)
# ---------------------------------------------------------------------------
def _meal_card(est: dict, totals: dict, target: int | None) -> str:
    lines = [
        "🍽 Meal logged",
        est["description"],
        f"{est['calories']} kcal · P {_n(est['protein'])}g · "
        f"C {_n(est['carbs'])}g · F {_n(est['fat'])}g",
        "",
        f"Today: {totals['calories']} kcal" + _target_suffix(totals["calories"], target),
    ]
    return "\n".join(lines)


def _target_suffix(eaten: int, target: int | None) -> str:
    if not target:
        return "  (set a target with /target)"
    remaining = target - eaten
    if remaining >= 0:
        return f" / {target} kcal  ({remaining} left)"
    return f" / {target} kcal  ({-remaining} over)"


def _n(number) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"
