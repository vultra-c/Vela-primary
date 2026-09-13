# 9 Pro 快应用路线：从固件证实的安装通道 + 番茄钟实现

日期：2026-09-13。本文所有结论都有固件证据（`vela_ap.bin` @ load base `0x2C080000`）
或工具链实证（官方 `aiot-toolkit` 构建成功）。

## 1. 设备上的 quickapp 子系统（vela_ap.bin 证据）

| 证据 | 地址/出处 | 含义 |
|---|---|---|
| `proxyquickapp/wearpb_handler.c` | 0x2C6ABC28 附近 | 手机→手环的 rpk 传输走 **小米 Protobuf 协议**（AstroBox 99% 复原的就是它）|
| `"prepare to install qapp: [%s]"` / `"app version: [0x%08lx]"` / `"prepared to receive rpk file."` | 同上 | 逐包接收→校验→落盘 |
| `/data/quickapp/app/` 与 `/data/quickapp/app/%s/%s` | 0x2C6AC2FE 附近 | 安装目录：`app/<包名>/` |
| `/data/app/quickapp/config.json` | `proxyquickapp/config.c` | 应用清单（appList、版本、升级状态）|
| `/quickapp/rpk_info.json` | 同上 | 每个 rpk 的元信息 |
| `"start to install rpkfiles[%lu]: %s"` / `"appList"` | config.c | 批量安装+清单更新 |
| `/data/app/launcher/launcher.db` | launcher 模块 | **应用列表数据库**——装好的 quickapp 从这里出现在桌面 |
| `saveInstalledAppInfo` / `"installedApps count is: %u"` | AIOTJS 框架 | JS 侧记录已装应用 |
| `jse_*.cpp` 12 个模块（file/prompt/media/health/miwear/nativeproxy/request/timers/gui/folme/apppath/bootstrap） | 0x2C708DA9 起 | AIOTJS 引擎的桥接层清单 |
| `__loadApplication` / `app.jsc (fallback app.js)` / `/manifest.json` | jse_gui.cpp | 运行时加载顺序：先编译后源码 |

## 2. JS API 面（决定番茄钟能做什么）

全部 `system.*` 模块（字符串计数取证）：
`router app device configuration storage vibrator prompt audio fetch request
interconnect sensor geolocation brightness cipher crypto file network media
db settings uploadtask folme bin`
**没有 `system.notification`** —— 通知托盘 API（`lvx_notification_insert_message`，
0x2C4F1C44）没有暴露给 quickapp JS。所以番茄钟的阶段提醒用
`system.vibrator`（固件里在）+ `system.prompt.showToast` 实现，这也是
所有米坛快应用的实际做法。`service.health` 是唯一暴露的 service.*。

## 3. 已交付的实现（pomodoro-quickapp/）

```
pomodoro-quickapp/
├── package.json          # 官方 aiot-toolkit 1.1.4 工程文件
├── sign/                 # 自签 release/debug 证书（私有 key 只用于本地签名）
├── dist/org.pomodoro.band9pro.release.1.0.0.rpk   # 成品（9.6 KB）
└── src/
    ├── manifest.json     # designWidth=168（Band 9 Pro 官方推荐值，336x480 物理）
    │                     # features: router/app/device/configuration/storage/
    │                     #           vibrator/prompt/audio
    ├── app.ux
    └── Home/index.ux     # 主页：专注25min→休息5min，每4轮长休息15min
                          #   250ms tick、进度条、阶段切换震动、About 子页
```

构建与验证（本环境实测）：
```sh
npm install                 # aiot-toolkit ^1.0.12（实际装到 1.1.4）
npx aiot build --disable-sign   # 开发包
npx aiot release                # 生成 dist/org.pomodoro.band9pro.release.1.0.0.rpk
unzip -l dist/*.rpk         # META-INF/CERT + manifest.json + app.js + Home/index.js + About/index.js
```
rpk 结构与固件 `wearpb_handler.c` 期望一致（zip 容器 + META-INF/CERT +
manifest.json）。签名用的是本项目自签证书——**真机安装取决于安装器是否校验
官方证书链**：GMF 工具与 AstroBox 均支持自签 rpk（社区现状），官方渠道会拒绝。

## 4. 安装到真机的路径（按可行性排序）

1. **AstroBox（推荐）**：你的手环 + 手机装 AstroBox → "快应用安装" → 选 rpk。
   它走的就是固件里那条 protobuf 通道，无需 root。
2. **表盘自定义工具（GMF）**：米坛社区标配，同样走 protobuf 安装 rpk。
3. **Mi Fitness 隐藏调试页**（`oryonatan/xiaomi-band-development` 的 deploy.sh：
   app_process 注入 dex → ThirdAppDebugFragment → 文件选择器选 rpk）。
   这是国际固件的免费方案。

## 5. 与"原生应用"需求的对照说明

你最初要的是"注入原生应用、同系统级权限"。在 9 Pro 上：
- Canopus 原生模块：**不可能**（无 modlib，见 `docs/canopus-protocol.md` §6）
- quickapp：**可能且合法**——应用出现在 launcher 列表（launcher.db）、
  有独立图标/页面路由/存储/震动，权限边界由固件 manifest features 控制
- 通知托盘：两者都拿不到（JS 无接口；原生调用点 0x2C4F1C44 不可达）。
  若未来某版固件把 `system.notification` 加进 features，本工程
  `Home/index.ux` 的 `vibrate()` 处可以直接补一行调用。
