-- Pomodoro focus timer as a native Vela Lua watchface (Mi Band 9 Pro).
-- Packed into a stock Lua watchface's `_lua/themeN/themeN.lua` segment (<=0x9A4 bytes).
-- C=minified build artifact source; see README for the readable version.
local L=require"lvgl"
local hv,vb=pcall(require,"vibrator")
local W,H=L.HOR_RES(),L.VER_RES()
local D={{t=1500,n="FOCUS",c=0xe05252},{t=300,n="BREAK",c=0x35c28f},{t=900,n="LONG BREAK",c=0x3a8dd6}}
local S={run=false,md=0,rem=1500,rd=0}
local u={}
local function B(d)if hv and vb then pcall(vb.start,{type=0,repeat_count=1,duration=d})end end
local function fmt(m)if m<0 then m=0 end return string.format("%02d:%02d",m//60,m%60)end
local function dw()
  local t=D[S.md+1]
  local p=1-S.rem/t.t
  if p<0 then p=0 elseif p>1 then p=1 end
  u.time:set{text=fmt(S.rem)}
  u.fill:set{w=160*p//1,bg_color=t.c}
  u.mode:set{text=t.n,text_color=t.c}
  u.rd:set{text="ROUND "..S.rd,text_color=t.c}
  u.hint:set{text_color=t.c}
end
local function nx()
  if S.md<1 then
    S.rd=S.rd+1
    if S.rd%4<1 then S.md=2 S.rem=D[3].t B(700)else S.md=1 S.rem=D[2].t B(350)end
  else S.md=0 S.rem=D[1].t B(180)end
  dw()
end
local tk=L.Timer{period=1000,cb=function()
  if not S.run then return end
  S.rem=S.rem-1
  if S.rem<1 then S.run=false nx()else dw()end
end}
tk:pause()
local r=L.Object(nil,{w=W,h=H,bg_color=0x0b0e14,bg_opa=L.OPA(100),border_width=0,outline_width=0,pad_all=0})
r:clear_flag(L.FLAG.SCROLLABLE)
u.mode=L.Label(r,{text="FOCUS",text_color=0xe05252,text_font=L.Font("MiSans-Regular",18,"normal"),align={type=L.ALIGN.TOP_MID,y_ofs=58}})
u.time=L.Label(r,{text=fmt(S.rem),text_color=0xeaf1f8,text_font=L.Font("MiSans-Regular",56,"normal"),align={type=L.ALIGN.CENTER,y_ofs=-8}})
u.rd=L.Label(r,{text="ROUND 0",text_color=0x8fa3bd,text_font=L.Font("MiSans-Regular",16,"normal"),align={type=L.ALIGN.CENTER,y_ofs=58}})
local bar=L.Object(r,{w=160,h=6,radius=3,bg_color=0x27303f,bg_opa=L.OPA(100),border_width=0,outline_width=0,align={type=L.ALIGN.CENTER,y_ofs=104}})
bar:clear_flag(L.FLAG.SCROLLABLE)
u.fill=L.Object(bar,{w=0,h=6,radius=3,bg_color=0xe05252,bg_opa=L.OPA(100),border_width=0,outline_width=0,align={type=L.ALIGN.LEFT_MID,x_ofs=0,y_ofs=0}})
u.fill:clear_flag(L.FLAG.SCROLLABLE)
u.hint=L.Label(r,{text="< RESET    START >",text_color=0x8fa3bd,text_font=L.Font("MiSans-Regular",14,"normal"),align={type=L.ALIGN.BOTTOM_MID,y_ofs=-36}})
local function zn(l,fn)
  local o=L.Object(r,{w=W/2|0,h=H,bg_opa=L.OPA(0),border_width=0,outline_width=0,align={type=l and L.ALIGN.LEFT_MID or L.ALIGN.RIGHT_MID,x_ofs=0,y_ofs=0}})
  o:clear_flag(L.FLAG.SCROLLABLE)
  o:add_flag(L.FLAG.CLICKABLE)
  o:add_flag(L.FLAG.EVENT_BUBBLE)
  o:onevent(L.EVENT.CLICKED,function()fn()B(120)dw()end)
end
zn(true,function()S.run=false tk:pause()S.rem=D[S.md+1].t end)
zn(false,function()S.run=not S.run if S.run then tk:resume()else tk:pause()end end)
function pageOnPause()S.run=false tk:pause()end
function pageOnResume()dw()end
dw()
