import os

import orjson
import sys
from typing import Any


def main(bandit_output_json_filepath: str) -> int:
    with open(bandit_output_json_filepath, "rb+") as output_file:
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

    os.unlink(bandit_output_json_filepath)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
