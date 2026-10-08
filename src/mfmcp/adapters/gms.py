"""GMS (Aquaveo) 适配器。

GMS 10.4 提供两部分可利用的能力：

1. **数值引擎**（``models/`` 下的 exe）—— 可命令行直接跑 NAME FILE，能力最实在。
2. **xms_api**（``Python35/Lib/site-packages/xms_api.pyd``）—— 一个*客户端* API，
   必须连到正在运行的 GMS 进程；脱离 GMS 会抛
   ``SystemError: initialization of xms_api raised unreported exception``。

因此这里的策略是：**优先走引擎与文件**，xms_api 仅作为「GMS 已打开时」的增强项探测，
不把它当作硬依赖。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .. import env

GMS_SUFFIXES = [".gpr", ".gsm", ".xgpr"]  # GMS 工程/模型文件


# ---------------------------------------------------------------- 工程发现

def find_gms_projects(root: str | Path, max_depth: int = 5) -> list[dict]:
    root = Path(root)
    out: list[dict] = []
    if not root.exists():
        return out
    for suf in GMS_SUFFIXES:
        for p in root.rglob(f"*{suf}"):
            if len(p.relative_to(root).parts) > max_depth:
                continue
            out.append({"kind": "gms", "name": p.stem, "suffix": suf,
                        "path": str(p), "dir": str(p.parent),
                        "size_kb": round(p.stat().st_size / 1024, 1)})
    return out


def gms_export_hint() -> dict:
    """GMS 工程无法直接读；说明怎么把模型导出成标准 MODFLOW 文件以便接进来。"""
    return {
        "why": "GMS 的 .gpr 是私有二进制格式，外部无法解析。",
        "how": [
            "GUI：MODFLOW 菜单 → 'Export Native MF2K text...'（或 Save As → MODFLOW 文本）",
            "得到 .nam + .dis/.bcf/.wel/... 标准文件后，用 mfm_load 直接接管",
            "或：直接把 GMS 工程另存为 MODFLOW 文件，落到本 MCP 的工作目录"
            "（见 config/env.json 的 workspace，默认 <repo>/workspace）",
        ],
        "note": "导出后即可用本 MCP 的全部参数/运行/结果/出图能力，无需 GMS 参与。",
    }


# ---------------------------------------------------------------- 引擎

def list_engines() -> list[dict]:
    return [dict(key=e.key, name=e.name, path=e.path, kind=e.kind,
                 version=e.version, exists=e.exists())
            for e in env.list_gms_engines()]


def run_namefile(engine_key: str, namefile: str | Path,
                 timeout: int = 1800) -> dict:
    eng = env.find_engine(engine_key, "gms")
    if eng is None:
        avail = [e["key"] for e in list_engines()]
        return {"ok": False, "error": f"GMS 里没有引擎 {engine_key}", "available": avail}
    return env.run_engine(eng, namefile, timeout=timeout)


# ---------------------------------------------------------------- xms_api 桥

def xms_api_status() -> dict:
    """探测 xms_api 是否可用（是否已连上正在运行的 GMS）。"""
    home = Path(env.CONFIG.get("gms_home") or "")
    py = home / "Python35" / "python.exe"
    pyd = home / "Python35" / "Lib" / "site-packages" / "xms_api.pyd"
    if not pyd.exists():
        return {"available": False, "reason": "未找到 xms_api.pyd"}
    if not py.exists():
        return {"available": False, "reason": "未找到 GMS 内置 Python35"}
    probe = "import xms_api; print('XMS_OK')"
    try:
        r = subprocess.run([str(py), "-c", probe], capture_output=True, timeout=20)
    except Exception as exc:
        return {"available": False, "reason": f"探测失败：{exc}"}
    ok = b"XMS_OK" in r.stdout
    return {
        "available": ok,
        "python": str(py),
        "stdout": r.stdout.decode("utf-8", "replace")[-500:],
        "stderr": r.stderr.decode("utf-8", "replace")[-500:],
        "reason": "" if ok else "GMS 进程未运行，或版本不匹配（xms_api 是客户端 API）",
    }
