from __future__ import annotations

import json
import re
from dataclasses import dataclass


class ShortcutError(ValueError):
    pass


@dataclass(frozen=True)
class OnlinePlayer:
    name: str


def build_online_players_query() -> str:
    # Built-in multiplayer command: unlike /c or /sc, this does not set the
    # save's scripting/cheat achievement flag.
    return "/players online"


def parse_online_players(response: str) -> list[OnlinePlayer]:
    lines = response.splitlines()
    if not lines:
        raise ShortcutError("服务器没有返回有效的在线玩家列表")
    count_match = re.search(r"\((\d+)\)\s*:\s*$", lines[0])
    if count_match is None:
        raise ShortcutError(f"无法识别在线玩家响应：{lines[0]}")
    expected_count = int(count_match.group(1))
    names = [line.strip() for line in lines[1:] if line.strip()]
    if len(names) != expected_count:
        raise ShortcutError(
            f"在线玩家数量不一致：服务器报告 {expected_count}，解析到 {len(names)}"
        )
    return [OnlinePlayer(name=name) for name in names]


def build_player_sc_command(player_name: str, lua_body: str) -> str:
    name = player_name.strip()
    body = lua_body.strip()
    if not name:
        raise ShortcutError("请先选择在线玩家")
    if not body:
        raise ShortcutError("Lua 指令不能为空")
    encoded_name = json.dumps(name, ensure_ascii=False)
    return (
        f"/sc local player=game.get_player({encoded_name}); "
        f"if player and player.connected then {body} else rcon.print(\"目标玩家已离线\") end"
    )


def checked_integer(text: str, label: str, minimum: int, maximum: int) -> int:
    try:
        value = int(text.strip())
    except ValueError as exc:
        raise ShortcutError(f"{label}必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ShortcutError(f"{label}必须在 {minimum}–{maximum} 之间")
    return value
