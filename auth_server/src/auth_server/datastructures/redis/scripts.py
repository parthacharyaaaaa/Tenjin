from typing import Final, LiteralString

CONDITIONAL_HASH_ENTRY_EVICTION_SCRIPT: Final[LiteralString] = """
    if #ARGV % 2 ~= 0 then
        return redis.error_reply("Expected admin_id/timestamp pairs")
    end

    for index = 2, #ARGV, 2 do
        if tonumber(ARGV[index]) == nil then
            return redis.error_reply("Invalid timestamp")
        end
    end

    local deleted = 0
    for index = 1, #ARGV, 2 do
        local current = redis.call("HGET", KEYS[1], ARGV[index])
        if current then
            local numeric_value = tonumber(current)
            if numeric_value and numeric_value <= tonumber(ARGV[index + 1]) then
                deleted = deleted + redis.call("HDEL", KEYS[1], ARGV[index])
            end
        end
    end

    return deleted
"""
