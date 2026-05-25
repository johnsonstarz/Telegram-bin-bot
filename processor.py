import asyncio
import re
import tempfile
from pathlib import Path

import aiohttp

BIN_PATTERN = re.compile(r"BIN\s*[:\-]\s*(\d{6})", re.IGNORECASE)

BINLIST_URL = "https://lookup.binlist.net/{bin}"


async def lookup_bin(session, bin_number):

    try:

        async with session.get(
            BINLIST_URL.format(bin=bin_number)
        ) as resp:

            if resp.status != 200:
                return {
                    "brand": "UNKNOWN",
                    "type": "UNKNOWN",
                    "level": "UNKNOWN",
                    "bank": "UNKNOWN",
                }

            data = await resp.json()

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

    except:
        return {
            "brand": "UNKNOWN",
            "type": "UNKNOWN",
            "level": "UNKNOWN",
            "bank": "UNKNOWN",
        }


async def process_file(
    input_path,
    cache,
):

    stats = {
        "total_lines": 0,
        "bins_found": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "errors": 0,
    }

    fd, temp_path = tempfile.mkstemp(
        suffix=".txt"
    )

    async with aiohttp.ClientSession() as session:

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

                    bin_number = match.group(1)

                    data = await lookup_bin(
                        session,
                        bin_number,
                    )

                    stats["api_calls"] += 1

                    suffix = (
                        f" | BRAND - {data['brand']}"
                        f" | TYPE - {data['type']}"
                        f" | LEVEL - {data['level']}"
                        f" | BANK - {data['bank']}"
                    )

                    line = (
                        line.rstrip("\n")
                        + suffix
                        + "\n"
                    )

                outfile.write(line)

    return Path(temp_path), stats
