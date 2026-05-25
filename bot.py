import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from telegram import (
Update,
InlineKeyboardButton,
InlineKeyboardMarkup,
)

from telegram.ext import (
Application,
CommandHandler,
ContextTypes,
MessageHandler,
CallbackQueryHandler,
filters,
)

from cache import BINCache
from processor import process_file

load_dotenv()

TOKEN = os.getenv(“BOT_TOKEN”)

logging.basicConfig(
format=”%(asctime)s | %(levelname)s | %(name)s | %(message)s”,
level=logging.INFO,
)

logger = logging.getLogger(name)

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
tg_file = await message.document.get_file()
downloads_dir = Path("downloads")
downloads_dir.mkdir(
    exist_ok=True
)
input_path = (
    downloads_dir
    / message.document.file_name
)
await tg_file.download_to_drive(
    custom_path=str(input_path)
)
context.user_data["input_path"] = str(
    input_path
)
keyboard = [
    [
        InlineKeyboardButton(
            "✅ Continue Processing",
            callback_data="continue_process",
        )
    ],
    [
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data="cancel_process",
        )
    ],
]
reply_markup = InlineKeyboardMarkup(
    keyboard
)
await message.reply_text(
    "⚠️ Save your file before continuing.",
    reply_markup=reply_markup,
)

async def output_handler(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):

query = update.callback_query
await query.answer()
output_path = Path(
    context.user_data["output_path"]
)
input_name = context.user_data["input_name"]
lines = output_path.read_text(
    encoding="utf-8",
    errors="ignore",
).splitlines()
if query.data == "out_original":
    filtered = lines
    label = "original"
elif query.data == "out_sorted":
    filtered = sorted(lines)
    label = "sorted"
elif query.data == "out_debit":
    filtered = [
        l for l in lines
        if "DEBIT" in l.upper()
    ]
    label = "debit_only"
elif query.data == "out_credit":
    filtered = [
        l for l in lines
        if "CREDIT" in l.upper()
    ]
    label = "credit_only"
else:
    return
out_text = "\n".join(filtered)
out_bytes = out_text.encode(
    "utf-8"
)
await query.message.reply_document(
    document=out_bytes,
    filename=f"{label}_{input_name}",
    caption=(
        f"✅ {label.replace('_', ' ').title()}"
        f" — {len(filtered)} lines"
    ),
)

async def button_handler(
update: Update,
context: ContextTypes.DEFAULT_TYPE,
):

query = update.callback_query
await query.answer()
if query.data == "cancel_process":
    await query.edit_message_text(
        "❌ Processing cancelled."
    )
    return
if query.data == "continue_process":
    await query.edit_message_text(
        "🔍 Processing your file...\nPlease wait."
    )
    input_path = Path(
        context.user_data["input_path"]
    )
    try:
        output_path, stats = await process_file(
            input_path,
            cache,
        )
        top_banks = (
            stats["bank_counts"]
            .most_common(10)
        )
        analysis_text = (
            "📊 FILE ANALYSIS\n\n"
        )
        for bank, count in top_banks:
            analysis_text += (
                f"{count}x {bank}\n"
            )
        remaining = (
            len(stats["bank_counts"])
            - len(top_banks)
        )
        if remaining > 0:
            analysis_text += (
                f"\n+ {remaining} more banks..."
            )
        analysis_text += (
            f"\n\n💳 Debit: "
            f"{stats['debit_count']}"
            f"\n💳 Credit: "
            f"{stats['credit_count']}"
            f"\n\n👇 Choose Output Below 👇"
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "📄 ORIGINAL",
                    callback_data="out_original",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏦 SORTED",
                    callback_data="out_sorted",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 DEBIT ONLY",
                    callback_data="out_debit",
                )
            ],
            [
                InlineKeyboardButton(
                    "💳 CREDIT ONLY",
                    callback_data="out_credit",
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(
            keyboard
        )
        await query.message.reply_text(
            analysis_text,
            reply_markup=reply_markup,
        )
        context.user_data["output_path"] = str(
            output_path
        )
        context.user_data["input_name"] = (
            input_path.name
        )
        with open(output_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=(
                    f"processed_{input_path.name}"
                ),
                caption=(
                    f"✅ Processing Complete\n\n"
                    f"Lines: "
                    f"{stats['total_lines']}\n"
                    f"BINs: "
                    f"{stats['bins_found']}\n"
                    f"API Calls: "
                    f"{stats['api_calls']}\n"
                    f"Cache Hits: "
                    f"{stats['cache_hits']}\n"
                    f"Errors: "
                    f"{stats['errors']}"
                ),
            )
    except Exception as e:
        logger.exception(e)
        await query.message.reply_text(
            f"❌ Error:\n{e}"
        )

def main():

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN missing in environment"
    )
application = (
    Application.builder()
    .token(TOKEN)
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
        output_handler,
        pattern="^out_",
    )
)
application.add_handler(
    CallbackQueryHandler(
        button_handler
    )
)
logger.info(
    "Starting bot"
)
application.run_polling()

if name == “main”:
main()