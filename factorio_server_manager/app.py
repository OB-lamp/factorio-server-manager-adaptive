from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .achievement_flags import AchievementFlagError, clear_achievement_flags
from .core import (
    ConfigurationError,
    RuntimeFiles,
    ServerProfile,
    build_server_command,
    load_profile,
    parse_comma_list,
    parse_name_lines,
    redact_command,
    save_profile,
    scrub_runtime_secrets,
    write_runtime_files,
)
from .rcon import RconError, execute_rcon
from .path_discovery import (
    discover_factorio_executables,
    factorio_user_paths,
    newest_save,
)
from .shortcuts import (
    ShortcutError,
    build_online_players_query,
    build_player_sc_command,
    checked_integer,
    parse_online_players,
)


SUPPORTED_FACTORIO_VERSIONS = ("2.0.77", "2.1.14", "2.1.16")
APP_TITLE = "Factorio 2.0.77 / 2.1.14 / 2.1.16 自适配开服工具"
PROJECT_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
PROFILE_PATH = PROJECT_ROOT / "profile.json"
RUNTIME_DIR = PROJECT_ROOT / "runtime"


def default_profile() -> ServerProfile:
    saves_directory, mods_directory = factorio_user_paths()
    executables = discover_factorio_executables()
    latest_save = newest_save(saves_directory)
    return ServerProfile(
        factorio_exe=str(executables[0]) if executables else "",
        save_file=str(latest_save) if latest_save else "",
        mods_directory=str(mods_directory),
    )


def detect_factorio_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError(f"无法读取 Factorio 版本：{exc}") from exc

    version_line = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip().startswith("Version:")),
        "",
    )
    if result.returncode != 0 or not version_line:
        raise ConfigurationError("无法从所选 factorio.exe 读取版本号")
    return version_line.removeprefix("Version:").split("(", 1)[0].strip()


def wait_for_saved_zip(path: Path, previous_mtime_ns: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            if stat.st_mtime_ns != previous_mtime_ns and stat.st_size > 0:
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is None:
                        return
        except (FileNotFoundError, OSError, zipfile.BadZipFile):
            pass
        time.sleep(0.25)
    raise RconError("等待服务器存档写入完成超时；为保护存档，未强制结束进程")


class ServerManagerApp:
    BG = "#10151d"
    PANEL = "#18212d"
    INPUT = "#202b39"
    TEXT = "#edf3f8"
    MUTED = "#9badbd"
    ACCENT = "#f3a13b"
    GOOD = "#55c98d"
    WARN = "#e7bd58"
    BAD = "#ef6b73"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._detected_profile_version = ""
        self.profile = load_profile(PROFILE_PATH, default_profile())
        self.profile = self._repair_discovered_paths(self.profile)
        self.process: subprocess.Popen[str] | None = None
        self.runtime_files: RuntimeFiles | None = None
        self._control_rcon_password = ""
        self._control_rcon_port = 0
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._closing_after_stop = False
        self._stop_requested = False
        self._last_failure_hint = ""
        self._player_refresh_pending = False
        self._shortcut_action_buttons: list[ttk.Button] = []

        self._make_vars()
        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._load_profile_into_ui()
        self._refresh_saves()
        self.root.after(100, self._drain_log_queue)

    def _make_vars(self) -> None:
        self.exe_var = tk.StringVar()
        self.save_var = tk.StringVar()
        self.mods_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.description_var = tk.StringVar()
        self.tags_var = tk.StringVar()
        self.max_players_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.game_password_var = tk.StringVar()
        self.public_var = tk.BooleanVar()
        self.lan_var = tk.BooleanVar()
        self.verify_var = tk.BooleanVar()
        self.token_var = tk.StringVar()
        self.autosave_interval_var = tk.StringVar()
        self.autosave_slots_var = tk.StringVar()
        self.afk_var = tk.StringVar()
        self.auto_pause_var = tk.BooleanVar()
        self.pause_connect_var = tk.BooleanVar()
        self.allow_commands_var = tk.StringVar()
        self.whitelist_enabled_var = tk.BooleanVar()
        self.rcon_enabled_var = tk.BooleanVar()
        self.rcon_port_var = tk.StringVar()
        self.rcon_password_var = tk.StringVar()
        self.status_var = tk.StringVar(value="服务器未运行")
        self.detected_version_var = tk.StringVar(
            value=(f"已检测：{self._detected_profile_version}" if self._detected_profile_version else "版本未检测")
        )
        self.shortcut_target_name_var = tk.StringVar()
        self.shortcut_item_var = tk.StringVar(value="iron-plate")
        self.shortcut_count_var = tk.StringVar(value="100")
        self.shortcut_radius_var = tk.StringVar(value="50")

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("1080x720")
        self.root.minsize(900, 640)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("App.TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("Panel.TLabel", background=self.PANEL, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.PANEL, foreground=self.MUTED)
        style.configure("Panel.TCheckbutton", background=self.PANEL, foreground=self.TEXT)
        style.map("Panel.TCheckbutton", background=[("active", self.PANEL)])
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.INPUT, foreground=self.MUTED, padding=(18, 9))
        style.map("TNotebook.Tab", background=[("selected", self.PANEL)], foreground=[("selected", self.TEXT)])
        style.configure("Treeview", background=self.INPUT, fieldbackground=self.INPUT, foreground=self.TEXT, rowheight=26)
        style.map("Treeview", background=[("selected", self.ACCENT)], foreground=[("selected", "#151515")])
        style.configure("Treeview.Heading", background=self.BG, foreground=self.TEXT)
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#151515", padding=(16, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#ffb65c"), ("disabled", "#5d5141")])
        style.configure("Danger.TButton", background=self.BAD, foreground="white", padding=(16, 8))
        style.map("Danger.TButton", background=[("active", "#ff8188"), ("disabled", "#5a4248")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="自动识别 Factorio 2.0.77、2.1.14 或 2.1.16 · 独立数据目录、RCON、快捷指令与实时控制台",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        paths = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        paths.pack(fill="x", pady=(0, 12))
        self._path_row(paths, 0, "Factorio", self.exe_var, self._browse_exe)
        ttk.Button(paths, text="自动检测", command=self._auto_detect_paths).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(paths, textvariable=self.detected_version_var, style="Muted.TLabel").grid(
            row=0, column=4, padx=(10, 0), sticky="w"
        )
        self.save_combo = self._path_row(paths, 1, "服务器存档", self.save_var, self._browse_save, combo=True)
        ttk.Button(paths, text="刷新", command=self._refresh_saves).grid(row=1, column=3, padx=(6, 0))
        self._path_row(paths, 2, "模组目录", self.mods_var, self._browse_mods)
        paths.columnconfigure(1, weight=1)

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.pack(fill="x", pady=(0, 12))
        self.status_label = tk.Label(
            actions,
            textvariable=self.status_var,
            bg=self.INPUT,
            fg=self.MUTED,
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=12,
            pady=8,
        )
        self.status_label.pack(side="left")
        ttk.Button(actions, text="保存设置", command=self._save_profile).pack(side="right")
        self.achievement_button = ttk.Button(
            actions,
            text="清除成就标记",
            command=self._clear_achievement_flags,
        )
        self.achievement_button.pack(side="right", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止服务器", style="Danger.TButton", command=self._stop_server)
        self.stop_button.pack(side="right", padx=8)
        self.start_button = ttk.Button(actions, text="启动服务器", style="Accent.TButton", command=self._start_server)
        self.start_button.pack(side="right")

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.basic_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=18)
        self.rules_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=18)
        self.access_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=18)
        self.shortcuts_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=14)
        self.console_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.notebook.add(self.basic_tab, text="基本设置")
        self.notebook.add(self.rules_tab, text="规则与 RCON")
        self.notebook.add(self.access_tab, text="管理员与白名单")
        self.notebook.add(self.shortcuts_tab, text="快捷指令")
        self.notebook.add(self.console_tab, text="服务器控制台")
        self._build_basic_tab()
        self._build_rules_tab()
        self._build_access_tab()
        self._build_shortcuts_tab()
        self._build_console_tab()
        self._set_running_state(False)

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        *,
        combo: bool = False,
    ):
        ttk.Label(parent, text=label, style="Panel.TLabel", width=12).grid(row=row, column=0, sticky="w", pady=5)
        if combo:
            control = ttk.Combobox(parent, textvariable=variable)
        else:
            control = ttk.Entry(parent, textvariable=variable)
        control.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, padx=(8, 0), pady=5)
        return control

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.Variable, *, show: str = "", width: int = 34):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=7)
        entry = ttk.Entry(parent, textvariable=variable, width=width, show=show)
        entry.grid(row=row, column=1, sticky="ew", padx=(12, 28), pady=7)
        return entry

    def _build_basic_tab(self) -> None:
        tab = self.basic_tab
        self._entry_row(tab, 0, "服务器名称", self.name_var)
        self._entry_row(tab, 1, "服务器描述", self.description_var)
        self._entry_row(tab, 2, "标签（逗号分隔）", self.tags_var)
        self._entry_row(tab, 3, "最大玩家数（0=无限）", self.max_players_var)
        self._entry_row(tab, 4, "游戏端口（UDP）", self.port_var)
        self._entry_row(tab, 5, "加入密码", self.game_password_var, show="●")

        options = ttk.Frame(tab, style="Panel.TFrame")
        options.grid(row=0, column=2, rowspan=6, sticky="nsew", padx=(10, 0))
        ttk.Label(options, text="可见性与账户验证", style="Panel.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Checkbutton(options, text="局域网内可发现", variable=self.lan_var, style="Panel.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Checkbutton(options, text="发布到官方服务器列表", variable=self.public_var, style="Panel.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Checkbutton(options, text="要求 Factorio 正版账户验证", variable=self.verify_var, style="Panel.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Label(options, text="官方账户令牌（仅公开服务器需要）", style="Muted.TLabel").pack(anchor="w", pady=(15, 4))
        ttk.Entry(options, textvariable=self.token_var, show="●", width=35).pack(fill="x")
        ttk.Label(
            options,
            text="令牌与密码不会保存到 profile.json；服务器停止后运行时配置会被清空。",
            style="Muted.TLabel",
            wraplength=360,
        ).pack(anchor="w", pady=(12, 0))
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)

    def _build_rules_tab(self) -> None:
        tab = self.rules_tab
        self._entry_row(tab, 0, "自动保存间隔（分钟）", self.autosave_interval_var)
        self._entry_row(tab, 1, "自动保存槽位数", self.autosave_slots_var)
        self._entry_row(tab, 2, "AFK 踢出（分钟，0=关闭）", self.afk_var)
        ttk.Label(tab, text="脚本命令权限", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Combobox(
            tab,
            textvariable=self.allow_commands_var,
            values=("admins-only", "false", "true"),
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", padx=(12, 28), pady=7)
        ttk.Checkbutton(tab, text="无人在线时自动暂停", variable=self.auto_pause_var, style="Panel.TCheckbutton").grid(row=4, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Checkbutton(tab, text="玩家连接过程中暂停", variable=self.pause_connect_var, style="Panel.TCheckbutton").grid(row=5, column=0, columnspan=2, sticky="w", pady=7)

        rcon = ttk.Frame(tab, style="Panel.TFrame")
        rcon.grid(row=0, column=2, rowspan=6, sticky="nsew", padx=(24, 0))
        ttk.Label(rcon, text="RCON 远程控制", style="Panel.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Checkbutton(rcon, text="启用 RCON", variable=self.rcon_enabled_var, style="Panel.TCheckbutton").pack(anchor="w", pady=(12, 8))
        ttk.Label(rcon, text="RCON TCP 端口", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(rcon, textvariable=self.rcon_port_var).pack(fill="x", pady=(3, 10))
        ttk.Label(rcon, text="RCON 密码", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(rcon, textvariable=self.rcon_password_var, show="●").pack(fill="x", pady=(3, 10))
        ttk.Label(rcon, text="本工具的本地控制台不依赖 RCON。", style="Muted.TLabel").pack(anchor="w")
        tab.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)

    def _build_access_tab(self) -> None:
        tab = self.access_tab
        for column, (title, attr) in enumerate(
            (("管理员（每行一个用户名）", "admins_text"), ("白名单", "whitelist_text"), ("封禁列表", "bans_text"))
        ):
            frame = ttk.Frame(tab, style="Panel.TFrame")
            frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0 if column == 2 else 8))
            ttk.Label(frame, text=title, style="Panel.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
            text = tk.Text(frame, bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", height=16, wrap="none")
            text.pack(fill="both", expand=True)
            setattr(self, attr, text)
            tab.columnconfigure(column, weight=1)
        tab.rowconfigure(0, weight=1)
        ttk.Checkbutton(
            tab,
            text="启用白名单（仅允许白名单与管理员进入）",
            variable=self.whitelist_enabled_var,
            style="Panel.TCheckbutton",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _build_shortcuts_tab(self) -> None:
        tab = self.shortcuts_tab
        players_panel = ttk.Frame(tab, style="Panel.TFrame")
        players_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        title_row = ttk.Frame(players_panel, style="Panel.TFrame")
        title_row.pack(fill="x", pady=(0, 8))
        ttk.Label(
            title_row,
            text="在线玩家",
            style="Panel.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        self.player_refresh_button = ttk.Button(
            title_row,
            text="刷新玩家",
            command=self._refresh_online_players,
        )
        self.player_refresh_button.pack(side="right")

        self.players_tree = ttk.Treeview(
            players_panel,
            columns=("name",),
            show="headings",
            height=13,
            selectmode="browse",
        )
        for column, title, width in (("name", "在线玩家名称", 300),):
            self.players_tree.heading(column, text=title)
            self.players_tree.column(column, width=width, minwidth=55, anchor="center")
        self.players_tree.pack(fill="both", expand=True)
        self.players_tree.bind("<<TreeviewSelect>>", self._on_player_selected)
        target_row = ttk.Frame(players_panel, style="Panel.TFrame")
        target_row.pack(fill="x", pady=(10, 0))
        ttk.Label(target_row, text="目标玩家名", style="Panel.TLabel").pack(side="left")
        ttk.Entry(target_row, textvariable=self.shortcut_target_name_var).pack(
            side="left", fill="x", expand=True, padx=(8, 0)
        )
        ttk.Label(
            players_panel,
            text="手动输入完整名称；点击上方在线玩家可自动填入。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        commands_panel = ttk.Frame(tab, style="Panel.TFrame")
        commands_panel.grid(row=0, column=1, sticky="nsew")
        ttk.Label(
            commands_panel,
            text="玩家快捷指令（自动使用 /sc）",
            style="Panel.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            commands_panel,
            text="刷新使用普通 /players online，不影响成就；只有执行下列 /sc 指令才会设置脚本命令标记。",
            style="Muted.TLabel",
            wraplength=500,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 12))

        ttk.Label(commands_panel, text="物品内部名", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(commands_panel, textvariable=self.shortcut_item_var, width=22).grid(row=2, column=1, sticky="ew", padx=(8, 10))
        ttk.Label(commands_panel, text="数量", style="Panel.TLabel").grid(row=2, column=2, sticky="w")
        ttk.Entry(commands_panel, textvariable=self.shortcut_count_var, width=8).grid(row=2, column=3, sticky="ew", padx=(8, 0))
        self._shortcut_button(commands_panel, "给予物品", self._shortcut_give_item, 3, 0, 2)
        self._shortcut_button(commands_panel, "清空背包", self._shortcut_clear_inventory, 3, 2, 2)

        self._shortcut_button(commands_panel, "开启作弊模式", lambda: self._run_shortcut("player.cheat_mode=true", "开启作弊模式"), 4, 0, 2)
        self._shortcut_button(commands_panel, "关闭作弊模式", lambda: self._run_shortcut("player.cheat_mode=false", "关闭作弊模式"), 4, 2, 2)
        self._shortcut_button(
            commands_panel,
            "恢复生命",
            lambda: self._run_shortcut(
                "if player.character then player.character.health=player.character.prototype.max_health end",
                "恢复生命",
            ),
            5,
            0,
            2,
        )
        self._shortcut_button(
            commands_panel,
            "传送到出生点",
            lambda: self._run_shortcut(
                "player.teleport(player.force.get_spawn_position(player.surface),player.surface)",
                "传送到出生点",
            ),
            5,
            2,
            2,
        )

        ttk.Label(commands_panel, text="清敌半径", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(commands_panel, textvariable=self.shortcut_radius_var, width=10).grid(row=6, column=1, sticky="w", padx=(8, 10), pady=(10, 0))
        self._shortcut_button(commands_panel, "清除移动敌人", lambda: self._shortcut_kill_enemies(False), 7, 0, 2)
        self._shortcut_button(commands_panel, "清除全部敌对目标", lambda: self._shortcut_kill_enemies(True), 7, 2, 2)

        ttk.Label(
            commands_panel,
            text="自定义 Lua（可直接使用局部变量 player）",
            style="Panel.TLabel",
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(12, 4))
        self.custom_lua_text = tk.Text(
            commands_panel,
            height=4,
            bg=self.INPUT,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.custom_lua_text.grid(row=9, column=0, columnspan=4, sticky="nsew")
        self.custom_lua_text.insert("1.0", 'player.print("快捷指令测试")')
        self._shortcut_button(commands_panel, "执行自定义 /sc", self._shortcut_custom, 10, 0, 4)

        for column in range(4):
            commands_panel.columnconfigure(column, weight=1)
        commands_panel.rowconfigure(9, weight=1)
        tab.columnconfigure(0, weight=4)
        tab.columnconfigure(1, weight=5)
        tab.rowconfigure(0, weight=1)

    def _shortcut_button(self, parent, text: str, command, row: int, column: int, columnspan: int) -> None:
        button = ttk.Button(parent, text=text, command=command)
        button.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=(0, 6), pady=(7, 0))
        self._shortcut_action_buttons.append(button)

    def _build_console_tab(self) -> None:
        tab = self.console_tab
        self.console_text = tk.Text(
            tab,
            bg="#0b1017",
            fg="#dce7f1",
            insertbackground=self.TEXT,
            relief="flat",
            wrap="none",
            font=("Consolas", 10),
            state="disabled",
        )
        ybar = ttk.Scrollbar(tab, orient="vertical", command=self.console_text.yview)
        xbar = ttk.Scrollbar(tab, orient="horizontal", command=self.console_text.xview)
        self.console_text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.console_text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        command_bar = ttk.Frame(tab, style="Panel.TFrame")
        command_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.command_entry = ttk.Entry(command_bar)
        self.command_entry.pack(side="left", fill="x", expand=True)
        self.command_entry.bind("<Return>", lambda _event: self._send_console_command())
        ttk.Button(command_bar, text="发送命令", command=self._send_console_command).pack(side="right", padx=(8, 0))
        ttk.Button(command_bar, text="清空显示", command=self._clear_console).pack(side="right", padx=(8, 0))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

    def _load_profile_into_ui(self) -> None:
        p = self.profile
        self.exe_var.set(p.factorio_exe)
        self.save_var.set(p.save_file)
        self.mods_var.set(p.mods_directory)
        self.name_var.set(p.server_name)
        self.description_var.set(p.description)
        self.tags_var.set(", ".join(p.tags))
        self.max_players_var.set(str(p.max_players))
        self.port_var.set(str(p.game_port))
        self.public_var.set(p.public_visibility)
        self.lan_var.set(p.lan_visibility)
        self.verify_var.set(p.require_user_verification)
        self.autosave_interval_var.set(str(p.autosave_interval))
        self.autosave_slots_var.set(str(p.autosave_slots))
        self.afk_var.set(str(p.afk_autokick_interval))
        self.auto_pause_var.set(p.auto_pause)
        self.pause_connect_var.set(p.auto_pause_when_players_connect)
        self.allow_commands_var.set(p.allow_commands)
        self.whitelist_enabled_var.set(p.whitelist_enabled)
        self.rcon_enabled_var.set(p.rcon_enabled)
        self.rcon_port_var.set(str(p.rcon_port))
        self._set_text(self.admins_text, "\n".join(p.admins))
        self._set_text(self.whitelist_text, "\n".join(p.whitelist))
        self._set_text(self.bans_text, "\n".join(p.bans))

    def _collect_profile(self) -> ServerProfile:
        def integer(variable: tk.StringVar, label: str) -> int:
            try:
                return int(variable.get().strip())
            except ValueError as exc:
                raise ConfigurationError(f"{label}必须是整数") from exc

        return ServerProfile(
            factorio_exe=self.exe_var.get().strip(),
            save_file=self.save_var.get().strip(),
            mods_directory=self.mods_var.get().strip(),
            server_name=self.name_var.get().strip(),
            description=self.description_var.get().strip(),
            tags=parse_comma_list(self.tags_var.get()),
            max_players=integer(self.max_players_var, "最大玩家数"),
            game_port=integer(self.port_var, "游戏端口"),
            public_visibility=self.public_var.get(),
            lan_visibility=self.lan_var.get(),
            require_user_verification=self.verify_var.get(),
            autosave_interval=integer(self.autosave_interval_var, "自动保存间隔"),
            autosave_slots=integer(self.autosave_slots_var, "自动保存槽位"),
            afk_autokick_interval=integer(self.afk_var, "AFK 踢出时间"),
            auto_pause=self.auto_pause_var.get(),
            auto_pause_when_players_connect=self.pause_connect_var.get(),
            allow_commands=self.allow_commands_var.get(),
            whitelist_enabled=self.whitelist_enabled_var.get(),
            admins=parse_name_lines(self.admins_text.get("1.0", "end")),
            whitelist=parse_name_lines(self.whitelist_text.get("1.0", "end")),
            bans=parse_name_lines(self.bans_text.get("1.0", "end")),
            rcon_enabled=self.rcon_enabled_var.get(),
            rcon_port=integer(self.rcon_port_var, "RCON 端口"),
        )

    def _save_profile(self, *, quiet: bool = False) -> bool:
        try:
            profile = self._collect_profile()
            save_profile(PROFILE_PATH, profile)
            self.profile = profile
            if not quiet:
                self._set_status("设置已保存（不含密码与令牌）", self.GOOD)
            return True
        except ConfigurationError as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return False

    def _start_server(self) -> None:
        if self.process and self.process.poll() is None:
            return
        try:
            profile = self._collect_profile()
            profile.validate(
                game_password=self.game_password_var.get(),
                factorio_token=self.token_var.get(),
                rcon_password=self.rcon_password_var.get(),
            )
            detected_version = detect_factorio_version(profile.factorio_exe)
            if detected_version not in SUPPORTED_FACTORIO_VERSIONS:
                raise ConfigurationError(
                    f"版本不匹配：当前 factorio.exe 是 {detected_version}，"
                    f"本工具仅支持 {'、'.join(SUPPORTED_FACTORIO_VERSIONS)}"
                )
            self.detected_version_var.set(f"已检测：{detected_version}")
            self._check_udp_port_available(profile.game_port)
            save_profile(PROFILE_PATH, profile)
            files = write_runtime_files(
                RUNTIME_DIR,
                profile,
                game_password=self.game_password_var.get(),
                factorio_token=self.token_var.get(),
            )
            external_rcon = profile.rcon_enabled
            control_password = self.rcon_password_var.get() if external_rcon else secrets.token_urlsafe(32)
            control_port = profile.rcon_port if external_rcon else self._find_available_tcp_port()
            if external_rcon:
                self._check_tcp_port_available(control_port)
            launch_profile = replace(profile, rcon_port=control_port)
            command = build_server_command(
                launch_profile,
                files,
                rcon_password=control_password,
                rcon_bind_local=not external_rcon,
            )
            self._append_log(f"[版本校验] Factorio {detected_version}\n")
            self._append_log(f"> {redact_command(command)}\n", "command")

            startupinfo = None
            creationflags = 0
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                command,
                cwd=str(Path(profile.factorio_exe).parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self.profile = profile
            self.runtime_files = files
            self._stop_requested = False
            self._last_failure_hint = ""
            self._control_rcon_password = control_password
            self._control_rcon_port = control_port
            self._set_running_state(True)
            self._set_status(f"正在启动 · PID {self.process.pid}", self.WARN)
            self.notebook.select(self.console_tab)
            threading.Thread(target=self._read_process_output, args=(self.process,), daemon=True).start()
            threading.Thread(target=self._wait_for_process, args=(self.process,), daemon=True).start()
        except (ConfigurationError, OSError) as exc:
            if self.runtime_files:
                scrub_runtime_secrets(self.runtime_files.settings)
            messagebox.showerror(APP_TITLE, f"无法启动服务器：\n{exc}", parent=self.root)
            self._set_status("启动失败", self.BAD)

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.log_queue.put(("log", line))

    def _wait_for_process(self, process: subprocess.Popen[str]) -> None:
        code = process.wait()
        self.log_queue.put(("exit", str(code)))

    def _drain_log_queue(self) -> None:
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                    if "Can't bind socket: Error code 10013" in payload:
                        self._last_failure_hint = "局域网广播绑定失败：请关闭“局域网内可发现”后重试"
                        self._append_log(f"[提示] {self._last_failure_hint}\n")
                        self._set_status(self._last_failure_hint, self.BAD)
                    if "Hosting game at" in payload or "Starting RCON interface" in payload:
                        self._set_status(f"服务器运行中 · UDP {self.profile.game_port}", self.GOOD)
                    if "Starting RCON interface" in payload and not self._player_refresh_pending:
                        self.root.after(500, lambda: self._refresh_online_players(True))
                elif kind == "exit":
                    self._on_process_exit(int(payload))
                elif kind == "rcon":
                    if payload:
                        self._append_log(payload.rstrip() + "\n")
                elif kind == "rcon_error":
                    self._append_log(f"[RCON 错误] {payload}\n")
                    self._set_status("RCON 命令失败", self.BAD)
                elif kind == "stop_sent":
                    self._append_log("[存档已保存，服务端进程已结束]\n")
                    self._set_status("已保存，正在停止服务器…", self.WARN)
                elif kind == "stop_error":
                    self._append_log(f"[无法通过 RCON 正常停止] {payload}\n")
                    self._set_status("正常停止失败", self.BAD)
                    if self.process:
                        self._offer_force_stop(self.process)
                elif kind == "achievement_done":
                    self.achievement_button.configure(state="normal")
                    self._refresh_saves()
                    self._append_log(payload + "\n")
                    self._set_status("成就标记已清除", self.GOOD)
                    messagebox.showinfo(APP_TITLE, payload, parent=self.root)
                elif kind == "achievement_clean":
                    self.achievement_button.configure(state="normal")
                    self._append_log(payload + "\n")
                    self._set_status("成就标记已经是 00", self.GOOD)
                    messagebox.showinfo(APP_TITLE, payload, parent=self.root)
                elif kind == "achievement_error":
                    self.achievement_button.configure(state="normal")
                    self._append_log(f"[成就标记修复失败] {payload}\n")
                    self._set_status("成就标记修复失败", self.BAD)
                    messagebox.showerror(APP_TITLE, payload, parent=self.root)
                elif kind == "players":
                    self._player_refresh_pending = False
                    self.player_refresh_button.configure(state="normal")
                    self._apply_online_players(payload)
                elif kind == "players_error":
                    self._player_refresh_pending = False
                    if self.process and self.process.poll() is None:
                        self.player_refresh_button.configure(state="normal")
                    self._append_log(f"[在线玩家刷新失败] {payload}\n")
                    self._set_status("在线玩家刷新失败", self.BAD)
                elif kind == "shortcut_done":
                    result = json.loads(payload)
                    response = result.get("response", "").strip()
                    self._append_log(
                        f"[快捷指令完成] {result['label']} → {result['player']}"
                        + (f"：{response}" if response else "")
                        + "\n"
                    )
                    self._set_status(f"快捷指令已执行 · {result['player']}", self.GOOD)
                    self.root.after(300, lambda: self._refresh_online_players(True))
                elif kind == "shortcut_error":
                    self._append_log(f"[快捷指令失败] {payload}\n")
                    self._set_status("快捷指令执行失败", self.BAD)
                    messagebox.showerror(APP_TITLE, payload, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _send_console_command(self) -> None:
        text = self.command_entry.get().strip()
        if not text:
            return
        process = self.process
        if not process or process.poll() is not None:
            messagebox.showinfo(APP_TITLE, "服务器当前未运行。", parent=self.root)
            return
        self._append_log(f"> {text}\n", "command")
        self.command_entry.delete(0, "end")
        threading.Thread(target=self._send_rcon_worker, args=(text,), daemon=True).start()

    def _refresh_online_players(self, quiet: bool = False) -> None:
        process = self.process
        if not process or process.poll() is not None:
            if not quiet:
                messagebox.showinfo(APP_TITLE, "服务器当前未运行。", parent=self.root)
            return
        if self._player_refresh_pending:
            return
        self._player_refresh_pending = True
        self.player_refresh_button.configure(state="disabled")
        if not quiet:
            self._set_status("正在读取在线玩家…", self.WARN)
        threading.Thread(target=self._online_players_worker, daemon=True).start()

    def _online_players_worker(self) -> None:
        try:
            response = execute_rcon(
                "127.0.0.1",
                self._control_rcon_port,
                self._control_rcon_password,
                build_online_players_query(),
            )
            players = parse_online_players(response)
            payload = json.dumps(
                [{"name": player.name} for player in players],
                ensure_ascii=False,
            )
            self.log_queue.put(("players", payload))
        except (OSError, RconError, ShortcutError) as exc:
            self.log_queue.put(("players_error", str(exc)))

    def _apply_online_players(self, payload: str) -> None:
        previous = self._selected_player_name()
        for item in self.players_tree.get_children():
            self.players_tree.delete(item)
        players = json.loads(payload)
        selected_item = ""
        for player in players:
            item = self.players_tree.insert(
                "",
                "end",
                values=(
                    player["name"],
                ),
            )
            if player["name"] == previous:
                selected_item = item
        if selected_item:
            self.players_tree.selection_set(selected_item)
            self.players_tree.focus(selected_item)
            self.players_tree.see(selected_item)
        self._set_status(f"在线玩家：{len(players)}", self.GOOD)

    def _on_player_selected(self, _event=None) -> None:
        name = self._tree_selected_player_name()
        if name:
            self.shortcut_target_name_var.set(name)

    def _selected_player_name(self) -> str:
        return self.shortcut_target_name_var.get().strip()

    def _tree_selected_player_name(self) -> str:
        selection = self.players_tree.selection()
        if not selection:
            return ""
        values = self.players_tree.item(selection[0], "values")
        return str(values[0]) if values else ""

    def _shortcut_give_item(self) -> None:
        try:
            item = self.shortcut_item_var.get().strip()
            if not item or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in item):
                raise ShortcutError("物品内部名只能包含小写字母、数字、连字符和下划线")
            count = checked_integer(self.shortcut_count_var.get(), "物品数量", 1, 1000000)
            encoded_item = json.dumps(item)
            self._run_shortcut(
                f"local inserted=player.insert{{name={encoded_item},count={count}}}; rcon.print('已放入 '..inserted)",
                f"给予 {item} × {count}",
            )
        except ShortcutError as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)

    def _shortcut_clear_inventory(self) -> None:
        self._run_shortcut(
            "local inventory=player.get_main_inventory(); if inventory then inventory.clear() end",
            "清空背包",
        )

    def _shortcut_kill_enemies(self, include_structures: bool) -> None:
        try:
            radius = checked_integer(self.shortcut_radius_var.get(), "清敌半径", 1, 5000)
        except ShortcutError as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        type_filter = "" if include_structures else ',type="unit"'
        label = "清除全部敌对目标" if include_structures else "清除移动敌人"
        self._run_shortcut(
            "local count=0; for _,entity in pairs(player.surface.find_entities_filtered{"
            f"position=player.position,radius={radius},force=\"enemy\"{type_filter}"
            "}) do entity.destroy(); count=count+1 end; rcon.print('已清除 '..count)",
            f"{label}（半径 {radius}）",
        )

    def _shortcut_custom(self) -> None:
        self._run_shortcut(self.custom_lua_text.get("1.0", "end").strip(), "自定义 /sc")

    def _run_shortcut(self, lua_body: str, label: str) -> None:
        process = self.process
        if not process or process.poll() is not None:
            messagebox.showinfo(APP_TITLE, "服务器当前未运行。", parent=self.root)
            return
        player_name = self._selected_player_name()
        try:
            command = build_player_sc_command(player_name, lua_body)
        except ShortcutError as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return
        self._append_log(f"> /sc [快捷指令：{label} → {player_name}]\n", "command")
        threading.Thread(
            target=self._shortcut_worker,
            args=(command, label, player_name),
            daemon=True,
        ).start()

    def _shortcut_worker(self, command: str, label: str, player_name: str) -> None:
        try:
            response = execute_rcon(
                "127.0.0.1",
                self._control_rcon_port,
                self._control_rcon_password,
                command,
            )
            if "目标玩家已离线" in response:
                self.log_queue.put(("shortcut_error", f"找不到在线玩家：{player_name}"))
                return
            self.log_queue.put((
                "shortcut_done",
                json.dumps(
                    {"label": label, "player": player_name, "response": response},
                    ensure_ascii=False,
                ),
            ))
        except (OSError, RconError) as exc:
            self.log_queue.put(("shortcut_error", str(exc)))

    def _clear_achievement_flags(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning(
                APP_TITLE,
                "服务器正在运行，不能修改其存档。请先停止服务器并等待保存完成。",
                parent=self.root,
            )
            return
        save_path = Path(self.save_var.get().strip())
        if not save_path.is_file() or save_path.suffix.casefold() != ".zip":
            messagebox.showerror(APP_TITLE, "请先选择有效的 Factorio ZIP 存档。", parent=self.root)
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "将清除所选存档的控制台命令与编辑器成就标记。\n\n"
            "工具会先在同一目录创建备份。请确认没有其他 Factorio 进程正在保存此文件。\n\n"
            f"存档：{save_path}",
            parent=self.root,
        ):
            return
        self.achievement_button.configure(state="disabled")
        self._set_status("正在备份并清除成就标记…", self.WARN)
        threading.Thread(
            target=self._clear_achievement_flags_worker,
            args=(save_path,),
            daemon=True,
        ).start()

    def _clear_achievement_flags_worker(self, save_path: Path) -> None:
        try:
            result = clear_achievement_flags(save_path)
            if not result.changed:
                self.log_queue.put((
                    "achievement_clean",
                    f"存档中的控制台命令与编辑器标记已经是 00。\n位置：{result.entry_name} + 0x{result.offset:X}",
                ))
                return
            cleared = []
            if result.command_was_set:
                cleared.append("控制台命令")
            if result.editor_was_set:
                cleared.append("编辑器")
            self.log_queue.put((
                "achievement_done",
                "已清除成就标记：" + "、".join(cleared) + "\n"
                f"位置：{result.entry_name} + 0x{result.offset:X}\n"
                f"备份：{result.backup_path}",
            ))
        except (AchievementFlagError, OSError) as exc:
            self.log_queue.put(("achievement_error", str(exc)))

    def _stop_server(self) -> None:
        process = self.process
        if not process or process.poll() is not None:
            return
        self._append_log("> /server-save\n", "command")
        self._stop_requested = True
        self._set_status("正在通过 RCON 保存并停止服务器…", self.WARN)
        self.stop_button.configure(state="disabled")
        threading.Thread(target=self._stop_rcon_worker, args=(process,), daemon=True).start()
        self.root.after(12000, lambda: self._offer_force_stop(process))

    def _send_rcon_worker(self, command: str) -> None:
        try:
            response = execute_rcon(
                "127.0.0.1",
                self._control_rcon_port,
                self._control_rcon_password,
                command,
            )
            self.log_queue.put(("rcon", response))
        except (OSError, RconError) as exc:
            self.log_queue.put(("rcon_error", str(exc)))

    def _stop_rcon_worker(self, process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 10
        last_error: Exception | None = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                save_path = Path(self.profile.save_file)
                previous_mtime_ns = save_path.stat().st_mtime_ns
                execute_rcon(
                    "127.0.0.1",
                    self._control_rcon_port,
                    self._control_rcon_password,
                    "/server-save",
                )
                wait_for_saved_zip(save_path, previous_mtime_ns)
                # Factorio 2.1.x Windows dedicated servers can crash while
                # handling remote /quit after unloading the map. End the
                # process only after the updated save is a complete ZIP.
                if process.poll() is None:
                    process.kill()
                self.log_queue.put(("stop_sent", ""))
                return
            except (OSError, RconError) as exc:
                last_error = exc
                time.sleep(0.5)
        if process.poll() is None:
            self.log_queue.put(("stop_error", str(last_error or "RCON 尚未就绪")))

    def _offer_force_stop(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if messagebox.askyesno(
            APP_TITLE,
            "服务器在 8 秒内没有正常退出。\n\n是否强制结束？强制结束可能损坏正在写入的存档。",
            parent=self.root,
        ):
            process.kill()
        else:
            self.stop_button.configure(state="normal")

    def _on_process_exit(self, code: int) -> None:
        if self.runtime_files:
            scrub_runtime_secrets(self.runtime_files.settings)
        expected_stop = self._stop_requested
        self._append_log(f"\n[进程已退出，代码 {code}]\n")
        self.process = None
        self._control_rcon_password = ""
        self._control_rcon_port = 0
        self._stop_requested = False
        self._player_refresh_pending = False
        for item in self.players_tree.get_children():
            self.players_tree.delete(item)
        self._set_running_state(False)
        normal_exit = code == 0 or expected_stop
        if not normal_exit and self._last_failure_hint:
            self._set_status(self._last_failure_hint, self.BAD)
        else:
            self._set_status("服务器已停止" if normal_exit else f"服务器异常退出 · 代码 {code}", self.MUTED if normal_exit else self.BAD)
        if self._closing_after_stop:
            self.root.destroy()

    def _set_running_state(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.achievement_button.configure(state="disabled" if running else "normal")
        self.player_refresh_button.configure(state="normal" if running else "disabled")
        for button in self._shortcut_action_buttons:
            button.configure(state="normal" if running else "disabled")

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def _append_log(self, text: str, tag: str = "log") -> None:
        self.console_text.configure(state="normal")
        self.console_text.tag_configure("command", foreground=self.ACCENT)
        self.console_text.insert("end", text, tag)
        self.console_text.see("end")
        self.console_text.configure(state="disabled")

    def _clear_console(self) -> None:
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", "end")
        self.console_text.configure(state="disabled")

    def _browse_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 factorio.exe",
            filetypes=[("Factorio", "factorio.exe"), ("可执行文件", "*.exe")],
            initialdir=str(Path(self.exe_var.get()).parent) if self.exe_var.get() else None,
        )
        if path:
            self.exe_var.set(path)
            self._detect_selected_version(show_error=True)

    def _browse_save(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Factorio 存档",
            filetypes=[("Factorio 存档", "*.zip")],
            initialdir=str(self._saves_directory()),
        )
        if path:
            self.save_var.set(path)

    def _browse_mods(self) -> None:
        path = filedialog.askdirectory(
            title="选择 Factorio 模组目录",
            initialdir=self.mods_var.get() or str(self._saves_directory().parent / "mods"),
        )
        if path:
            self.mods_var.set(path)

    def _auto_detect_paths(self) -> None:
        current = Path(self.exe_var.get().strip()) if self.exe_var.get().strip() else None
        executable, version = self._find_supported_factorio(current)
        if executable is None:
            found = discover_factorio_executables(
                extra_candidates=([current] if current else []),
            )
            details = "\n".join(str(path) for path in found) or "未找到任何 Factorio 安装"
            messagebox.showerror(
                APP_TITLE,
                f"没有找到支持的 Factorio {'、'.join(SUPPORTED_FACTORIO_VERSIONS)}。\n\n"
                f"已检查 Steam 主库、其他 Steam 库和常见安装位置。\n\n{details}",
                parent=self.root,
            )
            return
        saves_directory, mods_directory = factorio_user_paths()
        self.exe_var.set(str(executable))
        self.detected_version_var.set(f"已检测：{version}")
        self.mods_var.set(str(mods_directory))
        current_save = Path(self.save_var.get().strip()) if self.save_var.get().strip() else None
        if current_save is None or not current_save.is_file():
            latest_save = newest_save(saves_directory)
            self.save_var.set(str(latest_save) if latest_save else "")
        self._refresh_saves()
        self._set_status(f"已自动找到 Factorio {version}", self.GOOD)

    def _repair_discovered_paths(self, profile: ServerProfile) -> ServerProfile:
        current = Path(profile.factorio_exe) if profile.factorio_exe else None
        executable, version = self._find_supported_factorio(current)
        self._detected_profile_version = version
        saves_directory, mods_directory = factorio_user_paths()
        save_path = Path(profile.save_file) if profile.save_file else None
        if save_path is None or not save_path.is_file():
            save_path = newest_save(saves_directory)
        mods_path = Path(profile.mods_directory) if profile.mods_directory else None
        if mods_path is None or not mods_path.is_dir():
            mods_path = mods_directory
        return replace(
            profile,
            factorio_exe=str(executable) if executable else "",
            save_file=str(save_path) if save_path else "",
            mods_directory=str(mods_path),
        )

    @staticmethod
    def _find_supported_factorio(current: Path | None = None) -> tuple[Path | None, str]:
        candidates = discover_factorio_executables(
            extra_candidates=([current] if current else []),
        )
        supported: list[tuple[Path, str]] = []
        for executable in candidates:
            try:
                version = detect_factorio_version(str(executable))
                if version in SUPPORTED_FACTORIO_VERSIONS:
                    if current is not None and executable == current:
                        return executable, version
                    supported.append((executable, version))
            except ConfigurationError:
                continue
        if not supported:
            return None, ""
        supported.sort(
            key=lambda value: SUPPORTED_FACTORIO_VERSIONS.index(value[1]),
            reverse=True,
        )
        return supported[0]

    def _detect_selected_version(self, *, show_error: bool = False) -> str:
        executable = self.exe_var.get().strip()
        if not executable:
            self.detected_version_var.set("版本未检测")
            return ""
        try:
            version = detect_factorio_version(executable)
        except ConfigurationError as exc:
            self.detected_version_var.set("无法读取版本")
            if show_error:
                messagebox.showerror(APP_TITLE, str(exc), parent=self.root)
            return ""
        if version not in SUPPORTED_FACTORIO_VERSIONS:
            self.detected_version_var.set(f"不支持：{version}")
            if show_error:
                messagebox.showwarning(
                    APP_TITLE,
                    f"检测到 Factorio {version}，本工具只支持 "
                    f"{'、'.join(SUPPORTED_FACTORIO_VERSIONS)}。",
                    parent=self.root,
                )
            return version
        self.detected_version_var.set(f"已检测：{version}")
        self._set_status(f"已选择 Factorio {version}", self.GOOD)
        return version

    def _refresh_saves(self) -> None:
        saves = sorted(self._saves_directory().glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
        values = [str(path) for path in saves]
        self.save_combo.configure(values=values)
        if not self.save_var.get() and values:
            self.save_var.set(values[0])

    @staticmethod
    def _saves_directory() -> Path:
        return factorio_user_paths()[0]

    @staticmethod
    def _check_udp_port_available(port: int) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise ConfigurationError(f"UDP 游戏端口 {port} 当前不可用：{exc}") from exc

    @staticmethod
    def _check_tcp_port_available(port: int) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("0.0.0.0", port))
        except OSError as exc:
            raise ConfigurationError(f"TCP RCON 端口 {port} 已被占用：{exc}") from exc

    @staticmethod
    def _find_available_tcp_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno(
                APP_TITLE,
                "服务器仍在运行。是否先正常停止服务器并在退出后关闭工具？",
                parent=self.root,
            ):
                return
            self._closing_after_stop = True
            self._stop_server()
            return
        self.root.destroy()


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    enable_windows_dpi_awareness()
    root = tk.Tk()
    ServerManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
