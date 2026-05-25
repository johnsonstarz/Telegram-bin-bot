import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Tuple, Dict, Any

import aiohttp

from cache import BINCache

logger = logging.getLogger(__name__)

# Detect:
# BIN : 448297
# BIN:448297
# BIN-448297
BIN_PATTERN = re.compile(
    r"(BIN\s*[:\-]\s*(\d{6}))",
    re.IGNORECASE,
)

BINX_URL = "https://binx.vip/bin/{bin}"
BINLIST_URL = "https://lookup.binlist.net/{bin}"

MAX_CONCURRENT_LOOKUPS = 20

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


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


async def _fetch_from_binx(
    session,
    bin_number,
):

    url = BINX_URL.format(
        bin=bin_number
    )

    try:

        async with session.get(
            url,
            timeout=HTTP_TIMEOUT,
        ) as resp:

            if resp.status != 200:
                return None

            text = await resp.text()

            upper = text.upper()

            metadata = {
                "brand": "UNKNOWN",
                "type": "UNKNOWN",
                "level": "UNKNOWN",
                "bank": "UNKNOWN",
            }

            # BRAND
            brands = [
                "VISA",
                "MASTERCARD",
                "AMEX",
                "DISCOVER",
                "JCB",
                "DINERS",
            ]

            for brand in brands:

                if brand in upper:

                    metadata["brand"] = brand
                    break

            # TYPE
            types = [
                "DEBIT",
                "CREDIT",
                "PREPAID",
                "BUSINESS",
            ]

            for t in types:

                if t in upper:

                    metadata["type"] = t
                    break

            # LEVEL
            levels = [
                "CLASSIC",
                "GOLD",
                "PLATINUM",
                "SIGNATURE",
                "WORLD",
                "WORLD ELITE",
                "BUSINESS",
                "INFINITE",
                "ELECTRON",
            ]

            for lvl in levels:

                if lvl in upper:

                    metadata["level"] = lvl
                    break

            # DYNAMIC BANK EXTRACTION
            bank_patterns = [

                r"BANK\s*[:\-]\s*([A-Z0-9 .,&'\-]+)",

                r"ISSUER\s*[:\-]\s*([A-Z0-9 .,&'\-]+)",

                r"FINANCIAL INSTITUTION\s*[:\-]\s*([A-Z0-9 .,&'\-]+)",

            ]

            for pattern in bank_patterns:

                match = re.search(
                    pattern,
                    upper,
                )

                if match:

                    extracted = (
                        match.group(1)
                        .strip()
                    )

                    extracted = extracted.split(
                        "|"
                    )[0].strip()

                    if (
                        extracted
                        and len(extracted) > 2
                    ):

                        metadata["bank"] = (
                            extracted
                        )

                        break

            return metadata

    except Exception as e:

        logger.error(
            "BINX failed %s: %s",
            bin_number,
            e,
        )

        return None


async def _fetch_from_binlist(
    session,
    bin_number,
):

    url = BINLIST_URL.format(
        bin=bin_number
    )

    try:

        async with session.get(
            url,
            timeout=HTTP_TIMEOUT,
        ) as resp:

            if resp.status != 200:
                return None

            data = await resp.json(
                content_type=None
            )

            return {

                "brand": (
                    data.get("scheme")
                    or "UNKNOWN"
                ).upper(),

                "type": (
                    data.get("type")
                    or "UNKNOWN"
                ).upper(),

                "level": (
                    data.get("brand")
                    or "UNKNOWN"
                ).upper(),

                "bank": (
                    (
                        data.get("bank")
                        or {}
                    ).get("name")
                    or "UNKNOWN"
                ).upper(),
            }

    except Exception as e:

        logger.error(
            "Binlist failed %s: %s",
            bin_number,
            e,
        )

        return None


async def fetch_bin_metadata(
    session,
    bin_number,
    cache,
    semaphore,
    stats,
):

    cached = cache.get(
        bin_number
    )

    if cached is not None:

        stats["cache_hits"] += 1

        return cached

    async with semaphore:

        metadata = await _fetch_from_binx(
            session,
            bin_number,
        )

        # Fallback to binlist
        if (
            metadata is None
            or metadata["brand"] == "UNKNOWN"
        ):

            fallback = await _fetch_from_binlist(
                session,
                bin_number,
            )

            if fallback:

                # Fill missing fields only
                for key in fallback:

                    if (
                        metadata is None
                        or metadata[key]
                        == "UNKNOWN"
                    ):

                        if fallback[key] != "UNKNOWN":

                            if metadata is None:
                                metadata = {}

                            metadata[key] = fallback[
                                key
                            ]

        if metadata is None:

            metadata = (
                _unknown_metadata()
            )

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

    # PASS 1
    with input_path.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as fh:

        for line in fh:

            matches = BIN_PATTERN.findall(
                line
            )

            for full_match, bin_number in matches:

                unique_bins.add(
                    bin_number
                )

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_LOOKUPS
    )

    bin_metadata = {}

    # FETCH BIN DATA
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

        if isinstance(
            result,
            Exception,
        ):

            stats["errors"] += 1

            bin_metadata[bin_num] = (
                _unknown_metadata()
            )

        else:

            bin_metadata[
                bin_num
            ] = result

    # OUTPUT FILE
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".txt"
    )

    tmp_path = Path(
        tmp_path_str
    )

    with (
        input_path.open(
            "r",
            encoding="utf-8",
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

            matches = BIN_PATTERN.findall(
                line
            )

            if not matches:

                outfile.write(line)
                continue

            output_parts = []

            for full_match, bin_number in matches:

                stats["bins_found"] += 1

                metadata = bin_metadata.get(
                    bin_number,
                    _unknown_metadata(),
                )

                clean = (
                    full_match
                    + _format_metadata(
                        metadata
                    )
                )

                output_parts.append(
                    clean
                )

            outfile.write(
                " | ".join(output_parts)
                + "\n"
            )

    return tmp_path, stats