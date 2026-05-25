import asyncio
import logging
import os
import tempfile
from contextlib import suppress

from aiohttp import ClientSession
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from processor import (
    analyze_file,
    build_output,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("telegram-file-bot")

LOADING_FRAMES = [
    "🔍",
    "⏳",
    "⚡",
    "📡",
    "🔄",
]


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "Send a TXT file to begin processing."
    )


async def loading_animation(
    message,
    stop_event: asyncio.Event,
):

    idx = 0

    while not stop_event.is_set():

        try:

            frame = LOADING_FRAMES[
                idx % len(LOADING_FRAMES)
            ]

            await message.edit_text(
                f"{frame} Processing file..."
            )

            idx += 1

            await asyncio.sleep(0.7)

        except Exception:

            await asyncio.sleep(0.7)


async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    document = update.message.document

    if not document.file_name.lower().endswith(".txt"):

        await update.message.reply_text(
            "Only TXT files are supported."
        )

        return

    temp_dir = tempfile.mkdtemp(
        prefix="tgfile_"
    )

    temp_path = os.path.join(
        temp_dir,
        document.file_name,
    )

    try:

        telegram_file = await document.get_file()

        await update.message.chat.send_action(
            ChatAction.UPLOAD_DOCUMENT
        )

        async with ClientSession() as session:

            async with session.get(
                telegram_file.file_path
            ) as response:

                response.raise_for_status()

                with open(
                    temp_path,
                    "wb",
                ) as f:

                    while True:

                        chunk = await response.content.read(
                            1024 * 1024
                        )

                        if not chunk:
                            break

                        f.write(chunk)

        context.user_data[
            "uploaded_file"
        ] = temp_path

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "CONTINUE",
                        callback_data="continue_processing",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "CANCEL",
                        callback_data="cancel_processing",
                    )
                ],
            ]
        )

        await update.message.reply_text(
            "⚠️ Save your file before continuing.",
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception(
            "Failed to download file"
        )

        await update.message.reply_text(
            f"Error downloading file:\n{e}"
        )


async def handle_callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "cancel_processing":

        await query.edit_message_text(
            "Operation cancelled."
        )

        cleanup_temp(context)

        return

    if data == "continue_processing":

        file_path = context.user_data.get(
            "uploaded_file"
        )

        if (
            not file_path
            or not os.path.exists(file_path)
        ):

            await query.edit_message_text(
                "Uploaded file not found."
            )

            return

        loading_msg = await query.message.reply_text(
            "🔍 Processing file..."
        )

        stop_event = asyncio.Event()

        animation_task = asyncio.create_task(
            loading_animation(
                loading_msg,
                stop_event,
            )
        )

        try:

            analysis = await analyze_file(
                file_path
            )

            context.user_data[
                "analysis"
            ] = analysis

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "ORIGINAL",
                            callback_data="output_original",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "SORTED",
                            callback_data="output_sorted",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "TYPE A ONLY",
                            callback_data="output_type_a",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "TYPE B ONLY",
                            callback_data="output_type_b",
                        )
                    ],
                ]
            )

            text = (
                "📊 FILE ANALYSIS\n\n"
                f"{analysis['category_a']}x Category A\n"
                f"{analysis['category_b']}x Category B\n"
                f"{analysis['category_c']}x Category C\n\n"
                f"Type A: {analysis['type_a']}\n"
                f"Type B: {analysis['type_b']}\n\n"
                "Choose Output:"
            )

            stop_event.set()

            with suppress(Exception):

                await animation_task

            with suppress(Exception):

                await loading_msg.delete()

            await query.message.reply_text(
                text,
                reply_markup=keyboard,
            )

        except Exception as e:

            logger.exception(
                "Processing failed"
            )

            stop_event.set()

            with suppress(Exception):

                await animation_task

            with suppress(Exception):

                await loading_msg.delete()

            await query.message.reply_text(
                f"Processing failed:\n{e}"
            )

    elif data.startswith("output_"):

        analysis = context.user_data.get(
            "analysis"
        )

        if not analysis:

            await query.edit_message_text(
                "Session expired."
            )

            return

        mode_map = {
            "output_original": "original",
            "output_sorted": "sorted",
            "output_type_a": "type_a",
            "output_type_b": "type_b",
        }

        mode = mode_map[data]

        output_lines = await build_output(
            analysis,
            mode,
        )

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".txt",
            mode="w",
            encoding="utf-8",
        ) as tmp:

            tmp.write(
                "\n".join(output_lines)
            )

            output_path = tmp.name

        try:

            await query.message.reply_document(
                document=open(
                    output_path,
                    "rb",
                ),
                filename=f"{mode}_output.txt",
                caption="Processing complete.",
            )

        finally:

            with suppress(Exception):

                os.remove(output_path)

            cleanup_temp(context)


def cleanup_temp(context):

    file_path = context.user_data.get(
        "uploaded_file"
    )

    if (
        file_path
        and os.path.exists(file_path)
    ):

        with suppress(Exception):

            os.remove(file_path)

        with suppress(Exception):

            os.rmdir(
                os.path.dirname(file_path)
            )

    context.user_data.clear()


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable missing"
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_callbacks
        )
    )

    logger.info("Bot started")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":

    main()