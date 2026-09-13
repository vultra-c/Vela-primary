# RE 笔记（索引与最新结论）

> 2026-09-13 更新：本轮拿到了 Canopus 官方发布产物并全部解开，同时用官方工具链
> 交付了 9 Pro 可用的番茄钟快应用。旧工作区（pomodoro-watchface）按需求已删除，
> 仍可从 git 历史（6566698）恢复。

## 文档地图

| 文档 | 内容 |
|---|---|
| `canopus-protocol.md` | **Canopus 完整协议复原**：CPS1/CPC1 帧格式、/dev/canopus ABI、ed25519 安全模型、supervisor 525 个符号、安装流程逐行还原 |
| `quickapp-route.md` | **9 Pro 可行路线**：固件内 quickapp 子系统证据链、JS API 清单、rpk 安装通道、番茄钟实现说明 |
| `canopus-extracted/` | 官方产物原件：installer_main.lua（12911 B 源码）、supervisor ELF ×2（036/043）、manager_icon.bin、两份 manifest_v2.json |

## 一页结论

1. **Canopus 的机制**（现已完全证实）：安装器表盘（Lua，有 io+os.execute）→
   `insmod` 装 ARM ET_REL supervisor 进 modlib → supervisor 在 `/dev/canopus`
   提供 CPS1 状态/CPC1 命令帧 → 模块 ELF 同样 insmod，constructor 里注册
   native app（app_install/launcher_add）→ ed25519 签名 + 固件指纹校验。
2. **9 Pro 3.1.175 跑不了 Canopus**：全分区 `insmod/modlib` 出现 0 次；
   官方只发过 Band 10 Pro 安装器；发布侧对资源加密 + 签名锁定。
3. **9 Pro 的正路是 quickapp**：protobuf 装 rpk → `/data/quickapp/app/<pkg>` →
   launcher.db 出现应用。已交付 `pomodoro-quickapp/`（官方工具链构建通过，
   rpk 已产出）。
4. **通知托盘**：原生地址 0x2C4F1C44 存在但 JS/quickapp 无接口；表盘 Lua 与
   quickapp 都只能用震动。

## 固件关键地址（vela_ap.bin，load base 0x2C080000，27474 函数）

| 符号 | 地址 |
|---|---|
| `app_install`（launcher 模块） | 0x2C3CB5D0 |
| `activitymanager_page_register` | 0x2C3CB8C4 |
| `lvx_notification_insert_message` | 0x2C4F1C44 |
| `lvx_notification_init_message` | 0x2C45F390 |
| `miwear_vibrator_run` | 0x2C4602CC |
| quickapp wearpb_handler 字符串簇 | 0x2C6ABC28 / 0x2C6AC2FE |
| AIOTJS jse_* 模块簇 | 0x2C708DA9 起 |

（完整 26 入口点目标包历史版本：git 6566698 `pomodoro-watchface/native-app/targets/`）
