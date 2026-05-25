"""
processor.py
============
Core file-processing logic.
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

# BIN regex
BIN_PATTERN = re.compile(r"BIN\s*[:\-]\s*(\d{6})", re.IGNORECASE)

# APIs
BINX_URL = "https://binx.vip/api/{bin}"
BINLIST_URL = "https://lookup.binlist.net/{bin}"

# Settings
MAX_CONCURRENT_LOOKUPS = 5
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def fetch_bin_metadata(
    session: aiohttp.ClientSession,
    bin_number: str,
    cache: BINCache,
    semaphore: asyncio.Semaphore,
    stats: Dict[str, int],
) -> Dict[str, str]:

    # Check cache first
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

        urls = [
            BINX_URL.format(bin=bin_number),
            BINLIST_URL.format(bin=bin_number),
        ]

        metadata = None

        for url in urls:

            try:
                async with session.get(url, timeout=HTTP_TIMEOUT) as resp:

                    stats["api_calls"] += 1

                    if resp.status == 200:

                        data = await resp.json(content_type=None)

                        metadata = _parse_binlist_response(data)

                        if metadata["brand"] != "UNKNOWN":
                            logger.info(
                                "BIN %s resolved using %s",
                                bin_number,
                                url,
                            )
                            break

                    elif resp.status in [404, 429]:

                        logger.warning(
                            "API failed (%d) for BIN %s using %s",
                            resp.status,
                            bin_number,
                            url,
                        )

                        continue

                    else:

                        logger.warning(
                            "Unexpected HTTP %d for BIN %s using %s",
                            resp.status,
                            bin_number,
                            url,
                        )

                        continue

            except asyncio.TimeoutError:

                logger.error(
                    "Timeout fetching BIN %s using %s",
                    bin_number,
                    url,
                )

                stats["errors"] += 1
                continue

            except aiohttp.ClientError as exc:

                logger.error(
                    "HTTP error fetching BIN %s using %s: %s",
                    bin_number,
                    url,
                    exc,
                )

                stats["errors"] += 1
                continue

        if metadata is None:
            metadata = _unknown_metadata()

    # Save result to cache
    cache.set(bin_number, metadata)

    return metadata


def _parse_binlist_response(data: dict) -> Dict[str, str]:

    brand = (
        data.get("scheme")
        or data.get("brand")
        or "UNKNOWN"
    ).upper()

    card_type = (
        data.get("type")
        or "UNKNOWN"
    ).upper()

    level = (
        data.get("level")
        or data.get("category")
        or data.get("brand")
        or "UNKNOWN"
    ).upper()

    bank_data = data.get("bank") or {}

    if isinstance(bank_data, dict):
        bank = (bank_data.get("name") or "UNKNOWN").upper()
    else:
        bank = str(bank_data).upper()

    return {
        "brand": brand,
        "type": card_type,
        "level": level,
        "bank": bank,
    }


def _unknown_metadata() -> Dict[str, str]:

    return {
        "brand": "UNKNOWN",
        "type": "UNKNOWN",
        "level": "UNKNOWN",
        "bank": "UNKNOWN",
    }


def _format_metadata(metadata: Dict[str, str]) -> str:

    return (
        f" | BRAND - {metadata['brand']}"
        f" | TYPE - {metadata['type']}"
        f" | LEVEL - {metadata['level']}"
        f" | BANK - {metadata['bank']}"
    )


async def process_file(
    input_path: Path,
    cache: BINCache,
) -> Tuple[Path, Dict[str, Any]]:

    stats: Dict[str, Any] = {
        "total_lines": 0,
        "bins_found": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
    }

    unique_bins: set[str] = set()
    encoding = _detect_encoding(input_path)

    with input_path.open("r", encoding=encoding, errors="replace") as fh:
        for line in fh:
            match = BIN_PATTERN.search(line)
            if match:
                unique_bins.add(match.group(1))

    logger.info(
        "Found %d unique BINs",
        len(unique_bins),
    )

    bin_metadata: Dict[str, Dict[str, str]] = {}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

    async with aiohttp.ClientSession(
        headers={
            "Accept-Version": "3",
            "User-Agent": "BINLookupBot/1.0",
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

    for bin_num, result in zip(unique_bins, results):

        if isinstance(result, Exception):

            logger.error(
                "Unhandled exception for BIN %s: %s",
                bin_num,
                result,
            )

            bin_metadata[bin_num] = _unknown_metadata()
            stats["errors"] += 1

        else:
            bin_metadata[bin_num] = result

    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".txt")
    tmp_path = Path(tmp_path_str)

    with (
        input_path.open("r", encoding=encoding, errors="replace") as infile,
        open(tmp_fd, "w", encoding="utf-8") as outfile,
    ):

        for line in infile:

            stats["total_lines"] += 1

            enriched_line = _enrich_line(
                line,
                bin_metadata,
                stats,
            )

            outfile.write(enriched_line)

    return tmp_path, stats


def _enrich_line(
    line: str,
    bin_metadata: Dict[str, Dict[str, str]],
    stats: Dict[str, Any],
) -> str:

    match = BIN_PATTERN.search(line)

    if not match:
        return line

    bin_number = match.group(1)

    stats["bins_found"] += 1

    metadata = bin_metadata.get(
        bin_number,
        _unknown_metadata(),
    )

    suffix = _format_metadata(metadata)

    stripped = line.rstrip("\r\n")
    newline = line[len(stripped):]

    return stripped + suffix + newline


def _detect_encoding(path: Path) -> str:

    with path.open("rb") as f:
        raw = f.read(4)

    if raw.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"

    if raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"

    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"

    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"

    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    return "utf-8"


