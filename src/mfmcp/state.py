"""进程内状态：已加载模型的缓存。

MCP 工具调用是无状态的，但把 FloPy 模型对象缓存在 server 进程里，可以让
"加载一次 -> 改参数 -> 跑 -> 读结果" 不必反复重新解析文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CACHE: dict[str, Any] = {}
_LAST: str | None = None


def put(name: str, obj: Any) -> str:
    global _LAST
    _CACHE[name] = obj
    _LAST = name
    return name


def get(name: str | None = None) -> Any:
    key = name or _LAST
    if key is None or key not in _CACHE:
        raise KeyError(f"没有已加载的模型「{name}」。请先调用 mfm_load。"
                       f"当前已加载：{list(_CACHE)}")
    return _CACHE[key]


def get_or_none(name: str | None = None) -> Any:
    try:
        return get(name)
    except KeyError:
        return None


def keys() -> list[str]:
    return list(_CACHE)


def last() -> str | None:
    return _LAST


def drop(name: str) -> bool:
    return _CACHE.pop(name, None) is not None
