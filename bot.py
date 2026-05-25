"""
BIN Lookup Telegram Bot
=======================
Accepts .txt files from users, detects BIN numbers, enriches them
with metadata from binlist.net, and returns the modified file.

Usage:
    Set TELEGRAM_BOT_TOKEN in your .env file, then run:
        python src/bot.py
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from processor import process_file
from cache import BINCache

# ---------------------------------------------------------------------------
# Configuration & logging
# ---------------------------------------------------------------------------

load_dotenv()

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Add it to your .env file or export it as an environment variable."
    )

# Shared cache instance (SQLite-backed, safe for async use)
cache = BINCache()


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — greet the user and explain usage."""
    text = (
        "👋 *BIN Lookup Bot*\n\n"
        "Send me a `.txt` file containing BIN lines such as:\n"
        "```\n"
        "BIN : 448297\n"
        "BIN-448297\n"
        "BIN :448297\n"
        "```\n"
        "I will append metadata (brand, type, level, bank) to each BIN line "
        "and send the enriched file back to you.\n\n"
        "Commands:\n"
        "/start — show this message\n"
        "/help  — show this message\n"
        "/stats — show cache statistics"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias /help → /start."""
    await cmd_start(update, context)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return cache hit/miss counters and total cached BINs."""
    stats = cache.stats()
    text = (
        "📊 *Cache Statistics*\n\n"
        f"• Cached BINs : {stats['total_cached']}\n"
        f"• Cache hits  : {stats['hits']}\n"
        f"• Cache misses: {stats['misses']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Document handler
# ---------------------------------------------------------------------------


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main handler for file uploads.

    Flow:
      1. Validate the uploaded file is a .txt.
      2. Download the file to a temp location.
      3. Process it line-by-line (BIN detection + enrichment).
      4. Send the enriched file back.
    """
    message = update.message
    document: Document = message.document

    # ---- Validate file type ------------------------------------------------
    filename: str = document.file_name or ""
    if not filename.lower().endswith(".txt"):
        await message.reply_text(
            "⚠️ Please send a `.txt` file. Other formats are not supported."
        )
        return

    logger.info(
        "Received file '%s' (%d bytes) from user %d",
        filename,
        document.file_size,
        message.from_user.id,
    )

    processing_msg = await message.reply_text(
        "⏳ Processing your file… This may take a moment for large files."
    )

    try:
        # ---- Download file to a temporary path -----------------------------
        tg_file = await document.get_file()

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / filename
            await tg_file.download_to_drive(str(input_path))

            # ---- Process (enrich BIN lines) --------------------------------
            output_path, stats = await process_file(
                input_path=input_path,
                cache=cache,
            )

            # ---- Reply with enriched file ----------------------------------
            caption = (
                f"✅ Done!\n"
                f"• Lines processed : {stats['total_lines']}\n"
                f"• BINs found      : {stats['bins_found']}\n"
                f"• Lookups (API)   : {stats['api_calls']}\n"
                f"• Lookups (cache) : {stats['cache_hits']}\n"
                f"• Errors          : {stats['errors']}"
            )

            with open(output_path, "rb") as f:
                await message.reply_document(
                    document=f,
                    filename=f"enriched_{filename}",
                    caption=caption,
                )

        logger.info("Successfully processed '%s': %s", filename, stats)

    except Exception as exc:
        logger.exception("Error processing file '%s': %s", filename, exc)
        await message.reply_text(
            f"❌ An error occurred while processing your file:\n`{exc}`",
            parse_mode="Markdown",
        )

    finally:
        # Delete the "processing…" status message
        await processing_msg.delete()


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------


def build_application() -> Application:
    """Wire up handlers and return a configured Application instance."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    return app


def main() -> None:
    logger.info("Starting BIN Lookup Bot…")
    app = build_application()
    # run_polling blocks until SIGINT / SIGTERM
    app.run_polling(allowed_updates=Update.ALL_TYPES)


import asyncio

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
