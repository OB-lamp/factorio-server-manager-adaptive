from __future__ import annotations

import copy
import os
import shutil
import uuid
import zlib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class AchievementFlagError(RuntimeError):
    pass


@dataclass(frozen=True)
class AchievementFlagResult:
    save_path: Path
    backup_path: Path | None
    entry_name: str
    offset: int
    command_was_set: bool
    editor_was_set: bool
    changed: bool


_SENTINEL = b"\xff" * 24 + b"PF"


def clear_achievement_flags(save_path: Path) -> AchievementFlagResult:
    """Clear Factorio 2.1.x command/editor flags in a stopped save, atomically."""
    save_path = save_path.resolve()
    if not save_path.is_file() or save_path.suffix.casefold() != ".zip":
        raise AchievementFlagError(f"存档不存在或不是 ZIP：{save_path}")

    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    candidates: list[tuple[int, str, int, bytearray, str]] = []
    try:
        with zipfile.ZipFile(save_path, "r") as source:
            if source.testzip() is not None:
                raise AchievementFlagError("存档 ZIP 完整性检查失败")
            for index, info in enumerate(source.infolist()):
                payload = source.read(info.filename)
                entries.append((copy.copy(info), payload))
                name = info.filename.rsplit("/", 1)[-1]
                if not (name.startswith("level.dat") and name[-1:].isdigit()):
                    continue
                unpacked, compression = _decompress_level(payload)
                if unpacked is None:
                    continue
                search_from = 0
                while True:
                    sentinel_at = unpacked.find(_SENTINEL, search_from)
                    if sentinel_at < 0:
                        break
                    flags_at = sentinel_at - 3
                    if flags_at >= 0 and all(value in (0, 1) for value in unpacked[flags_at:sentinel_at]):
                        candidates.append((index, info.filename, flags_at, unpacked, compression))
                    search_from = sentinel_at + 1
    except (OSError, zipfile.BadZipFile, zlib.error) as exc:
        raise AchievementFlagError(f"无法读取存档：{exc}") from exc

    if len(candidates) != 1:
        raise AchievementFlagError(
            f"无法唯一定位 Factorio 2.1.x 成就标记（找到 {len(candidates)} 处）；未修改存档"
        )

    entry_index, entry_name, offset, unpacked, compression = candidates[0]
    command_was_set = unpacked[offset] == 1
    editor_was_set = unpacked[offset + 1] == 1
    if not command_was_set and not editor_was_set:
        return AchievementFlagResult(
            save_path, None, entry_name, offset, False, False, False
        )

    unpacked[offset] = 0
    unpacked[offset + 1] = 0
    info, _old_payload = entries[entry_index]
    entries[entry_index] = (info, _compress_level(bytes(unpacked), compression))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = _unused_path(save_path.with_name(f"{save_path.stem}.pre-achievement-fix-{stamp}.zip"))
    temp_path = save_path.with_name(f".{save_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(save_path, backup_path)
        with zipfile.ZipFile(temp_path, "x") as output:
            for entry_info, payload in entries:
                output.writestr(entry_info, payload, compress_type=entry_info.compress_type)
        with zipfile.ZipFile(temp_path, "r") as check:
            bad_entry = check.testzip()
            if bad_entry is not None:
                raise AchievementFlagError(f"修复后的 ZIP 校验失败：{bad_entry}")
        os.replace(temp_path, save_path)
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, AchievementFlagError):
            raise
        raise AchievementFlagError(f"写入修复存档失败：{exc}") from exc

    return AchievementFlagResult(
        save_path,
        backup_path,
        entry_name,
        offset,
        command_was_set,
        editor_was_set,
        True,
    )


def _decompress_level(payload: bytes) -> tuple[bytearray | None, str]:
    try:
        return bytearray(zlib.decompress(payload)), "zlib"
    except zlib.error:
        try:
            return bytearray(zlib.decompress(payload, -zlib.MAX_WBITS)), "raw"
        except zlib.error:
            return None, "none"


def _compress_level(payload: bytes, compression: str) -> bytes:
    if compression == "zlib":
        return zlib.compress(payload)
    if compression == "raw":
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        return compressor.compress(payload) + compressor.flush()
    return payload


def _unused_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1
