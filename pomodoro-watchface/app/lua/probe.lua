-- probe; see tools/device_probe.py + docs/device-recon.md
local L=require"lvgl"
local W,H=L.HOR_RES(),L.VER_RES()
local R={}
local A=function(s)R[#R+1]=tostring(s)end
local T=function(x)return type(x)=="table"or type(x)=="function"end
local B=function(b)return b and"1"or"0"end
local OP=T(io)and io.open
A("E io"..B(T(io)).." os"..B(T(os)).." pkg"..B(T(package)).." exe"..B(T(os)and os.execute).." pop"..B(T(io)and io.popen))
local mkf=function(s)local o,f=pcall(L.Font,"MiSans-Regular",s,"normal")if o then return f end end
local s=""
for m in("lvgl miwear topic dataman vibrator screen navigator activity").gmatch"%S+"do
 if pcall(require,m)then s=s..m.." " end
end
A("req "..s)
A("R:")
local function rd(p)
 if not OP then return nil end
 local f=OP(p,"rb")
 if not f then return end
 local d=f:read(24)
 f:close()
 return d
end
for _,p in ipairs{
"/proc/version","/proc/mounts","/proc/kconfig","/proc/modules","/etc/init.d/rcS",
"/data/app/quickapp/config.json","/data/app/watchface/watchface_list.json","/data/canopus",
"/data/canopus/inbox","/data/canopus/manager","/dev/canopus","/bin/nsh"}do
 local d=rd(p)
 A(p.." "..((d and #d>0)and("OK "..(""..d:gsub("[^%g ]",".")):sub(1,12))or"--"))
end
A("W:")
A(SCRIPT_PATH or"?")
for _,p in ipairs{SCRIPT_PATH,"/data/","/tmp/","/data/canopus/inbox/"}do
 local f=OP and OP(p.."vwprobe.tmp","wb")
 if f then
  f:write"v"f:close()
  local g=OP(p.."vwprobe.tmp","rb")
  local v=""
  if g then v=g:read"*a"g:close()end
  A(p.." "..(v=="v"and"W"or"!"))
 else A(p.."n")end
end
if OP then
 local f=OP((SCRIPT_PATH or".").."/vw_probe.txt","wb")
 if f then f:write(table.concat(R,"\n"))f:close()A("saved")else A("!save")end
end
local PER=8
local n=#R
local pages=math.max(1,math.ceil(n/PER))
local pg=1
local lb
local function dr()
 local t={}
 for i=(pg-1)*PER+1,math.min(pg*PER,n)do t[#t+1]=R[i]end
 t[#t+1]=pg.."/"..pages
 lb:set{text=table.concat(t,"\n")}
end
L.Timer{period=700,cb=function(tm)
 tm:pause()
 local rt=L.Object(nil,{w=W,h=H,bg_color=0,bg_opa=L.OPA(100),border_width=0,outline_width=0,pad_all=0})
 rt:clear_flag(L.FLAG.SCROLLABLE)rt:add_flag(L.FLAG.CLICKABLE)
 lb=L.Label(rt,{text="",w=W-6,h=H-8,text_color=0xffffff,
  align={type=L.ALIGN.TOP_LEFT,x_ofs=3,y_ofs=3},
  text_font=mkf(10)})
 rt:onevent(L.EVENT.CLICKED,function()pg=pg%pages+1 dr()end)
 dr()
end}
function pageOnResume()end
function pageOnPause()end
