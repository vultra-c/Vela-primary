local lvgl = require("lvgl")
local fsRoot = SCRIPT_PATH
function imgPath(src)
    return fsRoot .. src
end

local function ImageComponent(root, src, x, y )
    local t = {}

    t.img = lvgl.Image(t.bg, {x = img.x, y = img.y, src = img.src, zoom = lvgl.IMG_ZOOM_NONE})

    -- create animation and put it on hold

    local animimg = t.img:anim {
        run = false,
        start_value = -778,
        end_value = 0,
        time = 6000,
        -- delay = 120,
        repeat_count = lvgl.ANIM_REPEAT_INFINITE,
        -- playback_delay = 1000,
        -- playback_time = 1000,
        path = "linear",
        exec_cb = function(obj, value)
            obj:set { y = value }
        end
    }

    t.imgAnim = animimg
    return t
end


-- local function createWatchface(parent)
--     print('-------create  watchface--------')
--     local t = {}
--     t.root = parent
--     img = {x = 0, y = 0, src = imgPath("1.bin")}
--     t.bg = ImageComponent(parent, img.src, img.x, img.y)
--     t.event_mask = lvgl.Object(parent, {outline_width = 0,border_width = 0,pad_all = 0,bg_opa = lvgl.OPA(0),bg_color = 0x000000,w = lvgl.HOR_RES(), h = lvgl.VER_RES()})
--     return t
-- end

local function uiCreate()
    local root = lvgl.Object(nil, {outline_width = 0,border_width = 0,pad_all = 0,bg_opa = lvgl.OPA(100),bg_color = 0x0,w = lvgl.HOR_RES(), h = lvgl.VER_RES()})
    -- local watchface = createWatchface(root)
    local watchface = {}
    local t = {} 
    img = {x = 0, y = 0, src = imgPath("1.bin")}
    watchface.bg = ImageComponent(parent, img.src, img.x, img.y)
    watchface.event_mask = lvgl.Object(parent, {outline_width = 0,border_width = 0,pad_all = 0,bg_opa = lvgl.OPA(0),bg_color = 0x000000,w = lvgl.HOR_RES(), h = lvgl.VER_RES()})
    watchface.event_mask:add_flag(lvgl.FLAG.CLICKABLE) --  we accept event here
    watchface.event_mask:add_flag(lvgl.FLAG.EVENT_BUBBLE)
    return watchface
end

local t = uiCreate()

function pageOnPause()
    -- print("pageOnPause.\n");
    t.animStart = false
    t.bg.imgAnim:set{
        run = t.animStart,
    }
end

function pageOnResume()
    -- print("pageOnResume.\n");
    t.animStart = true
    t.bg.imgAnim:set{
        run = t.animStart,
    }
end

-- t.event_mask:onClicked(function (obj, code)
--     print("onClicked")
--     t.animStart = true
--     t.bg.imgAnim:set{
--         run = t.animStart,
--     }
-- end)
