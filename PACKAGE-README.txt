Factorio 2.0.77 / 2.1.14 / 2.1.16 / 2.1.17 自适配开服工具（Windows x64）
==================================================================

运行方法
--------
双击 FactorioServerManager.exe。
本程序已包含 Python 运行环境，不需要安装 Python。

首次启动会自动检查 Steam 主库、其他 Steam 库和常见安装位置，识别所选
factorio.exe 的实际版本并启动对应服务器。支持 2.0.77、2.1.14、2.1.16 和 2.1.17，并自动使用
当前 Windows 用户的 %APPDATA%\Factorio\saves 与 mods。找不到时可手动浏览。

文件说明
--------
- FactorioServerManager.exe：开服工具主程序
- _internal：程序运行库，必须与 EXE 保持在同一目录
- profile.json：首次保存设置后自动生成，不随便携包分发
- README.md：完整功能说明

程序运行时会在 EXE 所在目录创建 runtime 文件夹，并从同目录读取或写入
profile.json。请将整个文件夹放在具有写入权限的位置，不要直接从 ZIP 内运行。

安全说明
--------
profile.json 不包含加入密码、Factorio 令牌或 RCON 密码。快捷指令执行时使用
/sc，会设置脚本命令成就标记；普通的“刷新玩家”使用 /players online，不影响
成就。停服后可使用“清除成就标记”功能，操作前会自动备份存档。

如果 Windows SmartScreen 提示未知发布者，这是因为该本地构建未购买代码签名
证书；可核对 ZIP 或 EXE 的 SHA-256 后再运行。
