import os
import subprocess
import sys
from typing import Final

from constants import ERROR_DIRECTORY_NAME, LOGS_SUBDIRECTORY_NAME

PACKAGE_NAMES: Final[tuple[str, ...]] = (
    "resource_server",
    "auth_server",
    "auxillary",
    "resource_auxillary",
    "resource_database_workers",
)


def main() -> int:
    failed_packages: set[str] = set(PACKAGE_NAMES)

    os.makedirs(ERROR_DIRECTORY_NAME, exist_ok=True)
    os.makedirs(LOGS_SUBDIRECTORY_NAME, exist_ok=True)

    for package_name in PACKAGE_NAMES:
        output_filename: str = os.path.join(
            ERROR_DIRECTORY_NAME, f"{package_name}.json"
        )
        output_logs_filename: str = os.path.join(
            LOGS_SUBDIRECTORY_NAME, f"{package_name}.log"
        )

        try:
            with open(output_logs_filename, "wb") as output_logfile:
                return_code: int = subprocess.Popen(
                    [
                        "deptry",
                        "--config",
                        os.path.join(package_name, "pyproject.toml"),
                        "-o",
                        output_filename,
                        "--no-ansi",
                        package_name,
                    ],
                    stderr=output_logfile,
                ).wait(
                    timeout=5
                )  # Arbitrary 5 second value

            if return_code == 0:
                os.unlink(output_filename)
                os.unlink(output_logs_filename)
                failed_packages.remove(package_name)
        except subprocess.TimeoutExpired:
            raise SystemExit(f"Deptry took too long to process: {package_name}")

    if not failed_packages:
        return 0

    raise SystemExit(f"Deptry flagged packages: {', '.join(failed_packages)}")


if __name__ == "__main__":
    sys.exit(main())
