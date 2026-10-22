local M = {}

function M.parse_map_state(value)
    if type(value) ~= "string" then
        return nil
    end

    local map_id, screen_id, layer_id =
        value:match("^(%d+):(%d+):(%d+)$")

    if not map_id then
        return nil
    end

    map_id = tonumber(map_id)
    screen_id = tonumber(screen_id)
    layer_id = tonumber(layer_id)

    if screen_id < 0 or screen_id > 99 then
        return nil
    end

    if layer_id < 0 or layer_id > 7 then
        return nil
    end

    return map_id, screen_id, layer_id
end

return M