# Telegram-bin-bot
# BIN Lookup Telegram Bot

A production-ready Telegram bot that enriches `.txt` files containing BIN numbers with card metadata from [binlist.net](https://lookup.binlist.net).

## Features

- ✅ Detects all common BIN line formats (`BIN : 123456`, `BIN-123456`, `BIN :123456`)
- ✅ Preserves **all** original text — only BIN lines are modified
- ✅ Appends metadata on the **same line** as the BIN
- ✅ SQLite caching — avoids redundant API calls across sessions
- ✅ Concurrent async lookups for fast processing of large files
- ✅ Graceful handling of missing fields (`UNKNOWN`)
- ✅ `/stats` command shows cache performance
- ✅ Detailed logging to console and `logs/bot.log`

-----

## Project Structure

```
bin_lookup_bot/
├── src/
│   ├── bot.py          # Telegram bot: handlers & application bootstrap
│   ├── processor.py    # File processing: BIN detection, enrichment, I/O
│   └── cache.py        # SQLite-backed BIN metadata cache
├── data/               # Auto-created; holds bin_cache.db
├── logs/               # Auto-created; holds bot.log
├── requirements.txt
├── .env.example        # Copy to .env and fill in your token
└── README.md
```

-----

## Quick Start

### 1 · Prerequisites

- Python 3.11 or newer
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 2 · Clone and install

```bash
git clone <your-repo-url>
cd bin_lookup_bot

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 3 · Configure environment

```bash
cp .env.example .env
# Open .env in your editor and set TELEGRAM_BOT_TOKEN
```

### 4 · Run

```bash
python src/bot.py
```

The bot starts polling for updates. Send it a `.txt` file on Telegram!

-----

## Input / Output Example

**Input file** (`cards.txt`):

```
Some random header text
Date: 2024-01-15
BIN : 448297
Another line with data
BIN-411111
Notes: check above
BIN :523456
Footer text here
```

**Output file** (`enriched_cards.txt`):

```
Some random header text
Date: 2024-01-15
BIN : 448297 | BRAND - VISA | TYPE - DEBIT | LEVEL - CLASSIC | BANK - FIRELANDS FEDERAL CREDIT UNION
Another line with data
BIN-411111 | BRAND - VISA | TYPE - CREDIT | LEVEL - CLASSIC | BANK - JPMORGAN CHASE BANK NA
Notes: check above
BIN :523456 | BRAND - MASTERCARD | TYPE - CREDIT | LEVEL - WORLD | BANK - CITIBANK NA
Footer text here
```

Non-BIN lines are **completely unchanged**.

-----

## Bot Commands

|Command |Description                    |
|--------|-------------------------------|
|`/start`|Welcome message and usage guide|
|`/help` |Same as `/start`               |
|`/stats`|Show cache hit/miss statistics |

-----

## Configuration Reference

|Variable            |Required|Description              |
|--------------------|--------|-------------------------|
|`TELEGRAM_BOT_TOKEN`|✅ Yes   |Bot token from @BotFather|

-----

## Architecture Notes

### Two-pass file processing

For memory efficiency with large files:

1. **Pass 1** — stream the file to collect all unique BIN numbers
1. **Batch fetch** — look up all BINs concurrently (up to 5 at once)
1. **Pass 2** — stream the file again, writing the enriched output

This means even a 100 MB file only requires memory proportional to the number of *unique BINs*, not the file size.

### Caching

Results are stored in `data/bin_cache.db` (SQLite, WAL mode). The cache survives bot restarts, so BINs that were previously looked up are never re-fetched from the API.

### Rate limiting

binlist.net’s free tier allows ~10 requests/minute. The bot uses a semaphore (`MAX_CONCURRENT_LOOKUPS = 5`) to stay within safe limits. If rate-limited (HTTP 429), the bot logs a warning and returns `UNKNOWN` for that BIN rather than crashing.

-----

## Running in Production

### systemd service (Linux)

Create `/etc/systemd/system/bin-lookup-bot.service`:

```ini
[Unit]
Description=BIN Lookup Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bin_lookup_bot
EnvironmentFile=/opt/bin_lookup_bot/.env
ExecStart=/opt/bin_lookup_bot/.venv/bin/python src/bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bin-lookup-bot
sudo journalctl -u bin-lookup-bot -f   # tail logs
```

### Docker (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
ENV TELEGRAM_BOT_TOKEN=""
CMD ["python", "src/bot.py"]
```

```bash
docker build -t bin-lookup-bot .
docker run -d \
  -e TELEGRAM_BOT_TOKEN=your_token \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  bin-lookup-bot
```

-----

## License

MIT
