import asyncio
import re
import tempfile
from pathlib import Path

import aiohttp


BIN_PATTERN = re.compile(r"(\d{6})")

API_URL = "https://api.binx.io/bin/{bin}"

MAX_CONCURRENT_LOOKUPS = 35


async def lookup_bin(
    session,
    bin_number,
):

    try:

        async with session.get(
            API_URL.format(
                bin=bin_number
            ),
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        ) as response:

            if response.status != 200:

                return None

            data = await response.json()

            return {
                "scheme": (
                    data.get("brand")
                    or data.get("scheme")
                    or "UNKNOWN"
                ).upper(),

                "type": (
                    data.get("type")
                    or "UNKNOWN"
                ).upper(),

                "brand": (
                    data.get("level")
                    or data.get("category")
                    or "UNKNOWN"
                ).upper(),

                "bank": (
                    data.get("bank_name")
                    or data.get("bank")
                    or "UNKNOWN"
                ).upper(),
            }

    except Exception:

        return None


async def process_file(
    input_path,
    cache,
):

    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".txt"
    )

    total = 0
    bins = 0

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_LOOKUPS
    )

    async with aiohttp.ClientSession() as session:

        with open(
            input_path,
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as infile:

            lines = infile.readlines()

        async def process_line(
            line,
        ):

            match = BIN_PATTERN.search(
                line
            )

            if not match:

                return line

            bin_number = match.group(1)

            async with semaphore:

                info = await lookup_bin(
                    session,
                    bin_number,
                )

            if info:

                return (
                    f"{line.strip()} "
                    f"| {info['scheme']} "
                    f"| {info['type']} "
                    f"| {info['brand']} "
                    f"| {info['bank']}\n"
                )

            return (
                f"{line.strip()} "
                f"| UNKNOWN "
                f"| UNKNOWN "
                f"| UNKNOWN "
                f"| UNKNOWN\n"
            )

        tasks = []

        for line in lines:

            total += 1

            if BIN_PATTERN.search(line):

                bins += 1

            tasks.append(
                process_line(line)
            )

        processed_lines = await asyncio.gather(
            *tasks
        )

        with open(
            tmp_path,
            "w",
            encoding="utf-8",
        ) as outfile:

            outfile.writelines(
                processed_lines
            )

    stats = {
        "total_lines": total,
        "bins_found": bins,
        "api_calls": bins,
        "cache_hits": 0,
        "errors": 0,
    }

    return Path(tmp_path), stats