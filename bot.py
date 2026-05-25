"""
processor.py
=============
BIN processing + fallback lookup system
"""

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Tuple, Dict, Any

import aiohttp

from cache import BINCache

logger = logging.getLogger(__name__)

# Match:
# BIN : 448297
# BIN:448297
# BIN-448297
BIN_PATTERN = re.compile(r"BIN\s*[:\-]\s*(\d{6})", re.IGNORECASE)

# APIs
BINX_URL = "https://binx.vip/bin/{bin}"
BINLIST_URL = "https://lookup.binlist.net/{bin}"

MAX_CONCURRENT_LOOKUPS = 5
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _unknown_metadata():
    return {
        "brand": "UNKNOWN",
        "type": "UNKNOWN",
        "level": "UNKNOWN",
        "bank": "UNKNOWN",
    }


def _format_metadata(metadata):

    return (
        f" | BRAND - {metadata['brand']}"
        f" | TYPE - {metadata['type']}"
        f" | LEVEL - {metadata['level']}"
        f" | BANK - {metadata['bank']}"
    )


async def _fetch_from_binx(session, bin_number):

    url = BINX_URL.format(bin=bin_number)

    try:
        async with session.get(url, timeout=HTTP_TIMEOUT) as resp:

            if resp.status != 200:
                return None

            text = await resp.text()

            upper = text.upper()

            brand = "UNKNOWN"
            card_type = "UNKNOWN"
            level = "UNKNOWN"
            bank = "UNKNOWN"

            # Brand
            if "VISA" in upper:
                brand = "VISA"

            elif "MASTERCARD" in upper:
                brand = "MASTERCARD"

            elif "AMERICAN EXPRESS" in upper or "AMEX" in upper:
                brand = "AMEX"

            elif "DISCOVER" in upper:
                brand = "DISCOVER"

            # Type
            if "DEBIT" in upper:
                card_type = "DEBIT"

            elif "CREDIT" in upper:
                card_type = "CREDIT"

            elif "PREPAID" in upper:
                card_type = "PREPAID"

            # Level
            levels = [
                "CLASSIC",
                "PLATINUM",
                "GOLD",
                "SIGNATURE",
                "WORLD",
                "BUSINESS",
                "INFINITE",
            ]

            for lvl in levels:
                if lvl in upper:
                    level = lvl
                    break

            # Bank guesses
            possible_banks = [
                "CHASE",
                "BANK OF AMERICA",
                "WELLS FARGO",
                "CAPITAL ONE",
                "CITI",
                "JPMORGAN",
                "TD BANK",
                "PNC",
                "NAVY FEDERAL",
                "US BANK",
            ]

            for b in possible_banks:
                if b in upper:
                    bank = b
                    break

            return {
                "brand": brand,
                "type": card_type,
                "level": level,
                "bank": bank,
            }

    except Exception as e:
        logger.error("BINX failed for %s: %s", bin_number, e)
        return None


async def _fetch_from_binlist(session, bin_number):

    url = BINLIST_URL.format(bin=bin_number)

    try:
        async with session.get(url, timeout=HTTP_TIMEOUT) as resp:

            if resp.status != 200:
                return None

            data = await resp.json(content_type=None)

            brand = (
                data.get("scheme")
                or "UNKNOWN"
            ).upper()

            card_type = (
                data.get("type")
                or "UNKNOWN"
            ).upper()

            level = (
                data.get("brand")
                or "UNKNOWN"
            ).upper()

            bank_obj = data.get("bank") or {}

            bank = (
                bank_obj.get("name")
                or "UNKNOWN"
            ).upper()

            return {
                "brand": brand,
                "type": card_type,
                "level": level,
                "bank": bank,
            }

    except Exception as e:
        logger.error("Binlist failed for %s: %s", bin_number, e)
        return None


async def fetch_bin_metadata(
    session,
    bin_number,
    cache,
    semaphore,
    stats,
):

    # Cache first
    cached = cache.get(bin_number)

    if cached is not None:
        stats["cache_hits"] += 1
        return cached

    async with semaphore:

        # Double-check cache
        cached = cache.get(bin_number)

        if cached is not None:
            stats["cache_hits"] += 1
            return cached

        metadata = None

        # Primary → BINX
        metadata = await _fetch_from_binx(
            session,
            bin_number,
        )

        # Fallback → Binlist
        if (
            metadata is None
            or metadata["brand"] == "UNKNOWN"
        ):

            metadata = await _fetch_from_binlist(
                session,
                bin_number,
            )

        # Final fallback
        if metadata is None:
            metadata = _unknown_metadata()

        stats["api_calls"] += 1

        cache.set(
            bin_number,
            metadata,
        )

        return metadata


async def process_file(
    input_path: Path,
    cache: BINCache,
) -> Tuple[Path, Dict[str, Any]]:

    stats = {
        "total_lines": 0,
        "bins_found": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
    }

    unique_bins = set()

    encoding = "utf-8"

    with input_path.open(
        "r",
        encoding=encoding,
        errors="replace",
    ) as fh:

        for line in fh:

            match = BIN_PATTERN.search(line)

            if match:
                unique_bins.add(match.group(1))

    logger.info(
        "Found %d unique BINs",
        len(unique_bins),
    )

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_LOOKUPS
    )

    bin_metadata = {}

    async with aiohttp.ClientSession(
        headers={
            "User-Agent": "BINLookupBot/1.0"
        }
    ) as session:

        tasks = [
            fetch_bin_metadata(
                session,
                bin_num,
                cache,
                semaphore,
                stats,
            )
            for bin_num in unique_bins
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    for bin_num, result in zip(
        unique_bins,
        results,
    ):

        if isinstance(result, Exception):

            logger.error(
                "Lookup failed for %s: %s",
                bin_num,
                result,
            )

            stats["errors"] += 1

            bin_metadata[bin_num] = (
                _unknown_metadata()
            )

        else:
            bin_metadata[bin_num] = result

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".txt"
    )

    tmp_path = Path(tmp_path_str)

    with (
        input_path.open(
            "r",
            encoding=encoding,
            errors="replace",
        ) as infile,
        open(
            tmp_fd,
            "w",
            encoding="utf-8",
        ) as outfile,
    ):

        for line in infile:

            stats["total_lines"] += 1

            match = BIN_PATTERN.search(line)

            if match:

                stats["bins_found"] += 1

                bin_number = match.group(1)

                metadata = bin_metadata.get(
                    bin_number,
                    _unknown_metadata(),
                )

                suffix = _format_metadata(
                    metadata
                )

                stripped = line.rstrip(
                    "\r\n"
                )

                newline = line[
                    len(stripped):
                ]

                line = (
                    stripped
                    + suffix
                    + newline
                )

            outfile.write(line)

    return tmp_path, stats
