-- Device probe (see tools/device_probe.py + docs/device-recon.md).
-- Reports what watchface Lua can actually do on this firmware.
local L=require"lvgl"
local W,H=L.HOR_RES(),L.VER_RES()
local R={}
local A=function(s)R[#R+1]=tostring(s)end
local T=function(x)return type(x)=="table"or type(x)=="function"end
local Y=function(b)return b and"1"or"0"end
A("ENV")
A("io="..Y(T(io)).." os="..Y(T(os)).." pkg="..Y(T(package)).." exe="..Y(T(os)and os.execute).." popen="..Y(T(io)and io.popen))
local q={}
for m in("lvgl dataman topic vibrator screen navigator activity miwear io os package debug coroutine").gmatch"%S+"do
 if pcall(require,m)then q[#q+1]=m end
end
A(table.concat(q,","))
A("READ")
local function rd(p)
 if not(T(io)and io.open)then return nil end
 local f=io.open(p,"rb")
 if not f then return nil end
 local d=f:read(36)
 f:close()
 return d
end
for _,p in ipairs{
"/proc/version","/proc/mounts","/proc/kconfig","/proc/modules","/etc/init.d/rcS",
"/data/app/quickapp/config.json","/data/app/watchface/watchface_list.json","/data/canopus",
"/data/canopus/inbox","/data/canopus/packages","/data/canopus/manager","/dev/canopus",
"/bin/nsh","/data/astrobox"}do
 local d=rd(p)
 A(p.." "..((d and #d>0)and("OK "..(""..d:gsub("[^%g ]",".")):sub(1,22))or"-"))
end
A("WRITE")
local SP=tostring(SCRIPT_PATH or"?")
for _,p in ipairs{SCRIPT_PATH,"/data/","/tmp/","/data/canopus/inbox/","/data/app/"}do
 local f=T(io)and io.open and io.open(p.."vwprobe.tmp","wb")
 if f then
  f:write"v"f:close()
  local g=io.open(p.."vwprobe.tmp","rb")
  local v=""
  if g then v=g:read"*a"g:close()end
  A(p.." "..(v=="v"and"W"or"!"))
 else A(p.."n")end
end
A(SP)
if T(io)and io.open then
 local f=io.open(SP.."/vw_probe.txt","wb")
 if f then f:write(table.concat(R,"\n"))f:close()A("saved")else A("!save")end
end
local n=#R
local rt=L.Object(nil,{w=W,h=H,bg_color=0,bg_opa=L.OPA(100),border_width=0,outline_width=0,pad_all=0})
rt:clear_flag(L.FLAG.SCROLLABLE)rt:add_flag(L.FLAG.CLICKABLE)
local lb=L.Label(rt,{text="",text_color=0xffffff,align={type=L.ALIGN.TOP_LEFT,x_ofs=3,y_ofs=3},text_font=L.Font("MiSans-Regular",11,"normal")})
local PER=10
local pages=math.max(1,math.ceil(n/PER))
local pg=1
local function dr()
 local t={}
 for i=(pg-1)*PER+1,math.min(pg*PER,n)do t[#t+1]=R[i]end
 t[#t+1]=pg.."/"..pages.." tap"
 lb:set{text=table.concat(t,"\n")}
end
rt:onevent(L.EVENT.CLICKED,function()pg=pg%pages+1 dr()end)
function pageOnResume()dr()end
function pageOnPause()end
dr()
