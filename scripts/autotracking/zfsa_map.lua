local ZfsaMap = require("scripts.zfsa_map")

local current_key = nil
local itemPedestalsEnabled = false

function item_pedestals()
    return itemPedestalsEnabled
end

local stage_to_map = {
	[0] = "Hyrule",
    [10] = "Lake Hylia",
    [11] = "Cave of No Return",
    [13] = "Hyrule Castle",
    [20] = "The Coast",
    [21] = "Village of the Blue Maiden",
    [23] = "Eastern Temple",
    [30] = "Death Mountain Foothills",
    [31] = "The Mountain Path",
    [33] = "Tower of Flames",
    [40] = "The Field",
    [41] = "The Swamp",
    [43] = "Infiltration of Hyrule Castle",
    [50] = "Lost Woods",
    [51] = "Kakariko Village",
    [53] = "Temple of Darkness",
    [60] = "Desert of Doubt",
    [61] = "Desert Temple",
    [63] = "Pyramid",
    [70] = "Frozen Hyrule",
    [71] = "The Ice Temple",
    [73] = "Tower of Winds",
    [80] = "Realm of the Heavens",
    [82] = "The Dark Cloud",
    [83] = "Palace of Winds",
}

local function handle_map_state(key, value)
    if key ~= current_key then
        return
    end

    local map_id, screen_id, layer_id = ZfsaMap.parse_map_state(value)

    if map_id == nil then
        print("ZFSA: Invalid map state: " .. tostring(value))
        return
    end

    print(string.format("ZFSA: Map %d, screen %d, layer %d", map_id, screen_id, layer_id))

	map_id = tonumber(map_id)
	screen_id = tonumber(screen_id)
	layer_id = tonumber(layer_id)
	
	local map_name = stage_to_map[map_id]
	if not map_name then
        print("ZFSA: No map configured for stage " .. map_id)
		return
	end
	
	Tracker:UiHint("ActivateTab", map_name)
end

Archipelago:AddClearHandler("zfsa_map_clear",
    function(slot_data)
        if Archipelago.PlayerNumber == nil
            or Archipelago.PlayerNumber < 0
            or Archipelago.TeamNumber == nil
            or Archipelago.TeamNumber < 0 then
            return
        end
		
		itemPedestalsEnabled = slot_data ~= nil and (slot_data.item_pedestals == true or slot_data.item_pedestals == 1)
		
        current_key = string.format("zfsa_map_%d_%d", Archipelago.TeamNumber, Archipelago.PlayerNumber)

        Archipelago:SetNotify({current_key})
        Archipelago:Get({current_key})
    end
)

Archipelago:AddRetrievedHandler("zfsa_map_initial",
    handle_map_state
)

Archipelago:AddSetReplyHandler("zfsa_map_update",
    handle_map_state
)