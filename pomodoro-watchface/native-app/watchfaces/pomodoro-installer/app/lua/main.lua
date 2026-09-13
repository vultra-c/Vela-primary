-- Pomodoro native-module installer watchface (one-shot).
--
-- When the user opens this "watchface", this script runs once inside the
-- stock Lua runtime and submits the bundled Canopus module payload to the
-- device-resident Canopus manager, exactly like the reference project's
-- installer watchfaces:
--
--   1. read the signed CMI1 payload carried as a resource segment of this face
--      (it sits next to the theme scripts under SCRIPT_PATH);
--   2. write it to /data/canopus/inbox/ with plain io.open (the documented,
--      deliberately limited external interface — no execute/debug ops);
--   3. show write + read-back status on screen, then stop.
--
-- The manager (already resident from its own framework installer watchface)
-- picks up the file, verifies CMI1 signature, target id and module hash, and
-- insmods the ELF. The module then registers the 番茄钟 native app into the
-- launcher via app_install + launcher_add.

local lvgl = require("lvgl")
local ok, topic = pcall(require, "topic")

local PAYLOAD_NAME = "@PAYLOAD_NAME@"
local INBOX = "/data/canopus/inbox/"

local W, H = lvgl.HOR_RES(), lvgl.VER_RES()
local root = lvgl.Object(nil, {
    w = W, h = H,
    bg_color = 0x101418, bg_opa = lvgl.OPA(1),
    border_width = 0, outline_width = 0, pad_all = 8,
})
local status = lvgl.Label(root, {
    text = "番茄钟 安装器",
    text_color = 0xffffff, align = lvgl.ALIGN.TOP_MID, y = 40, w = W - 24,
})
local function say(line)
    status:set { text = tostring(line) }
end

local function install()
    -- Probe first: this one line tells us whether the watchface Lua state
    -- opens the standard io library on this firmware (see
    -- docs/userland-loader-blueprint.md, Channel A). If it does not, the
    -- payload cannot be delivered from a watchface at all.
    if type(io) ~= "table" or type(io.open) ~= "function" then
        say("此固件未向表盘脚本开放 io 库")
        return
    end

    local src = SCRIPT_PATH .. PAYLOAD_NAME
    local f = io.open(src, "rb")
    if not f then
        say("payload missing: " .. src)
        return
    end
    local data = f:read("*a")
    f:close()
    if not data or #data < 64 then
        say("payload empty")
        return
    end

    say("payload " .. #data .. " bytes -> inbox")
    local dst = INBOX .. "pomodoro.bin"
    local out = io.open(dst, "wb")
    if not out then
        say("inbox not ready (先安装新版 Canopus 管理器表盘)")
        return
    end
    out:write(data)
    out:close()

    -- read-back check
    local rb = io.open(dst, "rb")
    local back = rb and rb:read("*a") or nil
    if rb then rb:close() end
    if back and #back == #data then
        say("写入+回读 OK, 等待管理器签名校验…")
    else
        say("回读不一致!")
        return
    end

    -- nudge the manager (best effort; not required by all framework versions)
    if ok and topic and topic.publish then
        pcall(topic.publish, "/canopus/install", { file = dst })
    end
end

-- run once, shortly after the face becomes visible
local t = lvgl.Timer { period = 800, cb = function(timer)
    timer:pause()
    install()
end }

pageOnResume = function() end
pageOnPause = function() end
