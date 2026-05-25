import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Tuple, Dict, Any

import aiohttp
from bs4 import BeautifulSoup

from cache import BINCache

logger = logging.getLogger(__name__)

# ----------------------------------------
# MATCH:
# BIN : 448297
# BIN:448297
# BIN-448297
# ----------------------------------------
BIN_PATTERN = re.compile(
    r"(BIN\s*[:\-]\s*(\d{6}))",
    re.IGNORECASE,
)

# ----------------------------------------
# APIs
# ----------------------------------------
BINX_URL = "https://binx.vip/bin/{bin}"
BINLIST_URL = "https://lookup.binlist.net/{bin}"

# ----------------------------------------
# LOOKUP SETTINGS
# ----------------------------------------
MAX_CONCURRENT_LOOKUPS = 20

HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=15
)


# ----------------------------------------
# UNKNOWN FALLBACK
# ----------------------------------------
def _unknown_metadata():

    return {
        "brand": "UNKNOWN",
        "type": "UNKNOWN",
        "level": "UNKNOWN",
        "bank": "UNKNOWN",
    }


# ----------------------------------------
# FORMAT OUTPUT
# ----------------------------------------
def _format_metadata(metadata):

    return (
        f" | BRAND - {metadata['brand']}"
        f" | TYPE - {metadata['type']}"
        f" | LEVEL - {metadata['level']}"
        f" | BANK - {metadata['bank']}"
    )


# ----------------------------------------
# BINX LOOKUP
# ----------------------------------------
async def _fetch_from_binx(
    session,
    bin_number,
):

    try:

        url = BINX_URL.format(
            bin=bin_number
        )

        async with session.get(
            url,
            timeout=HTTP_TIMEOUT,
        ) as resp:

            if resp.status != 200:
                return None

            html = await resp.text()

            soup = BeautifulSoup(
                html,
                "html.parser",
            )

            text = soup.get_text(
                separator="\n"
            )

            lines = [
                line.strip().upper()
                for line in text.splitlines()
                if line.strip()
            ]

            metadata = {
                "brand": "UNKNOWN",
                "type": "UNKNOWN",
                "level": "UNKNOWN",
                "bank": "UNKNOWN",
            }

            # ----------------------------------------
            # BANK
            # BANK IS USUALLY THE LINE
            # DIRECTLY AFTER THE BIN NUMBER
            # ----------------------------------------
            for i, line in enumerate(lines):

                if line == bin_number:

                    if i + 1 < len(lines):

                        bank = lines[i + 1]

                        if len(bank) > 3:

                            metadata["bank"] = bank

                    break

            # ----------------------------------------
            # BRAND
            # ----------------------------------------
            for brand in [
                "VISA",
                "MASTERCARD",
                "AMEX",
                "DISCOVER",
            ]:

                if brand in lines:

                    metadata["brand"] = brand
                    break

            # ----------------------------------------
            # TYPE
            # ----------------------------------------
            for t in [
                "DEBIT",
                "CREDIT",
                "PREPAID",
            ]:

                if t in lines:

                    metadata["type"] = t
                    break

            # ----------------------------------------
            # LEVEL
            # ----------------------------------------
            for lvl in [
                "CLASSIC",
                "GOLD",
                "PLATINUM",
                "SIGNATURE",
                "WORLD",
                "WORLD ELITE",
                "BUSINESS",
                "INFINITE",
            ]:

                if lvl in lines:

                    metadata["level"] = lvl
                    break

            return metadata

    except Exception as e:

        logger.error(
            "BINX failed %s: %s",
            bin_number,
            e,
        )

        return None


# ----------------------------------------
# BINLIST FALLBACK
# ----------------------------------------
async def _fetch_from_binlist(
    session,
    bin_number,
):

    try:

        url = BINLIST_URL.format(
            bin=bin_number
        )

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


# ----------------------------------------
# FETCH BIN METADATA
# ----------------------------------------
async def fetch_bin_metadata(
    session,
    bin_number,
    cache,
    semaphore,
    stats,
):

    # CACHE FIRST
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

        fallback = await _fetch_from_binlist(
            session,
            bin_number,
        )

        # IF BINX FAILED
        if metadata is None:

            metadata = fallback

        # MERGE MISSING DATA
        elif fallback:

            for key in fallback:

                if (
                    metadata[key]
                    == "UNKNOWN"
                ):

                    if (
                        fallback[key]
                        != "UNKNOWN"
                    ):

                        metadata[key] = (
                            fallback[key]
                        )

        # FINAL FALLBACK
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


# ----------------------------------------
# PROCESS FILE
# ----------------------------------------
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

    # ----------------------------------------
    # PASS 1 → FIND UNIQUE BINS
    # ----------------------------------------
    with input_path.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as fh:

        for line in fh:

            matches = BIN_PATTERN.findall(
                line
            )

            for _, bin_number in matches:

                unique_bins.add(
                    bin_number
                )

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_LOOKUPS
    )

    bin_metadata = {}

    # ----------------------------------------
    # FETCH ALL BIN DATA
    # ----------------------------------------
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

    # ----------------------------------------
    # CREATE OUTPUT FILE
    # ----------------------------------------
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

            outputs = []

            for full_match, bin_number in matches:

                stats["bins_found"] += 1

                metadata = bin_metadata.get(
                    bin_number,
                    _unknown_metadata(),
                )

                outputs.append(
                    full_match
                    + _format_metadata(
                        metadata
                    )
                )

            outfile.write(
                " | ".join(outputs)
                + "\n"
            )

    return tmp_path, stats