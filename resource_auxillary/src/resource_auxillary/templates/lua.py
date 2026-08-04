from typing import Final, LiteralString

CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE: Final[LiteralString] = """
local current = redis.call("GET", KEYS[1])

if current == ARGV[1] then
    redis.call("DEL", KEYS[1])
    return true
end

return false
"""
