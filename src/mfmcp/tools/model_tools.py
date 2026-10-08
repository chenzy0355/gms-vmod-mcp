"""标准 MODFLOW 模型的读/改工具（FloPy 内核）。"""

from __future__ import annotations

from pathlib import Path

from .. import env, state
from ..adapters import mfmodel


def register(mcp) -> None:

    @mcp.tool()
    def mfm_load(path: str, exe_name: str = "", alias: str = "") -> dict:
        """加载一个 MODFLOW 模型（GMS / VMod 导出的标准文件均可），并缓存到本次会话。

        path: .nam 文件、mfsim.nam，或包含它们的目录。
        alias: 给模型起个名字，后续工具用它引用；省略则用文件名。
        """
        model = mfmodel.load_model(path, exe_name=exe_name or None)
        name = alias or Path(path).stem
        state.put(name, model)
        return {"loaded": name, "path": str(path),
                "summary": mfmodel.model_summary(model),
                "cached_models": state.keys()}

    @mcp.tool()
    def mfm_summary(alias: str = "") -> dict:
        """查看已加载模型的结构：网格、层数、应力期、包含哪些包。"""
        return mfmodel.model_summary(state.get(alias))

    @mcp.tool()
    def mfm_cached() -> dict:
        """列出本次会话已加载并缓存的模型。"""
        return {"models": state.keys(), "last": state.last()}

    @mcp.tool()
    def mfm_drop(alias: str) -> dict:
        """从缓存里移除一个模型。"""
        return {"dropped": alias, "ok": state.drop(alias), "remaining": state.keys()}

    @mcp.tool()
    def mfm_get_array(alias: str, name: str, layer: int = -1, return_values: bool = False) -> dict:
        """读取模型数组（渗透系数、顶底板、初始水头、给水度…）。

        name 支持别名：k/hk, k33/vk, top, botm, strt, ibound, sy, ss。
        layer>=0 时只返回该层；return_values=True 才回传完整数值（大模型慎用）。
        """
        model = state.get(alias)
        out = mfmodel.get_array(model, name, layer=layer if layer >= 0 else None)
        if not return_values:
            out.pop("values", None)
            out["hint"] = "需要完整数组请传 return_values=True"
        return out

    @mcp.tool()
    def mfm_set_array(alias: str, name: str, values=None, layer: int = -1,
                      factor: float | None = None) -> dict:
        """修改模型数组。

        * factor 给定时做整体缩放（例如 factor=0.5 把渗透系数减半，配合 layer 只改某层）
        * 否则用 values 覆盖：标量则全填充，嵌套列表需与目标形状一致
        """
        model = state.get(alias)
        return mfmodel.set_array(model, name, values,
                                 layer=layer if layer >= 0 else None, factor=factor)

    @mcp.tool()
    def mfm_save(alias: str, out_dir: str = "") -> dict:
        """把当前模型（含所有修改）写回磁盘。out_dir 给定时写到新目录，否则写回原目录。"""
        model = state.get(alias)
        return mfmodel.save_model(model, out_dir or None)

    @mcp.tool()
    def mfm_bc_list(alias: str, package: str = "") -> dict:
        """列出边界条件/源汇项（WEL 井、RCH 补给、RIV 河流、CHD 定水头、DRN 排水…）。

        package 留空列出全部；返回每个应力期的记录条数与前几条记录样例。
        """
        return {"boundaries": mfmodel.list_bc(state.get(alias), package or None)}

    @mcp.tool()
    def mfm_bc_edit(alias: str, package: str, period: int = 0, add: list | None = None,
                    remove_index: list[int] | None = None,
                    scale_value: float | None = None, value_column: int = -1) -> dict:
        """增删改边界条件。

        * add: 新记录，如 [[层, 行, 列, 流量], ...]
        * remove_index: 删掉该应力期第 N 条记录（0 基）
        * scale_value: 把数值列整体乘以系数（例如把所有抽水量放大 1.5 倍）
        """
        return mfmodel.edit_bc(state.get(alias), package, period=period, add=add,
                               remove_index=remove_index, scale_value=scale_value,
                               value_column=value_column)
