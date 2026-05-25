import asyncio
import logging
import re
import tempfile
from pathlib import Path

import aiohttp

from cache import BINCache

logger = logging.getLogger(__name__)

BIN_PATTERN = re.compile(r"BIN\s*[:\-]\s*(\d{6})", re.IGNORECASE)

BINX_URL = "https://binx.vip/bin/{bin}"
BINLIST_URL = "https://lookup.binlist.net/{bin}"

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)

MAX_CONCURRENT_LOOKUPS = 5


def unknown():
    return {
        "brand": "UNKNOWN",
        "type": "UNKNOWN",
        "level": "UNKNOWN",
        "bank": "UNKNOWN",
    }


def format_metadata(data):
    return (
        f" | BRAND - {data['brand']}"
        f" | TYPE - {data['type']}"
        f" | LEVEL - {data['level']}"
        f" | BANK - {data['bank']}"
    )


async def fetch_binx(session, bin_number):

    try:

        url = BINX_URL.format(bin=bin_number)

        async with session.get(url, timeout=HTTP_TIMEOUT) as resp:

            if resp.status != 200:
                return None

            text = await resp.text()

            upper = text.upper()

            brand = "UNKNOWN"
            card_type = "UNKNOWN"
            level = "UNKNOWN"
            bank = "UNKNOWN"

            if "VISA" in upper:
                brand = "VISA"

            elif "MASTERCARD" in upper:
                brand = "MASTERCARD"

            elif "AMEX" in upper:
                brand = "AMEX"

            if "DEBIT" in upper:
                card_type = "DEBIT"

            elif "CREDIT" in upper:
                card_type = "CREDIT"

            for lvl in [
                "CLASSIC",
                "PLATINUM",
                "GOLD",
                "SIGNATURE",
                "WORLD",
            ]:
                if lvl in upper:
                    level = lvl
                    break

            return {
                "brand": brand,
                "type": card_type,
                "level": level,
                "bank": bank,
            }

    except Exception as e:
        logger.error(e)
        return None


async def fetch_binlist(session, bin_number):

    try:

        url = BINLIST_URL.format(bin=bin_number)

        async with session.get(url, timeout=HTTP_TIMEOUT) as resp:

            if resp.status != 200:
                return None

            data = await resp.json(content_type=None)

            return {
                "brand": (data.get("scheme") or "UNKNOWN").upper(),
                "type": (data.get("type") or "UNKNOWN").upper(),
                "level": (data.get("brand") or "UNKNOWN").upper(),
                "bank": (
                    (data.get("bank") or {}).get("name")
                    or "UNKNOWN"
                ).upper(),
            }

    except Exception as e:
        logger.error(e)
        return None


async def fetch_metadata(
    session,
    bin_number,
    cache,
    semaphore,
    stats,
):

    cached = cache.get(bin_number)

    if cached:
        stats["cache_hits"] += 1
        return cached

    async with semaphore:

        data = await fetch_binx(
            session,
            bin_number,
        )

        if (
            not data
            or data["brand"] == "UNKNOWN"
        ):

            data = await fetch_binlist(
                session,
                bin_number,
            )

        if not data:
            data = unknown()

        cache.set(bin_number, data)

        stats["api_calls"] += 1

        return data


async def process_file(
    input_path: Path,
    cache: BINCache,
):

    stats = {
        "total_lines": 0,
        "bins_found": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
    }

    unique_bins = set()

    with open(
        input_path,
        "r",
        encoding="utf-8",
        errors="replace",
    ) as f:

        for line in f:

            match = BIN_PATTERN.search(line)

            if match:
                unique_bins.add(match.group(1))

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_LOOKUPS
    )

    metadata_map = {}

    async with aiohttp.ClientSession() as session:

        tasks = [
            fetch_metadata(
                session,
                b,
                cache,
                semaphore,
                stats,
            )
            for b in unique_bins
        ]

        results = await asyncio.gather(*tasks)

    for b, result in zip(unique_bins, results):
        metadata_map[b] = result

    fd, temp_path = tempfile.mkstemp(
        suffix=".txt"
    )

    with (
        open(
            input_path,
            "r",
            encoding="utf-8",
            errors="replace",
        ) as infile,
        open(
            fd,
            "w",
            encoding="utf-8",
        ) as outfile,
    ):

        for line in infile:

            stats["total_lines"] += 1

            match = BIN_PATTERN.search(line)

            if match:

                stats["bins_found"] += 1

                b = match.group(1)

                metadata = metadata_map.get(
                    b,
                    unknown(),
                )

                line = (
                    line.rstrip("\n")
                    + format_metadata(metadata)
                    + "\n"
                )

            outfile.write(line)

    return Path(temp_path), stats
