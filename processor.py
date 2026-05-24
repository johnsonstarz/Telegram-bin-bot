"""
processor.py
============
Core file-processing logic.

Responsibilities:
  - Read a .txt file line-by-line (streaming, memory-efficient for large files)
  - Detect BIN lines via regex
  - Fetch/cache metadata for each unique BIN
  - Write the output file, preserving ALL non-BIN content verbatim
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

# ---------------------------------------------------------------------------
# Regex pattern — matches all documented BIN formats:
#   BIN : 448297   (space-colon-space)
#   BIN :448297    (space-colon-no-space)
#   BIN-448297     (dash separator)
# Capture group 1 → the 6-digit BIN string
# ---------------------------------------------------------------------------
BIN_PATTERN = re.compile(r"BIN\s*[:\-]\s*(\d{6})", re.IGNORECASE)

# Binlist.net public API  (no key required, 10 req/min free tier)
BINLIST_URL = "https://lookup.binlist.net/{bin}"

# How many BIN lookups to run concurrently (stay polite to the free API)
MAX_CONCURRENT_LOOKUPS = 5

# HTTP timeout per request (seconds)
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ---------------------------------------------------------------------------
# Metadata fetcher
# ---------------------------------------------------------------------------


async def fetch_bin_metadata(
    session: aiohttp.ClientSession,
    bin_number: str,
    cache: BINCache,
    semaphore: asyncio.Semaphore,
    stats: Dict[str, int],
) -> Dict[str, str]:
    """
    Return a metadata dict for *bin_number*.

    Lookup order:
      1. Local cache  → instant
      2. binlist.net  → network call, result stored in cache
    """
    # 1. Cache hit?
    cached = cache.get(bin_number)
    if cached is not None:
        stats["cache_hits"] += 1
        logger.debug("Cache hit for BIN %s", bin_number)
        return cached

    # 2. Network lookup (bounded by semaphore)
    async with semaphore:
        # Double-check cache inside semaphore to avoid duplicate API calls
        # when multiple lines share the same BIN
        cached = cache.get(bin_number)
        if cached is not None:
            stats["cache_hits"] += 1
            return cached

        url = BINLIST_URL.format(bin=bin_number)
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT) as resp:
                stats["api_calls"] += 1

                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    metadata = _parse_binlist_response(data)
                elif resp.status == 404:
                    # BIN not found in binlist database
                    logger.warning("BIN %s not found (404)", bin_number)
                    metadata = _unknown_metadata()
                elif resp.status == 429:
                    # Rate-limited — return UNKNOWN, don't cache so we retry later
                    logger.warning("Rate-limited by binlist.net for BIN %s", bin_number)
                    stats["errors"] += 1
                    return _unknown_metadata()
                else:
                    logger.warning(
                        "Unexpected HTTP %d for BIN %s", resp.status, bin_number
                    )
                    metadata = _unknown_metadata()

        except asyncio.TimeoutError:
            logger.error("Timeout fetching BIN %s", bin_number)
            stats["errors"] += 1
            return _unknown_metadata()
        except aiohttp.ClientError as exc:
            logger.error("HTTP error fetching BIN %s: %s", bin_number, exc)
            stats["errors"] += 1
            return _unknown_metadata()

    # Store result (including UNKNOWN) so we don't re-hit the API
    cache.set(bin_number, metadata)
    logger.debug("Fetched and cached BIN %s → %s", bin_number, metadata)
    return metadata


def _parse_binlist_response(data: dict) -> Dict[str, str]:
    """
    Extract the four fields we care about from the binlist.net JSON response.

    Response shape (abbreviated):
    {
      "scheme": "visa",
      "type": "debit",
      "brand": "Visa Classic",
      "bank": { "name": "Firelands Federal Credit Union" },
      "country": { "name": "United States" }
    }
    """
    brand = (data.get("scheme") or "UNKNOWN").upper()
    card_type = (data.get("type") or "UNKNOWN").upper()

    # "brand" in binlist maps to what card issuers call the "level" (Classic, Gold…)
    level = (data.get("brand") or "UNKNOWN").upper()

    bank_obj = data.get("bank") or {}
    bank = (bank_obj.get("name") or "UNKNOWN").upper()

    return {"brand": brand, "type": card_type, "level": level, "bank": bank}


def _unknown_metadata() -> Dict[str, str]:
    """Fallback metadata when lookup fails or BIN is not found."""
    return {"brand": "UNKNOWN", "type": "UNKNOWN", "level": "UNKNOWN", "bank": "UNKNOWN"}


def _format_metadata(metadata: Dict[str, str]) -> str:
    """
    Format metadata as the inline suffix appended to BIN lines.

    Example:
      " | BRAND - VISA | TYPE - DEBIT | LEVEL - CLASSIC | BANK - FIRELANDS FEDERAL CREDIT UNION"
    """
    return (
        f" | BRAND - {metadata['brand']}"
        f" | TYPE - {metadata['type']}"
        f" | LEVEL - {metadata['level']}"
        f" | BANK - {metadata['bank']}"
    )


# ---------------------------------------------------------------------------
# Main processing entry point
# ---------------------------------------------------------------------------


async def process_file(
    input_path: Path,
    cache: BINCache,
) -> Tuple[Path, Dict[str, Any]]:
    """
    Process *input_path* and return (output_path, stats_dict).

    Strategy for large files:
      Pass 1 — collect ALL unique BINs from the file.
      Batch   — fetch all unique BINs concurrently.
      Pass 2 — stream file again, enriching BIN lines in-place.

    This avoids holding the whole file in memory.
    """
    stats: Dict[str, Any] = {
        "total_lines": 0,
        "bins_found": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
    }

    # ---- Pass 1: collect unique BINs --------------------------------------
    unique_bins: set[str] = set()
    encoding = _detect_encoding(input_path)

    try:
        with input_path.open("r", encoding=encoding, errors="replace") as fh:
            for line in fh:
                match = BIN_PATTERN.search(line)
                if match:
                    unique_bins.add(match.group(1))
    except OSError as exc:
        logger.error("Cannot read input file: %s", exc)
        raise

    logger.info(
        "Pass 1 complete: found %d unique BINs in '%s'",
        len(unique_bins),
        input_path.name,
    )

    # ---- Batch fetch all unique BINs --------------------------------------
    bin_metadata: Dict[str, Dict[str, str]] = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

    async with aiohttp.ClientSession(
        headers={"Accept-Version": "3", "User-Agent": "BINLookupBot/1.0"}
    ) as session:
        tasks = [
            fetch_bin_metadata(session, bin_num, cache, semaphore, stats)
            for bin_num in unique_bins
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for bin_num, result in zip(unique_bins, results):
        if isinstance(result, Exception):
            logger.error("Unhandled exception for BIN %s: %s", bin_num, result)
            bin_metadata[bin_num] = _unknown_metadata()
            stats["errors"] += 1
        else:
            bin_metadata[bin_num] = result

    # ---- Pass 2: write enriched output ------------------------------------
    # Use a temp file in the same directory so we can atomic-rename if needed
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".txt")
    tmp_path = Path(tmp_path_str)

    try:
        with (
            input_path.open("r", encoding=encoding, errors="replace") as infile,
            open(tmp_fd, "w", encoding="utf-8") as outfile,
        ):
            for line in infile:
                stats["total_lines"] += 1
                enriched_line = _enrich_line(line, bin_metadata, stats)
                outfile.write(enriched_line)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info(
        "Pass 2 complete: %d lines written, %d BINs enriched",
        stats["total_lines"],
        stats["bins_found"],
    )

    return tmp_path, stats


def _enrich_line(
    line: str,
    bin_metadata: Dict[str, Dict[str, str]],
    stats: Dict[str, Any],
) -> str:
    """
    If *line* contains a BIN pattern, append the metadata suffix
    **on the same line**, preserving the trailing newline.

    Non-BIN lines are returned completely unchanged.
    """
    match = BIN_PATTERN.search(line)
    if not match:
        return line  # ← untouched

    bin_number = match.group(1)
    stats["bins_found"] += 1

    metadata = bin_metadata.get(bin_number, _unknown_metadata())
    suffix = _format_metadata(metadata)

    # Preserve the line's original newline character(s) at the end
    stripped = line.rstrip("\r\n")
    newline = line[len(stripped):]  # could be "\n", "\r\n", or ""

    return stripped + suffix + newline


def _detect_encoding(path: Path) -> str:
    """
    Best-effort encoding detection.

    Reads the BOM (byte order mark) if present; defaults to UTF-8.
    """
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
        return "utf-8-sig"  # UTF-8 with BOM

    return "utf-8"
