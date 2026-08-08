from __future__ import annotations

import os
import re
import string
from pathlib import Path
from typing import Iterable, Mapping


def factorio_user_paths(environment: Mapping[str, str] | None = None) -> tuple[Path, Path]:
    env = os.environ if environment is None else environment
    appdata_value = env.get("APPDATA")
    appdata = Path(appdata_value) if appdata_value else Path.home() / "AppData" / "Roaming"
    factorio_data = appdata / "Factorio"
    return factorio_data / "saves", factorio_data / "mods"


def parse_steam_libraryfolders(path: Path) -> list[Path]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    libraries: list[Path] = []
    for match in re.finditer(r'"path"\s*"((?:\\.|[^"\\])*)"', text):
        value = match.group(1).replace(r"\\", "\\").replace(r'\"', '"')
        if value:
            libraries.append(Path(value))
    return _unique_paths(libraries)


def discover_steam_roots(environment: Mapping[str, str] | None = None) -> list[Path]:
    env = os.environ if environment is None else environment
    roots: list[Path] = []
    for variable in ("STEAM_PATH", "STEAM_HOME"):
        if env.get(variable):
            roots.append(Path(env[variable]))

    if os.name == "nt":
        try:
            import winreg

            registry_locations = (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", ("SteamPath", "InstallPath")),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam", ("InstallPath",)),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam", ("InstallPath",)),
            )
            for hive, key_name, value_names in registry_locations:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        for value_name in value_names:
                            try:
                                value, _kind = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            if value:
                                roots.append(Path(str(value)))
                except OSError:
                    continue
        except ImportError:
            pass

    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if env.get(variable):
            roots.append(Path(env[variable]) / "Steam")
    for drive in string.ascii_uppercase:
        roots.append(Path(f"{drive}:\\steam"))
        roots.append(Path(f"{drive}:\\Steam"))
    return _unique_paths(path for path in roots if path.is_dir())


def discover_factorio_executables(
    *,
    steam_roots: Iterable[Path] | None = None,
    extra_candidates: Iterable[Path] = (),
    environment: Mapping[str, str] | None = None,
    include_common_locations: bool = True,
) -> list[Path]:
    env = os.environ if environment is None else environment
    roots = list(steam_roots) if steam_roots is not None else discover_steam_roots(env)
    libraries: list[Path] = []
    for root in roots:
        libraries.append(root)
        libraries.extend(parse_steam_libraryfolders(root / "steamapps" / "libraryfolders.vdf"))

    candidates = [Path(value) for value in extra_candidates if str(value)]
    candidates.extend(
        library / "steamapps" / "common" / "Factorio" / "bin" / "x64" / "factorio.exe"
        for library in _unique_paths(libraries)
    )
    if include_common_locations:
        for drive in string.ascii_uppercase:
            candidates.extend(
                (
                    Path(f"{drive}:\\Factorio\\bin\\x64\\factorio.exe"),
                    Path(f"{drive}:\\Games\\Factorio\\bin\\x64\\factorio.exe"),
                )
            )
        local_appdata = env.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "Programs" / "Factorio" / "bin" / "x64" / "factorio.exe")
    return _unique_paths(path for path in candidates if path.is_file())


def newest_save(saves_directory: Path) -> Path | None:
    try:
        saves = [path for path in saves_directory.glob("*.zip") if path.is_file()]
        return max(saves, key=lambda path: path.stat().st_mtime_ns, default=None)
    except OSError:
        return None


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    output: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = os.path.normcase(os.path.abspath(os.path.expandvars(str(path))))
        if normalized not in seen:
            seen.add(normalized)
            output.append(Path(normalized))
    return output
