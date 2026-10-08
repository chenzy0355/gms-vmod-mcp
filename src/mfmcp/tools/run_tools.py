"""运行与结果读取工具。"""

from __future__ import annotations

from pathlib import Path

from .. import env, state
from ..adapters import mfmodel


def register(mcp) -> None:

    @mcp.tool()
    def mfm_run(alias: str, engine: str = "", vendor: str = "", timeout: int = 1800) -> dict:
        """运行已加载的 MODFLOW 模型。

        engine: 引擎 key（如 mf2000 / mf96 / mf2005 / mfnwt / mf6），留空自动挑。
        vendor: 'vmod' 优先用 Visual MODFLOW 自带引擎，'gms' 优先用 GMS 的引擎。
        返回是否成功、是否收敛、是否 Normal termination，以及日志尾部。
        """
        return mfmodel.run_model(state.get(alias), engine_key=engine or None,
                                 vendor=vendor or None, timeout=timeout)

    @mcp.tool()
    def mfm_run_engine_direct(engine_key: str, namefile: str, timeout: int = 1800) -> dict:
        """不加载模型，直接用引擎跑一个 NAME FILE（适合已有算好的模型目录）。"""
        eng = env.find_engine(engine_key)
        if eng is None:
            return {"ok": False, "error": f"没有引擎 {engine_key}",
                    "available": [e.key for e in env.list_all_engines()]}
        return env.run_engine(eng, namefile, timeout=timeout)

    @mcp.tool()
    def mfm_heads(alias: str, layer: int = -1, kper: int = -1,
                  return_values: bool = False) -> dict:
        """读取水头结果（.hds）。kper 用 -1 表示最后一个应力期；layer>=0 只取该层。"""
        return mfmodel.read_heads(state.get(alias), layer=layer if layer >= 0 else None,
                                  kper=kper) if return_values else _head_stats(alias, layer, kper)

    @mcp.tool()
    def mfm_drawdown(alias: str, kper: int = -1, return_values: bool = False) -> dict:
        """计算降深 = 初始水头 − 当前水头，返回最大/最小值（可选完整数组）。"""
        out = mfmodel.drawdown(state.get(alias), kper=kper)
        if not return_values:
            for r in out.get("result", []):
                r.pop("values", None)
            out["hint"] = "需要完整降深场请传 return_values=True"
        return out

    @mcp.tool()
    def mfm_budget(alias: str, kper: int = -1) -> dict:
        """读取水量均衡（.bud）：各收支项的分量，用于核查模型平衡。"""
        return mfmodel.budget(state.get(alias), kper=kper)


def _head_stats(alias, layer, kper):
    out = mfmodel.read_heads(state.get(alias), layer=layer if layer >= 0 else None, kper=kper)
    for r in out.get("result", []):
        r.pop("values", None)
    out["hint"] = "需要完整水头场请传 return_values=True"
    return out
