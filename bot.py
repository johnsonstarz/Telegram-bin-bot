import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    Update,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

from processor import process_file
from cache import BINCache

# Load environment variables
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN")

cache = BINCache()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                "CONTINUE",
                callback_data="continue",
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "⚠️ PRIVACY NOTICE\n\n"
        "Please save all files after processing.\n\n"
        "Chats, uploads, and generated files may automatically clear or delete over time. "
        "Once files are deleted on your end, they cannot be recovered.\n\n"
        "Press CONTINUE to proceed.",
        reply_markup=reply_markup,
    )


async def handle_continue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(
        "📂 Upload your TXT file to begin processing."
    )


async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.message

    document: Document = message.document

    filename = document.file_name or "file.txt"

    if not filename.lower().endswith(".txt"):

        await message.reply_text(
            "Please send a .txt file"
        )

        return

    processing = await message.reply_text(
        "Processing..."
    )

    try:

        tg_file = await document.get_file()

        with tempfile.TemporaryDirectory() as tmpdir:

            input_path = Path(tmpdir) / filename

            await tg_file.download_to_drive(
                str(input_path)
            )

            output_path, stats = await process_file(
                input_path=input_path,
                cache=cache,
            )

            with open(output_path, "rb") as f:

                await message.reply_document(
                    document=f,
                    filename=f"enriched_{filename}",
                    caption=(
                        f"Done\n"
                        f"Lines: {stats['total_lines']}\n"
                        f"BINs: {stats['bins_found']}"
                    ),
                )

    except Exception as e:

        logger.exception(e)

        await message.reply_text(
            f"Error: {e}"
        )

    finally:

        await processing.delete()


def main():

    logger.info("Starting bot")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_continue,
            pattern="^continue$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()