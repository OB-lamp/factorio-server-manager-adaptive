from __future__ import annotations

import queue
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from factorio_server_manager.app import PROFILE_PATH, default_profile
from factorio_server_manager.core import build_server_command, load_profile, write_runtime_files
from factorio_server_manager.rcon import execute_rcon
from factorio_server_manager.shortcuts import (
    build_online_players_query,
    build_player_sc_command,
    parse_online_players,
)


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    profile = load_profile(PROFILE_PATH, default_profile())
    profile = replace(
        profile,
        game_port=34414,
        rcon_port=27214,
        public_visibility=False,
        lan_visibility=False,
        require_user_verification=False,
    )
    runtime = ROOT / ".shortcut-smoke-2.1.16"
    files = write_runtime_files(runtime, profile, game_password="", factorio_token="")
    password = secrets.token_urlsafe(24)
    command = build_server_command(profile, files, rcon_password=password, rcon_bind_local=True)
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        command,
        cwd=str(Path(profile.factorio_exe).parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    output: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                line = output.get(timeout=0.25)
            except queue.Empty:
                continue
            if "Starting RCON interface" in line:
                response = execute_rcon(
                    "127.0.0.1",
                    profile.rcon_port,
                    password,
                    build_online_players_query(),
                )
                players = parse_online_players(response)
                bodies = [
                    "player.cheat_mode=true",
                    'local inserted=player.insert{name="iron-plate",count=100}; rcon.print(inserted)',
                    "local inventory=player.get_main_inventory(); if inventory then inventory.clear() end",
                    "if player.character then player.character.health=player.character.prototype.max_health end",
                    "player.teleport(player.force.get_spawn_position(player.surface),player.surface)",
                    'for _,entity in pairs(player.surface.find_entities_filtered{position=player.position,radius=50,force="enemy",type="unit"}) do entity.destroy() end',
                ]
                for body in bodies:
                    check = execute_rcon(
                        "127.0.0.1",
                        profile.rcon_port,
                        password,
                        build_player_sc_command("__shortcut_smoke_offline__", body),
                    )
                    if "Cannot execute command" in check:
                        raise RuntimeError(check)
                print(f"SHORTCUT_SMOKE_OK online_players={len(players)}")
                return 0
        print("SHORTCUT_SMOKE_FAILED")
        return 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
