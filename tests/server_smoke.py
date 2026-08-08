"""Manual integration smoke test for a local Factorio headless server."""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from factorio_server_manager.core import (
    ServerProfile,
    build_server_command,
    write_runtime_files,
)
from factorio_server_manager.app import wait_for_saved_zip
from factorio_server_manager.rcon import RconError, execute_rcon


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: server_smoke.py SOURCE_SAVE TEMP_DIRECTORY")
        return 2

    source_save = Path(sys.argv[1]).resolve()
    temp_directory = Path(sys.argv[2]).resolve()
    temp_directory.mkdir(parents=True, exist_ok=True)
    test_save = temp_directory / "smoke-save.zip"
    shutil.copy2(source_save, test_save)

    appdata = Path.home() / "AppData" / "Roaming"
    profile = ServerProfile(
        factorio_exe=r"D:\steam\steamapps\common\Factorio\bin\x64\factorio.exe",
        save_file=str(test_save),
        mods_directory=str(appdata / "Factorio" / "mods"),
        server_name="Codex Factorio Server Smoke Test",
        game_port=34297,
        public_visibility=False,
        lan_visibility=False,
        auto_pause=True,
        rcon_port=27115,
    )
    profile.validate()
    files = write_runtime_files(temp_directory, profile, game_password="", factorio_token="")
    control_password = "codex-smoke-control"
    command = build_server_command(
        profile,
        files,
        rcon_password=control_password,
        rcon_bind_local=True,
    )

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
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
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    lines: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + 45
    ready = False
    captured: list[str] = []
    while time.monotonic() < deadline and process.poll() is None:
        try:
            line = lines.get(timeout=0.25)
            captured.append(line)
            print(line, end="")
            if "Hosting game at" in line or "changing state from(CreatingGame) to(InGame)" in line:
                ready = True
                break
        except queue.Empty:
            pass

    if not ready:
        process.kill()
        process.wait(timeout=10)
        print("SMOKE_READY=False")
        return 1

    rcon_deadline = time.monotonic() + 10
    rcon_error: Exception | None = None
    while time.monotonic() < rcon_deadline:
        try:
            previous_mtime_ns = test_save.stat().st_mtime_ns
            execute_rcon("127.0.0.1", profile.rcon_port, control_password, "/server-save")
            wait_for_saved_zip(test_save, previous_mtime_ns)
            rcon_error = None
            break
        except (OSError, RconError) as exc:
            rcon_error = exc
            time.sleep(0.25)
    if rcon_error is not None:
        process.kill()
        process.wait(timeout=10)
        print(f"SMOKE_RCON_ERROR={rcon_error}")
        return 1
    if process.poll() is None:
        process.kill()
    exit_code = process.wait(timeout=10)
    print("SMOKE_SAVED_PROCESS_TERMINATED=True")

    while True:
        try:
            line = lines.get_nowait()
            captured.append(line)
            print(line, end="")
        except queue.Empty:
            break

    print(f"SMOKE_READY={ready}")
    print(f"SMOKE_EXIT_CODE={exit_code}")
    return 0 if exit_code in {0, 1} else 1


if __name__ == "__main__":
    raise SystemExit(main())
