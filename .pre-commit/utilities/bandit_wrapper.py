import os

import orjson
import sys
import subprocess
from typing import Any

from constants import ERROR_DIRECTORY_NAME


def main(bandit_output_json_filename: str, *files: str) -> int:
    os.makedirs(ERROR_DIRECTORY_NAME, exist_ok=True)
    fpath: str = os.path.join(ERROR_DIRECTORY_NAME, bandit_output_json_filename)

    try:
        subprocess.Popen(
            [
                "bandit",
                "-c",
                "pyproject.toml",
                "-r",
                "-f",
                "json",
                "-o",
                fpath,
                *files,
            ],
            stdout=subprocess.DEVNULL,
        ).wait()
    except subprocess.TimeoutExpired:
        raise SystemExit("Bandit timed out.")

    with open(fpath, "rb+") as output_file:
        output: dict[str, Any] = orjson.loads(output_file.read())
        del output["metrics"]
        output_file.truncate(0)
        output_file.seek(0)

        if output.get("results"):
            output_file.write(
                orjson.dumps(
                    output, option=orjson.OPT_INDENT_2 | orjson.OPT_APPEND_NEWLINE
                )
            )
            return 1

    os.unlink(fpath)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
