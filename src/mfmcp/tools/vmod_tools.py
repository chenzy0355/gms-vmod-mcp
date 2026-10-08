"""Visual MODFLOW 工程读写工具（.vmf XML）。"""

from __future__ import annotations

from pathlib import Path

from .. import env
from ..adapters import gms, vmod


def register(mcp) -> None:

    @mcp.tool()
    def vmod_info(vmf: str) -> dict:
        """读取 Visual MODFLOW 工程概况：网格尺寸、层数、引擎、水流变体、图层与地理配准。

        vmf: .vmf 文件路径（VMod 工程目录里通常与 .vmp 同名）。
        """
        return vmod.project_info(vmf)

    @mcp.tool()
    def vmod_search_params(vmf: str, query: str, limit: int = 50) -> dict:
        """在 VMod 工程里检索参数（按名称/描述/单位模糊匹配），如 query='Kx' 或 'Recharge' 或 'MXITER'。

        返回每项的名称、当前值、类型、单位、说明与是否可写。
        """
        hits = vmod.search_params(vmf, query, limit=limit)
        return {"query": query, "count": len(hits), "params": hits}

    @mcp.tool()
    def vmod_list_params(vmf: str, writable_only: bool = True, limit: int = 300) -> dict:
        """列出 VMod 工程的全部参数（默认只列可写的）。"""
        out = []
        for rec in vmod.iter_params(vmf):
            if writable_only and not rec.get("writable"):
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return {"count": len(out), "params": out}

    @mcp.tool()
    def vmod_set_params(vmf: str, edits: list[dict], out_path: str = "",
                        dry_run: bool = True) -> dict:
        """批量修改 VMod 工程参数。

        edits 形如 [{"name":"MXITER","value":"500"}, {"name":"HCLOSE","key":"0001.0002.0003","value":"0.001"}]。
        dry_run=True 时只预演不落盘；out_path 给定时写到新文件（原文件保留并生成 .bak）。
        """
        return vmod.set_params(vmf, edits, out_path=out_path or None, dry_run=dry_run)

    @mcp.tool()
    def vmod_set_engine(vmf: str, engine_name: str, out_path: str = "") -> dict:
        """切换 VMod 工程的水流引擎，如 engine_name='MODFLOW 2000' 或 'MODFLOW-96'。"""
        return vmod.set_engine(vmf, engine_name, out_path=out_path or None)

    @mcp.tool()
    def vmod_run_engine(engine_key: str, namefile: str, timeout: int = 1800) -> dict:
        """用引擎直接跑一个 MODFLOW NAME FILE。

        engine_key 取 mfm_engines 返回的 key，例如 'mf2000'(MODFLOW-2000)、'mf96'、
        'mt3dms'、'modpath'、'zonbud'。namefile 是 .nam 文件路径。
        """
        eng = env.find_engine(engine_key)
        if eng is None:
            return {"ok": False, "error": f"没有引擎 {engine_key}",
                    "available": [e.key for e in env.list_all_engines()]}
        return env.run_engine(eng, namefile, timeout=timeout)

    @mcp.tool()
    def vmod_read_namefile(namefile: str) -> dict:
        """解析 VMod 的 NAME FILE（.mfi）或标准 .nam，列出每个包的单元号、路径、文件是否存在。

        用途：跑之前先看模型文件齐不齐。
        """
        recs = vmod.read_namefile(namefile)
        return {"namefile": str(namefile),
                "records": len([r for r in recs if r.get("type")]),
                "external_refs": sum(1 for r in recs if r.get("external")),
                "missing": [r["name"] for r in recs if r.get("name") and not r["exists"]],
                "entries": recs}

    @mcp.tool()
    def vmod_portable_namefile(namefile: str, out_dir: str = "", out_name: str = "",
                               dry_run: bool = False) -> dict:
        """把 NAME FILE 里的绝对路径改写为就地文件名，使其能在本机直接运行。

        VMod 的 .mfi 记录的是安装时路径（如 C:\\VMODNT\\Tutorial\\X.LPF），换机后失效；
        本工具重写为同目录文件名，可选复制到 out_dir。
        """
        return vmod.portable_namefile(namefile, out_dir=out_dir or None,
                                      out_name=out_name or None, dry_run=dry_run)

    @mcp.tool()
    def gms_export_hint() -> dict:
        """GMS 工程怎么导出成可被本 MCP 接管的 MODFLOW 文件。"""
        return gms.gms_export_hint()
