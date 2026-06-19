"""Bot entry point.

Phase 2 — bot core: allow-list, /start, /today, the tap-to-complete checklist,
and the day pass/fail engine with strict/soft mode (in shared/db.py).

Run with:  python -m bot.main
"""

from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import ai_handlers, handlers, reminders
from shared.config import load_settings

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# python-telegram-bot's httpx logs are noisy at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("bot")


def build_application() -> Application:
    """Create the Telegram application and register the phase 2 handlers."""
    settings = load_settings()  # raises clearly if a required secret is missing
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        # Generous timeouts so a slow first TLS handshake doesn't kill startup.
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .get_updates_read_timeout(40.0)
        .build()
    )

    # Phase 2 — core checklist
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("today", handlers.today))

    # Phase 4 — weight + measurements
    app.add_handler(CommandHandler("weight", handlers.weight))

    # Phase 3 — AI logging
    app.add_handler(CommandHandler("workout", ai_handlers.workout))
    app.add_handler(CommandHandler("logworkout", ai_handlers.logworkout))
    app.add_handler(CommandHandler("meals", ai_handlers.meals))
    app.add_handler(CommandHandler("summary", ai_handlers.summary))
    app.add_handler(CommandHandler("target", ai_handlers.target))
    app.add_handler(MessageHandler(filters.PHOTO, ai_handlers.on_photo))
    # Any other plain text → chat with the data-aware coach.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_handlers.coach_chat))

    # Phase 5 — reminders
    app.add_handler(CommandHandler("preview", reminders.preview))

    # Callback routing by prefix: checklist taps vs AI-feature buttons.
    app.add_handler(CallbackQueryHandler(handlers.on_callback, pattern=r"^t\|"))
    app.add_handler(
        CallbackQueryHandler(ai_handlers.on_ai_callback, pattern=r"^(photo|meal|wk)\|")
    )

    # Schedule the daily reminder + rollover jobs.
    reminders.schedule_jobs(app)

    return app


def main() -> None:
    app = build_application()
    log.info("Bot starting (phase 2: core). Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
