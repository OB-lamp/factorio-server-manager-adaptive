from __future__ import annotations

import json
import tempfile
import unittest
import zlib
import zipfile
from pathlib import Path
from unittest.mock import patch

from factorio_server_manager.app import (
    SUPPORTED_FACTORIO_VERSIONS,
    ServerManagerApp,
    detect_factorio_version,
)
from factorio_server_manager.achievement_flags import clear_achievement_flags
from factorio_server_manager.shortcuts import (
    build_online_players_query,
    build_player_sc_command,
    checked_integer,
    parse_online_players,
)
from factorio_server_manager.path_discovery import (
    discover_factorio_executables,
    factorio_user_paths,
    newest_save,
    parse_steam_libraryfolders,
)

from factorio_server_manager.core import (
    ConfigurationError,
    ServerProfile,
    build_server_command,
    build_server_settings,
    parse_comma_list,
    redact_command,
    scrub_runtime_secrets,
    write_runtime_files,
)


class ServerCoreTests(unittest.TestCase):
    def make_profile(self, root: Path) -> ServerProfile:
        exe = root / "factorio.exe"
        save = root / "world.zip"
        exe.touch()
        save.touch()
        return ServerProfile(
            factorio_exe=str(exe),
            save_file=str(save),
            mods_directory=str(root / "mods"),
        )

    def test_parse_comma_list_deduplicates(self) -> None:
        self.assertEqual(parse_comma_list("vanilla， co-op,Vanilla"), ["vanilla", "co-op"])

    @patch("factorio_server_manager.app.subprocess.run")
    def test_detect_factorio_version(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "Version: 2.1.17 (build 87315, win64, steam)\n"
        self.assertEqual(detect_factorio_version("factorio.exe"), "2.1.17")

    @patch("factorio_server_manager.app.detect_factorio_version")
    @patch("factorio_server_manager.app.discover_factorio_executables")
    def test_adaptive_version_selection_honors_selected_executable(self, discover, detect) -> None:
        old = Path(r"C:\Factorio-2.0.77\factorio.exe")
        previous = Path(r"C:\Factorio-2.1.14\factorio.exe")
        recent = Path(r"C:\Factorio-2.1.16\factorio.exe")
        new = Path(r"C:\Factorio-2.1.17\factorio.exe")
        discover.return_value = [old, previous, recent, new]
        versions = {
            str(old): "2.0.77",
            str(previous): "2.1.14",
            str(recent): "2.1.16",
            str(new): "2.1.17",
        }
        detect.side_effect = versions.__getitem__
        self.assertEqual(SUPPORTED_FACTORIO_VERSIONS, ("2.0.77", "2.1.14", "2.1.16", "2.1.17"))
        self.assertEqual(
            ServerManagerApp._find_supported_factorio(old),
            (old, "2.0.77"),
        )

    @patch("factorio_server_manager.app.detect_factorio_version")
    @patch("factorio_server_manager.app.discover_factorio_executables")
    def test_adaptive_auto_detection_prefers_newer_supported_version(self, discover, detect) -> None:
        old = Path(r"C:\Factorio-2.0.77\factorio.exe")
        previous = Path(r"C:\Factorio-2.1.14\factorio.exe")
        recent = Path(r"C:\Factorio-2.1.16\factorio.exe")
        new = Path(r"C:\Factorio-2.1.17\factorio.exe")
        discover.return_value = [old, previous, recent, new]
        versions = {
            str(old): "2.0.77",
            str(previous): "2.1.14",
            str(recent): "2.1.16",
            str(new): "2.1.17",
        }
        detect.side_effect = versions.__getitem__
        self.assertEqual(
            ServerManagerApp._find_supported_factorio(),
            (new, "2.1.17"),
        )

    def test_public_server_requires_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self.make_profile(Path(directory))
            profile.public_visibility = True
            with self.assertRaises(ConfigurationError):
                profile.validate()

    def test_runtime_files_and_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            profile.rcon_enabled = True
            profile.admins = ["Alice", "alice", "Bob"]
            profile.validate(rcon_password="secret")
            files = write_runtime_files(root / "runtime", profile, game_password="join", factorio_token="")
            command = build_server_command(profile, files, rcon_password="secret")
            self.assertIn("--config", command)
            self.assertIn("--start-server", command)
            self.assertIn("--rcon-port", command)
            self.assertNotIn("secret", redact_command(command))
            config = files.config.read_text(encoding="utf-8")
            self.assertIn("[path]", config)
            self.assertIn(f"write-data={files.write_data.resolve().as_posix()}", config)
            self.assertEqual(json.loads(files.admins.read_text(encoding="utf-8")), ["Alice", "Bob"])
            scrub_runtime_secrets(files.settings)
            scrubbed = json.loads(files.settings.read_text(encoding="utf-8"))
            self.assertEqual(scrubbed["game_password"], "")

    def test_private_control_rcon_binds_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            files = write_runtime_files(root / "runtime", profile, game_password="", factorio_token="")
            command = build_server_command(
                profile,
                files,
                rcon_password="temporary",
                rcon_bind_local=True,
            )
            self.assertIn("--rcon-bind", command)
            self.assertIn("127.0.0.1:27015", command)

    def test_settings_do_not_publish_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = self.make_profile(Path(directory))
            settings = build_server_settings(profile, game_password="", factorio_token="")
            self.assertFalse(settings["visibility"]["public"])
            self.assertTrue(settings["visibility"]["lan"])

    def test_online_player_query_and_response(self) -> None:
        query = build_online_players_query()
        self.assertEqual(query, "/players online")
        players = parse_online_players(
            "Online players (2):\n  OBL\n  Player Two\n"
        )
        self.assertEqual(players[0].name, "OBL")
        self.assertEqual(players[1].name, "Player Two")
        self.assertEqual(parse_online_players("Online players (0):\n"), [])

    def test_player_shortcut_uses_sc_and_encoded_target(self) -> None:
        command = build_player_sc_command('测试"玩家', "player.cheat_mode=true")
        self.assertTrue(command.startswith("/sc "))
        self.assertIn('game.get_player("测试\\\"玩家")', command)
        self.assertIn("player.cheat_mode=true", command)
        self.assertEqual(checked_integer("50", "半径", 1, 5000), 50)

    def test_discovers_factorio_in_additional_steam_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "Steam"
            library = root / "GameLibrary"
            vdf = steam / "steamapps" / "libraryfolders.vdf"
            vdf.parent.mkdir(parents=True)
            escaped_library = str(library).replace("\\", "\\\\")
            vdf.write_text(
                '"libraryfolders"\n{\n  "1"\n  {\n    "path" "'
                + escaped_library
                + '"\n  }\n}\n',
                encoding="utf-8",
            )
            executable = library / "steamapps/common/Factorio/bin/x64/factorio.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            self.assertEqual(parse_steam_libraryfolders(vdf), [library])
            self.assertEqual(
                discover_factorio_executables(
                    steam_roots=[steam],
                    include_common_locations=False,
                ),
                [executable],
            )

    def test_current_user_factorio_paths_and_newest_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory) / "Roaming"
            saves, mods = factorio_user_paths({"APPDATA": str(appdata)})
            self.assertEqual(saves, appdata / "Factorio" / "saves")
            self.assertEqual(mods, appdata / "Factorio" / "mods")
            saves.mkdir(parents=True)
            first = saves / "first.zip"
            second = saves / "second.zip"
            first.touch()
            second.touch()
            first.touch()
            self.assertIn(newest_save(saves), (first, second))

    def test_clear_achievement_flags_creates_backup_and_preserves_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / "world.zip"
            level = b"before" + b"\x01\x01\x01" + b"\xff" * 24 + b"PF" + b"after"
            with zipfile.ZipFile(save, "w") as archive:
                archive.writestr("world/level.dat0", zlib.compress(level))
                archive.writestr("world/control.lua", "-- unchanged")

            result = clear_achievement_flags(save)

            self.assertTrue(result.changed)
            self.assertTrue(result.command_was_set)
            self.assertTrue(result.editor_was_set)
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.is_file())
            with zipfile.ZipFile(save) as archive:
                fixed = zlib.decompress(archive.read("world/level.dat0"))
                self.assertIn(b"\x00\x00\x01" + b"\xff" * 24 + b"PF", fixed)
                self.assertEqual(archive.read("world/control.lua"), b"-- unchanged")


if __name__ == "__main__":
    unittest.main()
