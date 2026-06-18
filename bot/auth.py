"""Allow-list — the bot is private to the two of us.

Every command and button press is gated by `restricted`, which resolves the
Telegram user id to one of the configured users and rejects anyone else.
"""

from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from shared.config import UserConfig, user_by_telegram_id

# Type of a handler that has been handed the resolved user.
Handler = Callable[
    [Update, ContextTypes.DEFAULT_TYPE, UserConfig], Awaitable[None]
]


def restricted(handler: Handler):
    """Decorator: only let allow-listed users through.

    Wraps a handler whose signature is `(update, context, user)` and supplies
    the resolved `UserConfig`. Unknown ids get a polite refusal and nothing is
    processed.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tg_user = update.effective_user
        user = user_by_telegram_id(tg_user.id) if tg_user else None

        if user is None:
            # Reject quietly but clearly. Works for both messages and buttons.
            if update.callback_query:
                await update.callback_query.answer(
                    "Sorry, this bot is private.", show_alert=True
                )
            elif update.effective_message:
                await update.effective_message.reply_text(
                    "Sorry, this bot is private to its two owners."
                )
            return

        await handler(update, context, user)

    return wrapper
