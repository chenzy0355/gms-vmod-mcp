"""MODFLOW MCP Server —— 让 AI 接入并调控 GMS(10.4) 与 Visual MODFLOW(4.0)。

设计原则（来自对两套软件的实际勘察）：

* 两家都是 MODFLOW 的**前后处理器**，导出的模型是标准 MODFLOW 文件；
  因此以 FloPy 为内核，即可对两家模型做参数读写、运行、结果分析与出图。
* Visual MODFLOW 的 ``.vmf`` 是完整 XML，可直接解析改写（网格/包/引擎/层参数）。
* GMS 的 ``.gpr`` 是私有格式，但它的 ``models/`` 下有一整套 USGS 引擎 exe，
  可命令行直接驱动；``xms_api`` 仅能在 GMS 运行时调用。
"""

from __future__ import annotations

import sys
import warnings

# 屏蔽第三方库的弃用告警噪声（matplotlib/flopy/pyparsing 会往 stderr 刷很多）。
# 注意：不要用环境变量 PYTHONWARNINGS=ignore —— 实测会让本服务在处理
# tools/list 时卡死；在代码里按类别/模块过滤是安全的。
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
warnings.filterwarnings("ignore", module=r"pyparsing.*")
warnings.filterwarnings("ignore", module=r"matplotlib.*")

import logging  # noqa: E402

for _lg in ("matplotlib", "matplotlib.font_manager", "matplotlib._fontconfig_pattern"):
    logging.getLogger(_lg).setLevel(logging.ERROR)

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):  # mcp 2.x：FastMCP 已改名 MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP

from . import env, state
from .tools import register_all

INSTRUCTIONS = """\
本地 MODFLOW 建模助手，可读写、运行、分析 GMS 与 Visual MODFLOW 的模型。

典型流程：
1. mfm_env / mfm_scan            —— 先摸清环境与手上有哪些模型
2. vmod_info / vmod_search_params —— 读 Visual MODFLOW 工程（.vmf）
3. mfm_load / mfm_summary        —— 加载标准 MODFLOW 模型
4. mfm_set_array / mfm_bc_edit / vmod_set_params —— 改参数
5. mfm_run                       —— 跑 MODFLOW（可用任一家的引擎）
6. mfm_heads / mfm_drawdown / mfm_budget —— 读结果
7. mfm_plot_map / mfm_plot_timeseries / mfm_plot_compare —— 出图

注意：GMS 工程（.gpr）需先在 GUI 里导出为 MODFLOW 文本文件才能被读取，见 gms_export_hint。
"""

mcp = FastMCP("modflow", instructions=INSTRUCTIONS)
_LOADED = register_all(mcp)


def _list_tool_names() -> list[str]:
    """兼容 mcp 1.x/2.x 地取工具名列表。"""
    import asyncio
    import inspect

    try:
        res = mcp.list_tools()
    except Exception:
        return []
    if inspect.isawaitable(res):
        res = asyncio.run(res)
    names = []
    for t in res:
        names.append(getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else str(t)))
    return [n for n in names if n]


def main() -> None:
    if "--check" in sys.argv:
        import json
        tools = _list_tool_names()
        print(json.dumps({
            "server": "modflow",
            "tool_count": len(tools),
            "tools": tools,
            "modules": _LOADED,
            "config": str(env.CONFIG_PATH),
        }, indent=2, ensure_ascii=False))
        return
    if "--info" in sys.argv:
        import json
        print(json.dumps({"gms": env.gms_info(), "vmod": env.vmod_info()},
                         indent=2, ensure_ascii=False))
        return
    mcp.run()


if __name__ == "__main__":
    main()
