from typing import Final, LiteralString

CONDITIONAL_COUNTER_DECREMENT_TEMPLATE: Final[LiteralString] = """
local counter_value = redis.call("HGET", KEYS[1], KEYS[2])
if counter_value ~= nil then
    redis.call("HINCRBY", KEYS[1], KEYS[2], ARGV[1])
    return true
end

return false
"""
