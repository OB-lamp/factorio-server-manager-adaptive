from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


class ConfigurationError(ValueError):
    pass


@dataclass
class ServerProfile:
    factorio_exe: str
    save_file: str = ""
    mods_directory: str = ""
    server_name: str = "Factorio 私人服务器"
    description: str = ""
    tags: list[str] = field(default_factory=lambda: ["vanilla", "co-op"])
    max_players: int = 8
    game_port: int = 34197
    public_visibility: bool = False
    lan_visibility: bool = True
    require_user_verification: bool = True
    autosave_interval: int = 10
    autosave_slots: int = 5
    afk_autokick_interval: int = 0
    auto_pause: bool = True
    auto_pause_when_players_connect: bool = False
    allow_commands: str = "admins-only"
    whitelist_enabled: bool = False
    admins: list[str] = field(default_factory=list)
    whitelist: list[str] = field(default_factory=list)
    bans: list[str] = field(default_factory=list)
    rcon_enabled: bool = False
    rcon_port: int = 27015

    def validate(
        self,
        *,
        game_password: str = "",
        factorio_token: str = "",
        rcon_password: str = "",
    ) -> None:
        exe = Path(self.factorio_exe)
        save = Path(self.save_file)
        if not exe.is_file():
            raise ConfigurationError(f"Factorio 主程序不存在：{exe}")
        if exe.name.casefold() != "factorio.exe":
            raise ConfigurationError("Factorio 主程序必须是 factorio.exe")
        if not save.is_file() or save.suffix.casefold() != ".zip":
            raise ConfigurationError(f"请选择有效的 .zip 存档：{save}")
        if not self.server_name.strip():
            raise ConfigurationError("服务器名称不能为空")
        if not 0 <= self.max_players <= 65535:
            raise ConfigurationError("最大玩家数必须在 0–65535 之间")
        _validate_port(self.game_port, "游戏端口")
        if not 1 <= self.autosave_interval <= 10080:
            raise ConfigurationError("自动保存间隔必须在 1–10080 分钟之间")
        if not 1 <= self.autosave_slots <= 100:
            raise ConfigurationError("自动保存槽位必须在 1–100 之间")
        if not 0 <= self.afk_autokick_interval <= 10080:
            raise ConfigurationError("AFK 踢出时间必须在 0–10080 分钟之间")
        if self.allow_commands not in {"true", "false", "admins-only"}:
            raise ConfigurationError("控制台脚本权限无效")
        if self.public_visibility and not factorio_token.strip():
            raise ConfigurationError("发布到官方服务器列表需要 Factorio 账户令牌")
        _validate_port(self.rcon_port, "RCON 端口")
        if self.rcon_port == self.game_port:
            raise ConfigurationError("RCON 端口不能与游戏端口相同")
        if self.rcon_enabled:
            if not rcon_password:
                raise ConfigurationError("启用 RCON 时必须设置 RCON 密码")
        for label, value in (
            ("游戏密码", game_password),
            ("Factorio 令牌", factorio_token),
            ("RCON 密码", rcon_password),
        ):
            if "\n" in value or "\r" in value:
                raise ConfigurationError(f"{label}不能包含换行符")

    def persistent_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict, defaults: "ServerProfile") -> "ServerProfile":
        allowed = set(cls.__dataclass_fields__)
        merged = defaults.persistent_dict()
        merged.update({key: value for key, value in raw.items() if key in allowed})
        for key in ("tags", "admins", "whitelist", "bans"):
            if not isinstance(merged.get(key), list):
                merged[key] = []
            merged[key] = [str(value) for value in merged[key]]
        return cls(**merged)


def _validate_port(port: int, label: str) -> None:
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"{label}必须在 1–65535 之间")


def normalize_names(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value).strip()
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            output.append(name)
    return output


def parse_comma_list(text: str) -> list[str]:
    return normalize_names(part for part in text.replace("，", ",").split(","))


def parse_name_lines(text: str) -> list[str]:
    return normalize_names(text.splitlines())


def build_server_settings(
    profile: ServerProfile,
    *,
    game_password: str,
    factorio_token: str,
) -> dict:
    return {
        "name": profile.server_name.strip(),
        "description": profile.description.strip(),
        "tags": normalize_names(profile.tags),
        "max_players": profile.max_players,
        "visibility": {
            "public": profile.public_visibility,
            "lan": profile.lan_visibility,
        },
        "username": "",
        "password": "",
        "token": factorio_token.strip() if profile.public_visibility else "",
        "game_password": game_password,
        "require_user_verification": profile.require_user_verification,
        "max_upload_in_kilobytes_per_second": 0,
        "max_upload_slots": 5,
        "minimum_latency_in_ticks": 0,
        "max_heartbeats_per_second": 60,
        "ignore_player_limit_for_returning_players": True,
        "allow_commands": profile.allow_commands,
        "autosave_interval": profile.autosave_interval,
        "autosave_slots": profile.autosave_slots,
        "afk_autokick_interval": profile.afk_autokick_interval,
        "auto_pause": profile.auto_pause,
        "auto_pause_when_players_connect": profile.auto_pause_when_players_connect,
        "only_admins_can_pause_the_game": True,
        "autosave_only_on_server": True,
        "non_blocking_saving": False,
        "minimum_segment_size": 25,
        "minimum_segment_size_peer_count": 20,
        "maximum_segment_size": 100,
        "maximum_segment_size_peer_count": 10,
    }


@dataclass(frozen=True)
class RuntimeFiles:
    config: Path
    write_data: Path
    settings: Path
    admins: Path
    bans: Path
    whitelist: Path
    server_id: Path
    console_log: Path


def write_runtime_files(
    runtime_dir: Path,
    profile: ServerProfile,
    *,
    game_password: str,
    factorio_token: str,
) -> RuntimeFiles:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_data = runtime_dir / "factorio-data"
    write_data.mkdir(parents=True, exist_ok=True)
    files = RuntimeFiles(
        config=runtime_dir / "factorio-config.ini",
        write_data=write_data,
        settings=runtime_dir / "server-settings.json",
        admins=runtime_dir / "server-adminlist.json",
        bans=runtime_dir / "server-banlist.json",
        whitelist=runtime_dir / "server-whitelist.json",
        server_id=runtime_dir / "server-id.json",
        console_log=runtime_dir / "console.log",
    )
    factorio_root = Path(profile.factorio_exe).resolve().parent.parent.parent
    read_data = factorio_root / "data"
    files.config.write_text(
        "[path]\n"
        f"read-data={read_data.as_posix()}\n"
        f"write-data={write_data.resolve().as_posix()}\n"
        "\n"
        "[general]\n"
        "locale=auto\n",
        encoding="utf-8",
    )
    _write_json(
        files.settings,
        build_server_settings(
            profile,
            game_password=game_password,
            factorio_token=factorio_token,
        ),
    )
    _write_json(files.admins, normalize_names(profile.admins))
    _write_json(files.bans, normalize_names(profile.bans))
    _write_json(files.whitelist, normalize_names(profile.whitelist))
    return files


def build_server_command(
    profile: ServerProfile,
    files: RuntimeFiles,
    *,
    rcon_password: str,
    rcon_bind_local: bool = False,
) -> list[str]:
    command = [
        profile.factorio_exe,
        "--config",
        str(files.config),
        "--start-server",
        profile.save_file,
        "--server-settings",
        str(files.settings),
        "--server-adminlist",
        str(files.admins),
        "--server-banlist",
        str(files.bans),
        "--server-whitelist",
        str(files.whitelist),
        "--use-server-whitelist",
        "true" if profile.whitelist_enabled else "false",
        "--server-id",
        str(files.server_id),
        "--console-log",
        str(files.console_log),
        "--port",
        str(profile.game_port),
        "--disable-audio",
    ]
    if profile.mods_directory:
        command.extend(["--mod-directory", profile.mods_directory])
    if rcon_password:
        rcon_endpoint_option = "--rcon-bind" if rcon_bind_local else "--rcon-port"
        rcon_endpoint = f"127.0.0.1:{profile.rcon_port}" if rcon_bind_local else str(profile.rcon_port)
        command.extend(
            [
                rcon_endpoint_option,
                rcon_endpoint,
                "--rcon-password",
                rcon_password,
            ]
        )
    return command


def redact_command(command: list[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for part in command:
        if hide_next:
            redacted.append("********")
            hide_next = False
        else:
            redacted.append(f'"{part}"' if " " in part else part)
            if part == "--rcon-password":
                hide_next = True
    return " ".join(redacted)


def load_profile(path: Path, defaults: ServerProfile) -> ServerProfile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError
        return ServerProfile.from_dict(raw, defaults)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return defaults


def save_profile(path: Path, profile: ServerProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, profile.persistent_dict())


def scrub_runtime_secrets(settings_path: Path) -> None:
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            return
        for key in ("password", "token", "game_password"):
            if key in raw:
                raw[key] = ""
        _write_json(settings_path, raw)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
