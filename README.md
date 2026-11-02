# StarRailSwitch · 星穹铁道账号切换

《崩坏：星穹铁道》国服 PC 端的多账号快照切换工具。游戏登录界面最多只记住 3 个账号，本工具把登录状态整体保存为“快照”，数量不限，双击即可切换并启动游戏。

## 功能

- **启动页**：大字号账号卡片，显示上次启动时间（刚刚 / N 分钟前 / N 小时前 / 昨天 / 月日），双击卡片自动 **关闭游戏 → 切换账号 → 重新启动**
- **配置页**：保存当前登录、用当前登录更新（令牌过期后重新登录再保存）、重命名、删除、仅切换不启动
- 切换前若当前登录状态未保存，会自动备份为 `_切换前自动备份`
- 浅色 / 深色主题

## 原理

游戏把登录界面的账号列表和登录凭据整体存放在注册表：

```
HKEY_CURRENT_USER\Software\miHoYo\崩坏：星穹铁道
    MIHOYOSDK_ADL_PROD_CN_h3123967166   (REG_BINARY)
```

本工具把这个值原样备份到 `profiles\<名称>.json`，切换时写回注册表再启动游戏。不修改游戏文件、不注入进程、不联网。

> 每份快照保存的是当时的整体状态（当前登录账号 + 登录界面列表中的其他账号）。

## 使用

详细步骤和常见问题见 **[使用说明.md](使用说明.md)**。

**exe 版（无需 Python）**：下载 [`release/StarRailSwitch.zip`](release/StarRailSwitch.zip)，解压后双击 `StarRailSwitch.exe`。

**源码版**：需要 Python 3（自带 tkinter，无第三方依赖）。

1. 下载本仓库，双击 `StarRailSwitch.pyw`（或 `启动.bat`）；exe 版直接双击 `StarRailSwitch.exe`
2. 首次打开会进入配置页，确认游戏路径（默认 `E:\Star Rail\Game\StarRail.exe`，可点「浏览…」修改）
3. 在游戏里登录某个账号 → 点「保存当前登录」并命名；换号登录后重复
4. 回到启动页，双击账号卡片即可切换

程序启动时会请求一次管理员权限，用于关闭和启动游戏（游戏本身以管理员权限运行）。拒绝也能使用，只是每次切换会多弹 UAC。

## 数据存放

| 文件 | 内容 |
|---|---|
| `profiles\*.json` | 账号快照，**含登录凭据，请勿分享或上传** |
| `config.json` | 游戏路径、主题 |

均位于程序（exe 或 .pyw）所在目录，首次启动自动创建，已在 `.gitignore` 中排除。

## 自行打包

```bash
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --windowed --uac-admin --name StarRailSwitch --icon logo.ico --add-data "logo.ico;." StarRailSwitch.pyw
```

生成的 exe 位于 `dist\`。

## 声明

本项目为非官方工具，与米哈游无关。仅读写本机注册表中的登录缓存，但使用第三方工具仍可能违反游戏用户协议，风险自负。
