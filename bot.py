import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from cache import BINCache
from processor import process_file

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

cache = BINCache()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Send a TXT file containing BINs."
    )


async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.message

    if not message.document:

        return

    processing = await message.reply_text(
        "🔍 Processing your file...\nPlease wait."
    )

    input_path = None
    output_path = None

    try:

        tg_file = await message.document.get_file()

        downloads_dir = Path("downloads")
        downloads_dir.mkdir(exist_ok=True)

        input_path = (
            downloads_dir
            / message.document.file_name
        )

        await tg_file.download_to_drive(
            custom_path=str(input_path)
        )

        output_path, stats = await process_file(
            input_path,
            cache,
        )

        with open(output_path, "rb") as f:

            await message.reply_document(
                document=f,
                filename=f"processed_{message.document.file_name}",
                caption=(
                    f"✅ Processing Complete\n\n"
                    f"Lines: {stats['total_lines']}\n"
                    f"BINs: {stats['bins_found']}\n"
                    f"API Calls: {stats['api_calls']}\n"
                    f"Cache Hits: {stats['cache_hits']}\n"
                    f"Errors: {stats['errors']}"
                ),
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
                pool_timeout=60,
            )

    except Exception as e:

        logger.exception(e)

        await message.reply_text(
            f"❌ Error:\n{e}"
        )

    finally:

        try:

            await processing.delete()

        except Exception:
            pass

        try:

            if input_path and input_path.exists():
                input_path.unlink()

        except Exception:
            pass

        try:

            if output_path and output_path.exists():
                output_path.unlink()

        except Exception:
            pass


def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN missing in environment"
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    logger.info("Starting bot")

    application.run_polling()


if __name__ == "__main__":

    main()