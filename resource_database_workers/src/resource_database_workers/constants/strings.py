from typing import Final, LiteralString

from resource_auxillary.strings import NAME_SEPERATOR

INTERNAL_NAME_SEPERATOR: Final[LiteralString] = "-"
assert INTERNAL_NAME_SEPERATOR != NAME_SEPERATOR  # nosec
