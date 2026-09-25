from typing import Final, LiteralString

CONDITIONAL_LOCK_UNSETTING_SCRIPT: Final[LiteralString] = """
    local current = redis.call("GET", KEYS[1])

    if current == ARGV[1] then
        redis.call("DEL", KEYS[1])
        return true
    end

    return false
"""
