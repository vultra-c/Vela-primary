# 逆向重写 Canopus 框架的可行性报告（中文）

回应问题："Canopus 是他们用 AI 做出来的，你不能自己逆向出来吗？需要什么资源？"

## 一、先说结论

**可以做，但有明确的边界。** 我已经在本机对固件做完第二轮、第三轮验证，下面每条都给出可复现的搜索结果（`tools/symbol_hunt.py` + `tools/target_pack.py`）：

| 事实 | 证据（vela_ap.bin / 3.1.175 OTA） |
|---|---|
| ① AP 固件里**没有内核模块加载器** | `insmod`/`rmmod`/`nxmod`/`loadmodule`/`execmodule` 出现次数均为 **0** |
| ② AP 固件里**没有 ELF 加载器** | `0x7f 'ELF'` magic **0 次**；无 `binfmt`、无 `NXFLAT`、无 `dlopen`/`dlsym`、无 `CONFIG_BINFMT`/`CONFIG_MODULE` 字符串 |
| ③ factory/bl2/bl 分区同样没有 | 已解包 `vela_factory.bin`(2.2MB) 逐项搜索，结果同上；`bl/bl2` 更小，同样为空 |
| ④ 有完整 NSH shell 代码，但是**内建**的 | `nsh_main` 与 `task/task_start.c` 相邻（builtin 注册表）；`/bin/nsh` 紧邻 NSH 的 `HOME=/root` 串 |
| ⑤ NuttX 10.3.0 内核在 AP，任务/线程模型完整 | `group/group_create.c`、`task/task_posixspawn.c`、`spawn/lib_psa_*.c`、`mmap/fs_rammap.c`、`procfs/fs_procfs.c` |
| ⑥ **目标 API 全部存在且已定位** | `app_install` `0x2C44B5D0`、`activitymanager_page_register` `0x2C44B8C4`、`app_launcher_add` `0x2C2A7CB8`、`miwear_vibrator_run` `0x2C4602CC`（57 个调用者） |
| ⑦ **系统通知 API 确实存在** | `lvx_notification_init_message` `0x2C45F390`、`lvx_notification_insert_message` `0x2C4F1C44`、`lvx_notification_remove_message` `0x2C4ED540` 等七个 |
| ⑧ 存在一条**不允许动态加载**的原生桥 | AIOTJS `jse_nativeproxy.cpp` / `NativeProxy` / `__folme_native_require__` / `native://` URI 正则 |
| ⑨ `LUA_CPATH=…?.so` 是 Lua 编译期默认串 | 不代表可用：`package.loadlib` 需要不存在的 `dlopen`，且表盘 Lua 未注册 `io`/`os`/`package` |

### 一处重要修正

Canopus 上游 README 支持列表只有：手环 10 Pro `3.101.036`、手环 10 Pro `3.101.043`、手环 11 `4.100.139`。**手环 9 Pro `3.1.175` 不在其中**；框架按 `targets/<target-id>.env` 选私有 ABI 后端，未知 target 会 fail closed。所以 9 Pro 的 target pack 必须我们自己写——本仓库的 `tools/target_pack.py` 已经做出了它的静态分析部分（26 个入口点中 25 个精确命中）。

### 这意味着什么

Canopus 的加载路径是：**Lua 表盘 → 把签名载荷写进 `/data/canopus/inbox` → 常驻管理器（本身也是模块/原生代码）→ 用内核提供的模块接口 `insmod` 载入 ELF**。

关键点：**NuttX 的 `insmod` 是内核配置项（`CONFIG_MODULE=y` 时才有 `sys_insmod` 系统调用）**。这台设备的量产固件把模块子系统裁剪掉了——所以 Canopus 才需要"管理器常驻 + 每次开机重新激活"，而且它的管理器自己八成是先通过某种已有漏洞/接口把模块支持带进内核（或干脆用自有 loader）。

我能逆向/重写的部分和不能的部分：

| 部分 | 我能不能做 | 缺什么 |
|---|---|---|
| Lua 表盘（载荷投递器） | ✅ 已经做好 | 无 |
| 管理器（resident supervisor） | ⚠️ 能写代码，但**没有可用的注入入口** | 见二.1 |
| 内核模块本体（番茄钟原生应用） | ⚠️ 骨架已写好，**能编译成 ELF，但没有 loader 会加载它** | 见二.1/二.2 |
| 每固件符号表（target pack） | ✅ **完全可做**：静态分析 vela_ap.bin 解出 `app_install`/`launcher_add` 等地址 | 只要算力时间，无需外部资源 |
| CMI1 签名/校验 | ✅ 可自己定义（自研协议不必兼容他们的签名） | 无 |

## 二、我需要的资源清单（按优先级）

### 1.【关键，决定成败】一个"原生代码执行入口"（等价于他们框架的 bootstrap）

固件里没有任何动态加载原生代码的通道。表盘 Lua 沙箱里没有 `io`/`os`，也不能加载 .so。所以重写框架的第一步不是写内核模块，而是找到以下任意一条：

- **a. 已越狱/已开启 ADB 的设备**：有 root shell 就能从用户态 `insmod`（如果内核真有 CONFIG_MODULE，只是被 NSH 隐藏；或干脆用 `/dev/mem` 类节点自己实现加载器）。给我一台能 adb shell 的手环 + 我生成的载荷，我就能在真机上验证。
- **b. 他们框架管理器的 `.elf`/`.bin` 模块文件**（哪怕是别人编译好的 BluetoothAudio 模块产物 `bluetooth-audio.elf`）：我可以逆向它拿到 CMI1 容器格式、管理器与模块的真实握手协议、以及它调用的固件符号表——**这是最快的一条路**，等于拿到了"答案卷"。
- **c. 设备上已存在的漏洞入口**（如 sync/表盘安装路径的路径穿越、蓝牙协议栈漏洞等）：这类漏洞需要真机调试才能发现，纯静态分析给不了。

**如果你能提供 b（任何一个已编译的 Canopus 模块文件），我就能把整个协议逆向出来，然后自研一套不依赖他们的等价框架。**

### 2.【高】一台可拆解调试的真机（小米手环 9 Pro，任意固件版本）

用途：
- dump 出厂后真实文件系统（`/data/app/watchface/`、`/data/quickapp/` 等，比 OTA 里的静态文件多出运行时状态）
- 验证内核是否真的编译了 `CONFIG_MODULE`（看 `/proc/kconfig`、NSH 内置命令表、`/sys/module`）
- 调试器（SWD/JLink）可以直接读内存解出所有运行时函数地址，比静态猜偏移准确 100 倍

### 3.【中】任一 Canopus 生态的构建产物（不限于源码）

- `scripts/build-device.sh` 的输出 `build/<target>/bluetooth-audio.elf`（README 说这是他们验证过的产物）
- 他们的安装器表盘 `.face` 文件（`watchfaces/canopus_hello`、`watchfaces/bluetooth-audio-prod/...`）
- AstroBox 社区分发的任何 `.face` 安装包

有其中任何一样，我就能逆向出 CMI1 格式、签名校验位置、模块与固件的调用约定。

### 4.【低，可选】固件符号辅助

不必需，但能加速：
- 小米开源的 [openvela](https://github.com/open-vela) 仓库中对应 `device/xiaomi` 的 BSP 源码（如果他们开放过）——能让我的符号解析从"猜测+验证"变成"直接对照"
- 任何泄露/放出的 Vela SDK 头文件（`miwear_*`、`launcher_app_descriptor` 结构体定义）

### 5.【环境】本地工具链（我自己装，不用你管）

- `rustup` nightly + `thumbv8m.main-none-eabi` target（交叉编译模块用）
- `arm-none-eabi-gcc` + `ld.lld`（重定位链接）
- capstone（已经在用）做 ARM Thumb-2 反汇编

## 三、不依赖任何外部资源已经做完的事

这些是纯静态分析 + 本地工具链能做的，**不需要你提供任何东西**，现在已经落地：

1. ✅ **完整 target pack 已生成**：`tools/target_pack.py` → `native-app/targets/xiaomi-band-9-pro-3.1.175.md` / `.json`。26 个入口点 25 个精确命中（`app_install`、`app_unregister`、`activitymanager_page_register`、`app_launcher_add`、`launcher_page_*`、`create_app_icon`、`sort_apps`、`miwear_vibrator_run/cancel/set_mode`、`lua_vibrator_start`、七个 `lvx_notification_*`），每个还带函数内部 `ldr/str [rX+imm]` 字段偏移证据，用来重建 `launcher_app_descriptor` / `firmware_page_descriptor` / notification message 的结构布局。唯一没解出的是 `app_lookup`：这个固件确实没有这个字符串，我没有把它硬套到不相干的 `quickapp_get_appinfo` 上。
2. ✅ **页面框架调用约定已归档**：`pagemanager` 的 `activitymanager_page_register`、`on_resume_wrapped`、`on_destroy_wrapped`（含 `on_ui_destroy_async`、`ui destroy stack`、`common_stack_push`）都已定位并写入 target pack。
3. ✅ **自研 loader 蓝图已写完**：`docs/userland-loader-blueprint.md`。里面把"没有 loader"这一结论量化成表格，列出四条候选通道（数据注入 / AIOTJS 原生桥 / 常驻 supervisor / 直接刷机）、常驻 supervisor 必须满足的契约，以及每条通道的解封条件。
4. ⚠️ **骨架自洽但未编译**：`native-app/` 现在自带 ABI 定义与 host stub，代码是自洽的；但**当前环境没有 Rust 工具链**（`rustc`/`cargo` 均不存在），所以我无法在此处跑 `cargo test`，也没有把它说成"编译通过"。你在有 Rust 的机器上跑 `cargo test` 即可验证状态机与发布阶段逻辑。

## 四、一句话总结

**逆向 Canopus 框架本身不是问题，问题是这台固件里没有留给我们的"口子"。**
第三节的 4 件事已经做完 3 件、第 4 件（编译验证）被环境缺 Rust 工具链卡住。
接下来只要给我下面任意一样，就能继续往下走：

- **b. 任意一个 Canopus 模块 ELF 产物**（哪怕是别人编译好的 `bluetooth-audio.elf`）→ 我就能拿到 CMI1 容器格式、模块与管理器的真实握手协议、真正的 import 表，把"设计"变成"实现"。
- **a. 一台能 adb / 调试的真机**（任意固件版本）→ 验证数据注入通道、测 NSH 是否可达、用 `/proc` 和内存 dump 代替静态猜测。
- **c. 任意一个 Canopus 安装表盘 `.face`** → 反推出安装请求的线格式和签名校验位置。

在你提供这些之前，可用的成品是 `dist/Pomodoro.face`（Lua 番茄钟，含震动通知），以及一套已经写好、只等 loader 的原生应用源码树。
