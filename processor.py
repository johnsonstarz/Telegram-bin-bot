import re
import tempfile
from pathlib import Path


BIN_PATTERN = re.compile(r"(\d{6})")


async def process_file(input_path, cache):

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt")

    total = 0
    bins = 0

    with open(input_path, "r", encoding="utf-8", errors="ignore") as infile:
        lines = infile.readlines()

    with open(tmp_path, "w", encoding="utf-8") as outfile:

        for line in lines:

            total += 1

            match = BIN_PATTERN.search(line)

            if match:

                bins += 1

                new_line = (
                    f"{line.strip()} "
                    f"| VISA | CREDIT | GOLD | TEST BANK\n"
                )

                outfile.write(new_line)

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