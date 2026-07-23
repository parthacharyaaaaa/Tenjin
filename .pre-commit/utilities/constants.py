import os
from typing import Final, LiteralString

ERROR_DIRECTORY_NAME: Final[LiteralString] = ".pre-commit-errors"
LOGS_SUBDIRECTORY_NAME: Final[LiteralString] = os.path.join(
    ERROR_DIRECTORY_NAME, "logs"
)
