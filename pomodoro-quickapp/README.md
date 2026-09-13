# org.pomodoro.band9pro — 小米手环 9 Pro 番茄钟快应用

小米手环 9 Pro（Vela OS，固件 3.1.175）的番茄工作法计时器，**quickapp（rpk）形态**。
使用小米官方 `aiot-toolkit` 构建，可直接被 AstroBox / GMF 表盘自定义工具通过
protobuf 通道安装（见 `../docs/quickapp-route.md`）。

## 功能

- 专注 25 min → 休息 5 min，每 4 轮进入 15 min 长休息
- 250 ms 精度计时，进度条实时显示
- 阶段切换自动震动（`system.vibrator`）
- 中英文界面文案（当前为中文），About 子页
- designWidth=168（Band 9 Pro 官方 DP 宽度，物理 336×480）

## 构建

```sh
npm install          # 安装 aiot-toolkit
npx aiot build --disable-sign   # 开发构建（build/）
npx aiot release     # 签名发布包 → dist/org.pomodoro.band9pro.release.1.0.0.rpk
```

签名材料在 `sign/{debug,release}/`（本地 openssl 自签）。真机安装走 AstroBox
或 GMF 时自签证书可用；如需官方渠道分发请替换为你自己的正式证书。

## 安装（真机）

1. 手机安装 AstroBox（或 GMF 表盘自定义工具），连接手环
2. 选择"快应用安装"，选取 `dist/*.rpk`
3. 安装完成后在应用列表启动「番茄钟」

## 目录

```
src/manifest.json    # 包名 org.pomodoro.band9pro，features、路由、designWidth
src/app.ux           # 应用生命周期
src/Home/index.ux    # 主页面（计时器全部逻辑）
src/About/index.ux   # 关于页
dist/                # 已构建的 rpk（v1.0.0, versionCode 1）
```

## 已知边界（固件决定）

- 通知托盘 API 未向 quickapp 开放（`system.notification` 不存在），提醒用震动代替
- 后台运行受系统限制，切走页面时计时暂停（onHide）；恢复时按剩余时长继续
