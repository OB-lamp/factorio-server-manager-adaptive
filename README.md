# Factorio 2.0.77 / 2.1.14 / 2.1.16 / 2.1.17 开服工具

Windows 图形化专用服务器管理器，同时支持：

- Factorio 2.0.77 正式版（build 84539）
- Factorio 2.1.14 测试版（build 87180）
- Factorio 2.1.16 测试版（build 87294）
- Factorio 2.1.17 测试版（build 87315）

## 使用

1. 双击 `FactorioServerManager`。
2. 选择服务器存档、模组目录并填写服务器设置。
3. 使用内网穿透时关闭“局域网内可发现”，隧道协议选择 UDP。
4. 点击“启动服务器”。
5. 本机客户端通过 `127.0.0.1:34197` 加入。

工具会在选择游戏和启动服务器前读取 `factorio.exe --version`。选择哪个版本
的 EXE，就使用哪个版本启动服务器；其他版本会被拒绝。配置、日志、锁文件和
服务端写入数据保存在本项目的 `runtime` 目录，不会与图形客户端争用
`%APPDATA%\Factorio\.lock`。

## 自动检测

首次启动且没有 `profile.json` 时，工具会检查 Steam 注册表、Steam 主库、
`libraryfolders.vdf` 中的其他 Steam 库，以及常见独立安装位置，并只采用版本
确实为 2.0.77、2.1.14、2.1.16 或 2.1.17 的 `factorio.exe`。发现多个受支持版本时
自动检测优先选择 2.1.17，用户可通过“浏览”改选其他受支持版本。存档与模组目录使用当前用户的：

- 存档：`%APPDATA%\Factorio\saves`
- 模组：`%APPDATA%\Factorio\mods`

顶部“自动检测”按钮可随时重新执行。找不到游戏时仍可使用“浏览”手动选择。
便携发行包不包含开发电脑的 `profile.json`；用户保存设置后才会在 EXE 旁生成。

令牌、游戏密码和 RCON 密码不会写入 `profile.json`；服务器退出后，运行时
设置中的令牌和游戏密码会被清空。

停止服务器时，工具会发送 RCON `/server-save`，等待存档更新时间变化并
通过 ZIP 完整性检查后，再结束专用服务器进程。此流程用于规避 2.1.x
Windows 测试版处理远程 `/quit` 时可能触发的崩溃。

## 成就标记修复

服务器停止后，可点击“清除成就标记”，将所选 Factorio 2.1.x 存档中的
控制台命令与编辑器标记清零。工具会先在存档目录创建带时间戳的备份，
通过 ZIP 完整性校验后再原子替换原文件。修复时不要让其他 Factorio 进程
同时保存该存档；再次使用 `/c` 或 `/editor` 后需要重新执行此操作。

## 在线玩家快捷指令

服务器启动并进入游戏后，“快捷指令”页会通过内部 RCON 与普通管理指令
`/players online` 自动读取在线玩家，该刷新操作不影响成就。快捷指令以“目标玩家名”输入框为准；可手动输入完整名称，也可点击在线玩家自动填入。随后可给予物品、开关作弊模式、恢复生命、传送到出生点、
清空背包或按半径清除敌人。自定义 Lua 输入框会自动添加 `/sc`，并提供指向
所选玩家的局部变量 `player`。

执行快捷指令使用 `/sc`，会设置存档的控制台命令成就标记；停服并保存后可
使用顶部“清除成就标记”按钮处理。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m py_compile factorio_server_manager\app.py factorio_server_manager\core.py factorio_server_manager\shortcuts.py
```
