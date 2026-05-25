import asyncio
import re
import tempfile
from pathlib import Path

import aiohttp


BIN_PATTERN = re.compile(r"(\d{6})")

API_URL = "https://lookup.binlist.net/{bin}"


async def lookup_bin(session, bin_number):

    try:

        async with session.get(
            API_URL.format(bin=bin_number)
        ) as response:

            if response.status != 200:
                return None

            data = await response.json()

            return {
                "scheme": (
                    data.get("scheme")
                    or "UNKNOWN"
                ).upper(),

                "type": (
                    data.get("type")
                    or "UNKNOWN"
                ).upper(),

                "brand": (
                    data.get("brand")
                    or "UNKNOWN"
                ).upper(),

                "bank": (
                    data.get("bank", {})
                    .get("name")
                    or "UNKNOWN"
                ).upper(),
            }

    except Exception:

        return None


async def process_file(input_path, cache):

    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".txt"
    )

    total = 0
    bins = 0

    async with aiohttp.ClientSession() as session:

        with open(
            input_path,
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as infile:

            lines = infile.readlines()

        with open(
            tmp_path,
            "w",
            encoding="utf-8",
        ) as outfile:

            for line in lines:

                total += 1

                match = BIN_PATTERN.search(
                    line
                )

                if match:

                    bins += 1

                    bin_number = match.group(1)

                    info = await lookup_bin(
                        session,
                        bin_number,
                    )

                    if info:

                        new_line = (
                            f"{line.strip()} "
                            f"| {info['scheme']} "
                            f"| {info['type']} "
                            f"| {info['brand']} "
                            f"| {info['bank']}\n"
                        )

                    else:

                        new_line = (
                            f"{line.strip()} "
                            f"| UNKNOWN | UNKNOWN "
                            f"| UNKNOWN | UNKNOWN\n"
                        )

                    outfile.write(
                        new_line
                    )

                else:

                    outfile.write(line)

    stats = {
        "total_lines": total,
        "bins_found": bins,
        "api_calls": bins,
        "cache_hits": 0,
        "errors": 0,
    }

    return Path(tmp_path), stats