"""发现类工具：本机软件/引擎/工程扫描。"""

from __future__ import annotations

from pathlib import Path

from .. import env
from ..adapters import gms, vmod


def register(mcp) -> None:

    @mcp.tool()
    def mfm_env() -> dict:
        """查看本机 MODFLOW 建模环境：GMS 与 Visual MODFLOW 的安装状态、可用数值引擎列表。"""
        return {
            "config_path": str(env.CONFIG_PATH),
            "config": {k: env.CONFIG.get(k) for k in ("gms_home", "vmod_home", "workspace")},
            "gms": env.gms_info(),
            "visual_modflow": env.vmod_info(),
            "engines_total": len(env.list_all_engines()),
            "workspace": str(env.workspace()),
        }

    @mcp.tool()
    def mfm_engines(kind: str = "", vendor: str = "") -> dict:
        """列出可用数值引擎。kind 可选 flow/transport/particle/budget/calibration；vendor 可选 gms/vmod。"""
        engs = env.list_all_engines()
        if kind:
            engs = [e for e in engs if e.kind == kind]
        if vendor:
            engs = [e for e in engs if e.vendor == vendor]
        return {"count": len(engs),
                "engines": [{"key": e.key, "name": e.name, "kind": e.kind,
                             "vendor": e.vendor, "version": e.version,
                             "path": e.path, "exists": e.exists()} for e in engs]}

    @mcp.tool()
    def mfm_scan(root: str = "", depth: int = 4) -> dict:
        """扫描目录，找出可被接管的模型：Visual MODFLOW 工程、GMS 工程、标准 MODFLOW 模型。"""
        base = Path(root) if root else env.workspace().parent
        if not base.exists():
            return {"error": f"目录不存在：{base}"}
        vp = vmod.find_projects(base, max_depth=depth)
        gp = gms.find_gms_projects(base, max_depth=depth)
        mf = vmod.find_modflow_files(base)
        return {
            "root": str(base),
            "visual_modflow_projects": vp,
            "gms_projects": gp,
            "standard_modflow_models": mf,
            "summary": {"vmod": len(vp), "gms": len(gp), "modflow": len(mf)},
            "note": "GMS 工程需先在 GUI 里导出为 MODFLOW 文本才能被读取。",
        }

    @mcp.tool()
    def mfm_xms_status() -> dict:
        """探测 GMS 的 xms_api 是否可用（需要 GMS 正在运行）。"""
        return gms.xms_api_status()
