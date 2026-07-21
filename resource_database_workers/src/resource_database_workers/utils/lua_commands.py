from typing import Final, LiteralString

CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE: Final[LiteralString] = """
local current = redis.call("GET", KEYS[1])

if current == ARGV[1] then
    redis.call("DEL", KEYS[1])
    return true
end

return false
"""

CONDITIONAL_COUNTER_DECREMENT_TEMPLATE: Final[LiteralString] = """
local counter_value = redis.call("HGET", KEYS[1], KEYS[2])
if counter_value ~= nil then
    redis.call("HINCRBY", KEYS[1], KEYS[2], ARGV[1])
    return true
end

return false
"""
