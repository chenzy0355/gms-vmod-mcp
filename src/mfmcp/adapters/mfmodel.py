"""MODFLOW 模型适配器（FloPy 内核）。

GMS 与 Visual MODFLOW 本质都是 MODFLOW 的前后处理器，导出的都是标准
MODFLOW 输入/输出文件。因此只要模型是标准文件，本适配器就能读参数、改
数组、跑引擎、读结果——两家软件共用同一套能力。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np  # 独立导入：即使 flopy 缺失，数组工具仍可工作

try:
    import flopy
except Exception as exc:  # pragma: no cover
    flopy = None
    _IMPORT_ERR = exc
else:
    _IMPORT_ERR = None

from .. import env


class MFError(RuntimeError):
    pass


def _require_flopy():
    if flopy is None:
        raise MFError(
            "未安装 flopy。请在 MCP 专用环境里执行："
            "pip install flopy mcp  （原始错误：%s）" % _IMPORT_ERR
        )


# ---------------------------------------------------------------- 识别

NAM_VERSIONS = {
    "mf2k": "mf2k", "mf2005": "mf2005", "mfnwt": "mfnwt",
    "mfusg": "mfusg", "mf6": "mf6", "modflow-2005": "mf2005",
}


def detect_model_type(path: str | Path) -> dict:
    """判断给定路径是什么：mf6 仿真 / 传统 name file / 只有部分文件。"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in (".nam", ".mfi"):
            body = p.read_text("latin-1", errors="ignore").lower()
            if "mf6" in body or "tdis6" in body:
                return {"type": "mf6", "nam": str(p), "name": p.stem}
            for tag, ver in NAM_VERSIONS.items():
                if tag in body:
                    return {"type": ver, "nam": str(p), "name": p.stem}
            return {"type": "mf2005", "nam": str(p), "name": p.stem,
                    "note": "未在 NAME FILE 里识别出版本，按 MODFLOW-2005 处理"}
        if p.suffix.lower() == ".dis":
            return {"type": "partial", "dis": str(p), "name": p.stem,
                    "note": "只找到 DIS 文件，缺少 NAME FILE，无法直接加载"}
        if p.suffix.lower() == ".nam6" or p.name.endswith("mfsim.nam"):
            return {"type": "mf6", "nam": str(p), "name": p.stem}
        raise MFError(f"不认识的模型文件：{p.name}")
    if p.is_dir():
        for cand in list(p.rglob("*.nam")) + list(p.rglob("*.mfi")):
            return detect_model_type(cand)
        raise MFError(f"目录里没有找到 .nam / .mfi / mfsim.nam：{p}")
    raise MFError(f"路径不存在：{p}")


# ---------------------------------------------------------------- 加载

def load_model(path: str | Path, exe_name: str | None = None,
               load_only: list[str] | None = None) -> Any:
    """加载 MODFLOW 模型（自动区分 MF6 / 传统版本）。"""
    _require_flopy()
    info = detect_model_type(path)
    nam = Path(info["nam"])
    if info["type"] == "mf6":
        sim = flopy.mf6.MFSimulation.load(
            sim_ws=str(nam.parent), verbosity_level=0, exe_name=exe_name)
        return sim
    kw: dict[str, Any] = dict(
        model_ws=str(nam.parent), check=False, verbose=False, exe_name=exe_name,
    )
    if load_only:
        kw["load_only"] = load_only
    model = flopy.modflow.Modflow.load(nam.name, **kw)
    return model


def _models(obj) -> list:
    """从仿真或单模型统一取出模型列表。"""
    if flopy is not None and isinstance(obj, flopy.mf6.MFSimulation):
        return [obj.get_model(n) for n in obj.model_names]
    return [obj]


def model_summary(obj) -> dict:
    """模型结构概览：网格、层、包、应力期。"""
    out = {"simulation": None, "models": []}
    if flopy is not None and isinstance(obj, flopy.mf6.MFSimulation):
        out["simulation"] = {
            "name": obj.sim_name, "ws": str(obj.sim_ws),
            "models": list(obj.model_names),
        }
    for m in _models(obj):
        mg = m.modelgrid
        rec: dict[str, Any] = {
            "name": getattr(m, "name", "?"),
            "version": getattr(m, "version", "mf6"),
            "workspace": str(getattr(m, "model_ws", "")),
            "grid": {
                "nlay": getattr(mg, "nlay", None),
                "nrow": getattr(mg, "nrow", None),
                "ncol": getattr(mg, "ncol", None),
                "ncpl": getattr(mg, "ncpl", None),
                "type": getattr(mg, "grid_type", None),
                "delr": _scalar(getattr(mg, "delr", None)),
                "delc": _scalar(getattr(mg, "delc", None)),
            },
        }
        dis = m.get_package("DIS") or m.get_package("DISU") or m.get_package("DISV")
        if dis is not None:
            rec["discretization"] = dis.name[0]
            if hasattr(dis, "nper"):
                rec["nper"] = int(dis.nper)
                if hasattr(dis, "perlen"):
                    pl = np.atleast_1d(dis.perlen.array)
                    rec["periods"] = [round(float(x), 2) for x in pl]
        pkgs = []
        for p in m.packagelist if hasattr(m, "packagelist") else []:
            pkgs.append(p.name[0] if isinstance(p.name, tuple) else str(p.name))
        rec["packages"] = sorted(set(pkgs))
        out["models"].append(rec)
    return out


def _scalar(v):
    try:
        a = np.atleast_1d(v)
        return float(a[0]) if a.size == 1 else None
    except Exception:
        return None


# ---------------------------------------------------------------- 数组读写

_ARRAY_ALIASES = {
    "k": ("k", "hk"), "hk": ("k", "hk"), "kx": ("k", "hk"),
    "k33": ("k33", "vk"), "vk": ("k33", "vk"),
    "top": ("top",), "botm": ("botm", "bot"), "bot": ("botm", "bot"),
    "laytyp": ("laytyp",), "strt": ("strt",), "ibound": ("ibound",),
    "sy": ("sy",), "ss": ("ss",), "rech": ("rech",),
}


def _array_holder(model, name: str):
    """找到承载某数组的包与属性名。"""
    n = name.lower()
    candidates = _ARRAY_ALIASES.get(n, (n,))
    for m in _models(model):
        for pkg in (m.packagelist if hasattr(m, "packagelist") else []):
            for cand in candidates:
                if hasattr(pkg, cand) and getattr(pkg, cand) is not None:
                    return pkg, cand
    raise MFError(f"模型里找不到数组「{name}」。可用别名：{sorted(_ARRAY_ALIASES)}")


def get_array(model, name: str, layer: int | None = None) -> dict:
    pkg, attr = _array_holder(model, name)
    arr = np.asarray(getattr(pkg, attr).array, dtype=float)
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    out = {"array": name, "package": pkg.name[0] if isinstance(pkg.name, tuple) else str(pkg.name),
           "shape": list(arr.shape),
           "min": float(np.nanmin(arr)), "max": float(np.nanmax(arr)),
           "mean": float(np.nanmean(arr))}
    if layer is not None:
        if arr.ndim == 3:
            arr = arr[layer]
        out["layer"] = layer
        out["shape"] = list(arr.shape)
        out["min"] = float(np.nanmin(arr)); out["max"] = float(np.nanmax(arr))
        out["mean"] = float(np.nanmean(arr))
    out["values"] = arr.tolist()
    return out


def set_array(model, name: str, values, layer: int | None = None,
              factor: float | None = None) -> dict:
    """改数组。``values`` 可以是标量、嵌套列表；也可只给 ``factor`` 做整体缩放。"""
    pkg, attr = _array_holder(model, name)
    holder = getattr(pkg, attr)
    arr = np.array(holder.array, dtype=float)
    if factor is not None:
        target = arr[layer] if (layer is not None and arr.ndim == 3) else arr
        before = (float(np.nanmin(target)), float(np.nanmax(target)))
        target *= float(factor)
        if layer is not None and arr.ndim == 3:
            arr[layer] = target
        after = (float(np.nanmin(target)), float(np.nanmax(target)))
        holder.set_data(arr)
        return {"array": name, "op": "scale", "factor": factor, "layer": layer,
                "before": before, "after": after}
    new = np.asarray(values, dtype=float)
    if layer is not None and arr.ndim == 3:
        if new.shape == arr[layer].shape:
            arr[layer] = new
        else:
            arr[layer] = new  # 广播
    elif new.shape == arr.shape:
        arr = new
    elif new.size == 1:
        arr = np.full_like(arr, float(new.ravel()[0]))
    else:
        raise MFError(f"新数组形状 {new.shape} 与模型 {arr.shape} 不匹配")
    holder.set_data(arr)
    return {"array": name, "op": "set", "shape": list(arr.shape),
            "min": float(np.nanmin(arr)), "max": float(np.nanmax(arr)),
            "mean": float(np.nanmean(arr))}


def save_model(model, path: str | Path | None = None) -> dict:
    _require_flopy()
    if path:
        Path(path).mkdir(parents=True, exist_ok=True)
        model.model_ws = str(path)
    model.write_input()
    return {"saved_to": str(model.model_ws)}


# ---------------------------------------------------------------- 边界条件

_BC_PACKAGES = ("WEL", "RIV", "RCH", "GHB", "CHD", "DRN", "EVT", "STR", "SFR",
                "LAK", "MNW2", "UZF", "MNW1", "DRT")


def list_bc(model, pkg_name: str | None = None) -> list[dict]:
    out = []
    names = [pkg_name.upper()] if pkg_name else _BC_PACKAGES
    for m in _models(model):
        for nm in names:
            pkg = m.get_package(nm)
            if pkg is None:
                continue
            try:
                data = pkg.stress_period_data
            except Exception:
                data = None
            recs: dict[str, int] = {}
            if data is not None:
                try:
                    for k, v in dict(data).items():
                        recs[str(k)] = int(np.asarray(v).shape[0])
                except Exception:
                    pass
            out.append({
                "model": getattr(m, "name", "?"),
                "package": nm,
                "cells_by_period": recs,
                "total_cells": sum(recs.values()),
                "kij_period0": _sample_cells(data),
            })
    return out


def _sample_cells(data, n: int = 5) -> list:
    if data is None:
        return []
    try:
        first = dict(data).get(0)
        if first is None:
            first = next(iter(dict(data).values()))
        arr = np.asarray(first, dtype=object)
        return [list(map(_py, row)) for row in arr[:n]]
    except Exception:
        return []


def _py(x):
    try:
        return int(x)
    except Exception:
        try:
            return round(float(x), 4)
        except Exception:
            return str(x)


def edit_bc(model, package: str, period: int = 0, add: list | None = None,
            remove_index: list[int] | None = None, scale_value: float | None = None,
            value_column: int = -1) -> dict:
    """边界条件增删改。

    * ``add``：新记录列表，如 ``[[layer, row, col, rate], ...]``
    * ``remove_index``：删掉该应力期第 N 条记录
    * ``scale_value``：把第 ``value_column`` 列整体乘以系数
    """
    pkg = model.get_package(package.upper())
    if pkg is None:
        raise MFError(f"模型里没有 {package} 包")
    data = dict(pkg.stress_period_data)
    cur = np.asarray(data.get(period), dtype=object).copy()
    changes = {}

    if remove_index:
        keep = [i for i in range(len(cur)) if i not in set(remove_index)]
        removed = [[_py(v) for v in cur[i]] for i in remove_index if i < len(cur)]
        cur = cur[keep]
        changes["removed"] = removed

    if scale_value is not None:
        col = value_column if value_column >= 0 else cur.shape[1] - 1
        old = [_py(r[col]) for r in cur]
        for r in cur:
            r[col] = float(r[col]) * float(scale_value)
        changes["scaled"] = {"column": col, "factor": scale_value,
                             "old_range": [min(old), max(old)] if len(cur) else []}

    if add:
        cur = np.vstack([cur, np.asarray(add, dtype=object)]) if len(cur) else np.asarray(add, dtype=object)
        changes["added"] = len(add)

    data[period] = cur
    pkg.stress_period_data = data
    return {"package": package.upper(), "period": period,
            "count_before": len(np.asarray(dict(pkg.stress_period_data).get(period, []))),
            "count_after": len(cur), "changes": changes}


# ---------------------------------------------------------------- 运行

def run_model(model, engine_key: str | None = None, vendor: str | None = None,
              timeout: int = 1800) -> dict:
    """写输入 -> 调引擎跑 -> 返回结果。

    默认优先用 Visual MODFLOW 自带引擎（体积小、启动快）；可用 vendor='gms'
    切到 GMS 的引擎。运行委托给 env.run_engine，以便统一处理老式 USGS 引擎
    「从 stdin 读 NAME FILE」的行为。
    """
    _require_flopy()
    eng = env.resolve_engine(engine_key, "flow", vendor)
    if eng is None:
        eng = env.resolve_engine(kind="flow")
    if eng is None:
        raise MFError("本机没有找到任何 MODFLOW 可执行引擎，请检查 config/env.json")

    models = _models(model)
    if len(models) != 1:
        raise MFError(f"该模型含 {len(models)} 个子模型，请分别运行（MF6 请用 mfsim.nam 直接跑）")
    m = models[0]

    m.exe_name = eng.path
    m.write_input()
    nam = Path(m.model_ws) / f"{m.name}.nam"
    if not nam.exists():
        cands = list(Path(m.model_ws).glob("*.nam"))
        if not cands:
            raise MFError(f"写输入后仍未找到 NAME FILE：{Path(m.model_ws)}")
        nam = cands[0]

    import time
    t0 = time.time()
    res = env.run_engine(eng, nam, cwd=m.model_ws, timeout=timeout)

    # 输出文件（.hds/.hed/.lst/.list）是否在本次运行中被写出
    fresh: list[str] = []
    for suffix in (".hds", ".hed", ".lst", ".list", ".bud", ".cbc"):
        for cand in (Path(m.model_ws) / f"{m.name}{suffix}",
                     Path(m.model_ws) / f"{m.name}{suffix.upper()}"):
            if cand.exists() and cand.stat().st_mtime >= t0 - 2:
                fresh.append(cand.name)
                break

    # 成功判定：VMod 的 MODFLOW-2000 构建不打印 "Normal termination"，
    # 因此以「无报错 + 收敛 + 产出新结果文件」为准。
    res["fresh_outputs"] = fresh
    res["success"] = bool(
        res.get("ok")
        and not res.get("errors")
        and res.get("converged", True)
        and (res.get("normal_termination") or fresh)
    )
    res["namefile"] = str(nam)
    res["workspace"] = str(m.model_ws)
    res["log_tail"] = res.get("stdout_tail", "")[-5000:]
    return res


# ---------------------------------------------------------------- 结果

def read_heads(model, layer: int | None = None, kper: int = -1) -> dict:
    _require_flopy()
    from flopy.utils import HeadFile
    res = []
    for m in _models(model):
        hf = None
        for suffix in (".hds", ".hed"):
            cand = Path(m.model_ws) / f"{m.name}{suffix}"
            if cand.exists():
                hf = HeadFile(str(cand))
                break
        if hf is None:
            res.append({"model": getattr(m, "name", "?"), "error": "找不到 .hds 水头文件"})
            continue
        times = hf.get_times()
        arr = hf.get_data(totim=times[kper])
        arr = np.asarray(arr, dtype=float)
        info = {
            "model": getattr(m, "name", "?"), "times": times, "kper": kper,
            "shape": list(arr.shape),
            "min": float(np.nanmin(arr)), "max": float(np.nanmax(arr)),
            "mean": float(np.nanmean(arr)),
        }
        if layer is not None and arr.ndim == 3:
            info["layer"] = layer
            info["values"] = np.asarray(arr[layer], dtype=float).tolist()
        else:
            info["values"] = arr.tolist()
        res.append(info)
    return {"result": res}


def drawdown(model, kper: int = -1) -> dict:
    """降深 = 初始水头 - 当前水头。"""
    _require_flopy()
    from flopy.utils import HeadFile
    out = []
    for m in _models(model):
        cand = Path(m.model_ws) / f"{m.name}.hds"
        if not cand.exists():
            out.append({"model": getattr(m, "name", "?"), "error": "找不到 .hds"})
            continue
        hf = HeadFile(str(cand))
        t = hf.get_times()[kper]
        cur = np.asarray(hf.get_data(totim=t), dtype=float)
        strt = None
        pkg = m.get_package("BAS6") or m.get_package("IC")
        if pkg is not None and hasattr(pkg, "strt"):
            strt = np.asarray(pkg.strt.array, dtype=float)
        if strt is None:
            out.append({"model": getattr(m, "name", "?"), "error": "取不到初始水头"})
            continue
        if strt.shape != cur.shape and strt.ndim == 3 and cur.ndim == 3:
            strt = strt[:cur.shape[0]]
        dd = strt - cur
        out.append({"model": getattr(m, "name", "?"), "kper": kper,
                    "max_drawdown": float(np.nanmax(dd)),
                    "min_drawdown": float(np.nanmin(dd)),
                    "values": dd.tolist()})
    return {"result": out}


def _budget_sum(arr) -> float:
    """均衡记录求和。

    紧凑格式下分两类记录：
    * 数组型（FLOW RIGHT FACE 等）—— 直接是 3D 数值数组；
    * 列表型（WELLS / HEAD DEP BOUNDS / RECHARGE 等）—— 结构化数组，
      末列才是流量。
    """
    a = np.asarray(arr)
    if a.dtype.names:                     # 结构化（列表型记录）
        vals = a[a.dtype.names[-1]]
    else:
        vals = a.ravel()
    return float(np.nansum(np.asarray(vals, dtype=float)))


def budget(model, kper: int = -1) -> dict:
    _require_flopy()
    from flopy.utils import CellBudgetFile
    out = []
    for m in _models(model):
        # 均衡文件名视 OC 的单位号而定：默认 * .bud，自定义单位常为 *.cbc
        cand = None
        for suffix in (".bud", ".cbc", ".BUD", ".CBC"):
            p = Path(m.model_ws) / f"{m.name}{suffix}"
            if p.exists():
                cand = p
                break
        if cand is None:
            out.append({"model": getattr(m, "name", "?"), "error": "找不到 .bud/.cbc"})
            continue
        cbf = CellBudgetFile(str(cand))
        times = cbf.get_times()
        t = times[kper]
        # 用文件里实际存在的标签，避免拼写差异
        try:
            labels = [str(x).strip() for x in cbf.get_unique_record_names(decode=True)]
        except Exception:
            labels = []
        recs = []
        for txt in labels:
            if not txt or txt.startswith("FLOW "):
                continue
            if txt not in ("WELLS", "HEAD DEP BOUNDS", "CONSTANT HEAD", "RECHARGE",
                           "RIVER LEAKAGE", "DRAINS", "ET", "STORAGE",
                           "TOTAL IN", "TOTAL OUT"):
                continue
            try:
                d = cbf.get_data(text=txt, totim=t)
                if not d:
                    continue
                recs.append({"term": txt, "sum": _budget_sum(d[0])})
            except Exception:
                continue
        out.append({"model": getattr(m, "name", "?"), "budget_file": cand.name,
                    "times": times, "kper": kper, "terms": recs,
                    "all_labels": labels})
    return {"result": out}


def zone_budget(model, zones: list[int] | None = None) -> dict:
    return {"hint": "Zone Budget 请用引擎 zbud 运行，或调用 mfm_run(vendor='vmod', engine='zonbud')",
            "zones": zones or []}
