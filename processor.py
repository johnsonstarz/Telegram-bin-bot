import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Tuple, Dict, Any
from collections import Counter

import aiohttp

from cache import BINCache

logger = logging.getLogger(__name__)

BIN_PATTERN = re.compile(
    r"BIN\s*[:-]\s*(\d{6,8})",
    re.IGNORECASE,
)

BINX_API_URL = "https://api.binx.vip/api/bins/{bin}"

MAX_CONCURRENT_LOOKUPS = 20

HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=15
)


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


def _parse_binx_data(data: dict) -> dict | None:
    """
    Parse BINX API response. Tries multiple known response shapes:
      1. data.data  (nested)
      2. data directly
      3. data.result
    Returns normalised metadata dict or None.
    """
    if not isinstance(data, dict):
        return None

    # Try nested shapes first
    info = (
        data.get("data")
        or data.get("result")
        or data.get("bin")
        or None
    )

    # If none of the nested keys exist, treat the root as the info object
    # (some BINX endpoints return flat responses)
    if info is None:
        # Heuristic: a usable flat object will have at least one card field
        card_keys = {"brand", "scheme", "type", "category", "bank", "bankName", "cardBrand"}
        if card_keys.intersection(data.keys()):
            info = data
        else:
            return None

    if not isinstance(info, dict):
        return None

    # brand / scheme
    brand = (
        info.get("brand")
        or info.get("scheme")
        or info.get("cardBrand")
        or info.get("cardScheme")
        or ""
    ).strip().upper() or "UNKNOWN"

    # type  (DEBIT / CREDIT)
    card_type = (
        info.get("type")
        or info.get("cardType")
        or ""
    ).strip().upper() or "UNKNOWN"

    # level / category  (CLASSIC, GOLD, PLATINUM …)
    level = (
        info.get("category")
        or info.get("level")
        or info.get("subBrand")
        or info.get("cardCategory")
        or ""
    ).strip().upper() or "UNKNOWN"

    # bank name
    bank_raw = info.get("bank") or info.get("bankName") or ""
    if isinstance(bank_raw, dict):
        bank = (
            bank_raw.get("name")
            or bank_raw.get("bankName")
            or ""
        ).strip().upper() or "UNKNOWN"
    else:
        bank = str(bank_raw).strip().upper() or "UNKNOWN"

    return {
        "brand": brand,
        "type": card_type,
        "level": level,
        "bank": bank,
    }


async def _fetch_from_binx(
    session: aiohttp.ClientSession,
    bin_number: str,
) -> dict | None:

    url = BINX_API_URL.format(bin=bin_number)
    try:
        async with session.get(
            url,
            timeout=HTTP_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    "BINX API status %s for BIN %s",
                    resp.status,
                    bin_number,
                )
                return None

            # Read raw text first so we can log it when parsing fails
            raw_text = await resp.text()
            if not raw_text or not raw_text.strip():
                logger.warning("Empty response body for BIN %s", bin_number)
                return None

            try:
                import json as _json
                data = _json.loads(raw_text)
            except Exception as json_err:
                logger.error(
                    "JSON decode error for BIN %s: %s | body: %.200s",
                    bin_number,
                    json_err,
                    raw_text,
                )
                return None

            metadata = _parse_binx_data(data)
            if metadata is None:
                logger.warning(
                    "Could not parse BINX response for BIN %s | body: %.300s",
                    bin_number,
                    raw_text,
                )
            return metadata

    except asyncio.TimeoutError:
        logger.error("BINX API timeout for BIN %s", bin_number)
        return None
    except Exception as e:
        logger.error("BINX API failed for BIN %s: %s", bin_number, e)
        return None


async def fetch_bin_metadata(
    session: aiohttp.ClientSession,
    bin_number: str,
    cache: BINCache,
    semaphore: asyncio.Semaphore,
    stats: dict,
) -> dict:

    # Check cache before acquiring semaphore
    cached = cache.get(bin_number)
    if cached is not None:
        stats["cache_hits"] += 1
        return cached

    async with semaphore:
        # Double-check after acquiring semaphore (another coroutine may have
        # already fetched + cached this BIN while we were waiting)
        cached = cache.get(bin_number)
        if cached is not None:
            stats["cache_hits"] += 1
            return cached

        metadata = await _fetch_from_binx(session, bin_number)
        if metadata is None:
            metadata = _unknown_metadata()

        stats["api_calls"] += 1
        cache.set(bin_number, metadata)
        return metadata


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
        "bank_counts": Counter(),
        "type_counts": Counter(),
        "debit_count": 0,
        "credit_count": 0,
        "rows": [],
    }

    # ── Pass 1: collect unique BINs ──────────────────────────────────────────
    unique_bins: set[str] = set()
    with input_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = BIN_PATTERN.search(line)
            if match:
                unique_bins.add(match.group(1))

    logger.info("Found %d unique BINs to look up", len(unique_bins))

    # ── Concurrent BIN lookups ───────────────────────────────────────────────
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
    bin_metadata: Dict[str, dict] = {}

    async with aiohttp.ClientSession(
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
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
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for bin_num, result in zip(unique_bins, results):
        if isinstance(result, Exception):
            logger.error("Lookup failed for BIN %s: %s", bin_num, result)
            stats["errors"] += 1
            bin_metadata[bin_num] = _unknown_metadata()
        else:
            bin_metadata[bin_num] = result

    # ── Pass 2: write enriched output to temp file ───────────────────────────
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".txt")
    tmp_path = Path(tmp_path_str)

    with (
        input_path.open("r", encoding="utf-8", errors="replace") as infile,
        open(tmp_fd, "w", encoding="utf-8") as outfile,
    ):
        for line in infile:
            stats["total_lines"] += 1
            match = BIN_PATTERN.search(line)

            if match:
                stats["bins_found"] += 1
                bin_number = match.group(1)
                metadata = bin_metadata.get(bin_number, _unknown_metadata())

                suffix = _format_metadata(metadata)
                stripped = line.rstrip("\r\n")
                newline = line[len(stripped):]
                line = stripped + suffix + newline

                # Aggregate counts
                stats["bank_counts"][metadata["bank"]] += 1
                stats["type_counts"][metadata["type"]] += 1

                card_type_upper = metadata["type"].upper()
                if card_type_upper == "DEBIT":
                    stats["debit_count"] += 1
                elif card_type_upper == "CREDIT":
                    stats["credit_count"] += 1

                stats["rows"].append(
                    {
                        "line": line.rstrip("\r\n"),
                        "bank": metadata["bank"],
                        "type": metadata["type"],
                        "brand": metadata["brand"],
                        "level": metadata["level"],
                    }
                )

            outfile.write(line)

    logger.info(
        "Done — %d lines, %d BINs enriched, %d API calls, %d cache hits, %d errors",
        stats["total_lines"],
        stats["bins_found"],
        stats["api_calls"],
        stats["cache_hits"],
        stats["errors"],
    )

    return tmp_path, stats