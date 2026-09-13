# Canopus 逆向报告（基于真实发布产物的完整协议复原）

日期：2026-09-13。本轮与前几轮不同：**不再只是从固件推断**，而是直接拿到了 Canopus
的官方发布产物并全部解开。来源：

- `Searchstars/Canopus-Manager-AstroBox-Release` — Canopus 模块管理器的**安装器表盘**
  （`canopus-installer-prod-10p-043+036.v65538.bin`）
- `Searchstars/astrobox-resource-canopus_bluetoothaudio` — 蓝牙音频模块的 AstroBox 资源仓
  （`manifest_v2.json` + 加密安装器）
- `Searchstars/Canopus-Module-BluetoothAudio` — 模块源码（Rust，`Canopus.toml`、ctor 胶水）
- `AstralSightStudios/AstroBox-Public` — AstroBox 官方开源仓（90%+，Tauri/TS）

所有产物均已存入 `docs/canopus-extracted/`（Lua 安装器源码、两个 supervisor ELF、
图标、两份 manifest）。

## 1. 架构真相（之前推断 vs 现在证实）

| 之前（从固件推断） | 现在（从真实产物证实） |
|---|---|
| 猜测 supervisor 是 ELF | 确认：**ARM ET_REL 模块**（ELF32 LE，type=1, machine=40），由 `insmod` 装入内核 modlib |
| 猜测表盘 Lua 是沙箱化的 | 确认：**表盘 Lua 拥有完整 `io` + `os`**（`os.execute`、`io.open`、`os.remove` 都在用）|
| 猜测有 `/data/canopus/inbox` | 确认，但它只用于 `rm -rf` 清理；**模块并不走 inbox** |
| 猜测管理器是原生应用 | 确认：管理器 = supervisor 内置的 quickapp（`com.canopus.manager`），页面 `main/modules/module_detail` |
| 不知道"每次重启要重装" | 确认：modlib 模块**不持久**，重启后必须打开安装器表盘点 Run 重新注入（manifest 原文就是这么写的）|

## 2. 完整安装流程（从 installer_main.lua 原文复现）

```
1. getprop ro.build.version > /data/canopus-installer-firmware-version.tmp   -- os.execute
2. 读回版本号，必须匹配 canopus_supervisor-xiaomi-band-10-pro-<ver>.bin
3. 校验 supervisor：ELF32 LE，e_type=1 (ET_REL)，e_machine=40 (ARM)，
   512 B ≤ size ≤ 256 KB
4. 若 /dev/canopus 不存在：
     os.execute("insmod '<module>' canopus_supervisor")   ← 名称作为 modlib 模块名
5. 写 manager 图标（0x19 头 + 12B 头 + w*h*4 RGBA）→ /data/canopus/manager_icon.bin
6. 每 1000ms 一个 LuaLVGL timer 依次执行（每步都写命令再读状态确认）：
     CMD_RESTORE_AFTER_BOOT 0x4351000A arg0=0   "Loading enabled modules..."
     CMD_INSTALL 0x43510002  arg0=0             "Registering Manager..."
     CMD_INSTALL 0x43510002  arg0=1             "Registering module apps..."
     CMD_INSTALL 0x43510002  arg0=2             "Publishing Launcher entries..."
7. Clear Env 按钮 = os.execute("rm -rf /data/canopus")
```

## 3. /dev/canopus 字符设备 ABI（从 supervisor ELF 符号表+状态帧复原）

- 状态帧（read，384 字节，LE u32 数组）：
  - `w[0]` = `0x43505331` "CPS1" magic；`w[1]` = ABI 版本（=1）
  - `w[5]` pending_op；`w[6]` pending_state（**5 = RESULT_COMPLETED**）
  - `w[8]` error_code（signed32）；`w[9]==w[10]` 且为偶数 = 快照一致（seqlock）
- 命令帧（write，16 字节）：`u32 magic 0x43504331 "CPC1"`、`u32 command`、`u32 arg0`、`u32 0`
- fops：`sup_control_open/close/read/write`；`s_fops` 表、`sup_dispatch`（654 字节）为总入口
- 已知命令：`0x43510002` INSTALL（arg0: 0=管理器, 1=模块 app 注册, 2=Launcher 条目发布）、
  `0x4351000A` RESTORE_AFTER_BOOT。符号表还有 `sup_load_module / sup_stage_package /
  sup_verify_package_at / sup_registry_persist / sup_activate_module / sup_remove_artifact`，
  对应管理器 UI 里的 install/activate/enable/disable/remove/update/rollback/safe_mode 全套操作
  （`canopus_manager_op_*` 系列）。

## 4. 安全模型（supervisor ELF 实证）

- 内嵌 `s_installer_public_key`（32 字节 ed25519 公钥）+ `s_firmware_sha256`（32 字节）
- 包校验：`sup_verify_package_at` → SHA-256 摘要 → `crypto_ed25519_check`
  （完整onna 的 monocypher/ed25519 实现：`fe_*`、`ge_*`、`sha512_*`、`crypto_verify32`）
- 固件指纹绑定：rodata 里有 `'xiaomi-band-10-pro-3.101.043'`、`'3.101.043'`、
  `'CONBINE_LTALM078_T3.101.043_08041658'`（构建号）——target 校验 fail-closed
- v2 传输层：`canopus_transport_v2_decode_request / encode_response`、
  `canopus_proto_validate_request/response`、`render_v2_query_payload`
- 注册表：`/data/canopus/registry.bin`（+ `.tmp` 原子改名），状态机字符串：
  `discovered → installed → verified → active / disabled / disabled-next-boot /
  remove-pending / update-staged / quarantined-next-boot / reboot-required / fail-stop`
- 崩溃防护：`canopus_supervisor_boot_should_safe_mode`、`record_crash` —— 连续崩溃进安全模式

## 5. 模块如何注册原生应用（模块源码 + supervisor 交叉证实）

- 模块 ELF（ET_REL）带 `__attribute__((constructor))`：modlib 装入即执行
  `canopus_mod_prepare(0)` + `canopus_register_module_descriptor()`
- `Canopus.toml` 的 `[native_app]`：`entry = "canopus_module_descriptor"`，
  supervisor 通过 `canopus_supervisor_add_module` → `canopus_supervisor_publish_native_apps`
  调 `app_install` / `launcher_add`（即我此前从 vela_ap.bin 逆出的那组符号）→
  应用出现在系统列表，和原生应用同权限
- 蓝牙音频模块的管理界面同样由 supervisor 的 UI 树协议渲染
  （`canopus_ui_*` / `manager_pages[32052 字节]`，`ui ABI 1.4`）

## 6. 9 Pro (3.1.175) 可行性判定（本轮最重要的结论）

**Canopus 无法在 9 Pro 3.1.175 上运行**，三条独立证据：

1. 全分区扫描（vela_ap / vela_factory / vela_bl / vela_bl2 / D11A06 / bream.patch + 整个 OTA）：
   `insmod` `rmmod` `modlib` 全部 **0 次出现**。modlib 加载器不在本机固件里，
   `insmod` 命令连执行的地方都没有。
2. 官方发布只有 Band 10 Pro 安装器（`10p-030/036/043`），没有 9 Pro 变体；
   supervisor rodata 硬编码 `xiaomi-band-10-pro-3.101.043` + ed25519 指纹校验。
   （`Canopus.toml` 的 targets 列表虽然写着 `xiaomi-band-9-pro-3.1.175`，那只是
   源码层的兼容声明，从未有对应的 9 Pro supervisor 被编译发布。）
3. 管理器 manifest 明言"仅支持 3.101.043/3.101.036 固件"，且 AstroBox 分发侧
   对资源做了加密（bta 安装器整包高熵，无容器 magic）——既拿不到 9 Pro 版，
   也无法给 9 Pro 版签发。

**但 9 Pro 有自己的官方通道**：quickapp（rpk）引擎在固件里完整存在
（`proxyquickapp/wearpb_handler.c`、`/data/quickapp/app/<pkg>`、`rpk_info.json`、
`appList` 升级逻辑、AIOTJS 引擎 `jse_*.cpp` 12 个模块、`saveInstalledAppInfo`）。
GMF 表盘自定义工具 / AstroBox 就是走 protobuf 安装 rpk 的。详见
`docs/quickapp-route.md` 与 `pomodoro-quickapp/`（已用官方 aiot-toolkit 构建出
`org.pomodoro.band9pro.release.1.0.0.rpk`）。

## 7. 对之前结论的修正

- ~~"3.1.175 不在 Canopus 支持列表"~~ → 方向正确但证据不足；本轮补全：**9 Pro 没有
  loader，也永远不会有人为它发 supervisor**（签名+发布侧双重锁死）。
- ~~"表盘 Lua 的 io 未验证"~~ → 官方安装器表盘直接用 `io.open/os.execute/os.remove`，
  我之前的 Probe.face 探针方向完全正确（在 10 Pro 上它必然返回 io=1 exe=1）。
- 原生应用注入在 9 Pro 上的等价物 = **quickapp**（路由进系统应用列表、独立图标、
  可在 launcher 排序），差别只是进程模型（JS VM 而非原生线程）与权限集合。
