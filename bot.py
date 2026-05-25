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

    await update.message.reply_text(
        "Send me a .txt file with BINs."
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
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()