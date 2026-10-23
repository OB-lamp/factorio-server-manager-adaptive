from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


STABLE_FACTORIO_VERSION = "2.1.23"
ADAPTIVE_FACTORIO_VERSIONS = (
    "2.0.77",
    "2.1.14",
    "2.1.16",
    "2.1.17",
    STABLE_FACTORIO_VERSION,
)
STABLE_EDITION_ENV = "FACTORIO_SERVER_MANAGER_EDITION"
STABLE_EDITION_VALUE = "stable-2.1.23"


@dataclass(frozen=True)
class Edition:
    title: str
    subtitle: str
    supported_versions: tuple[str, ...]
    stable_only: bool


def resolve_edition(environment: Mapping[str, str] | None = None) -> Edition:
    values = os.environ if environment is None else environment
    if values.get(STABLE_EDITION_ENV) == STABLE_EDITION_VALUE:
        return Edition(
            title=f"Factorio {STABLE_FACTORIO_VERSION} 稳定版开服工具",
            subtitle="专用于当前稳定版 Factorio 2.1.23 · 独立数据目录、RCON、快捷指令与实时控制台",
            supported_versions=(STABLE_FACTORIO_VERSION,),
            stable_only=True,
        )

    versions = " / ".join(ADAPTIVE_FACTORIO_VERSIONS)
    return Edition(
        title=f"Factorio {versions} 自适配开服工具",
        subtitle="自动识别受支持版本 · 独立数据目录、RCON、快捷指令与实时控制台",
        supported_versions=ADAPTIVE_FACTORIO_VERSIONS,
        stable_only=False,
    )
