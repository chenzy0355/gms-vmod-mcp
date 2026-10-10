"""MODFLOW MCP Server —— 让 AI 接入并调控 GMS(10.4)、Visual MODFLOW(4.0) 与 Grapher 16。

设计原则（来自对三套软件的实际勘察）：

* 两家建模软件都是 MODFLOW 的**前后处理器**，导出的模型是标准 MODFLOW 文件；
  因此以 FloPy 为内核，即可对两家模型做参数读写、运行、结果分析与出图。
* Visual MODFLOW 的 ``.vmf`` 是完整 XML，可直接解析改写（网格/包/引擎/层参数）。
* GMS 的 ``.gpr`` 是私有格式，但它的 ``models/`` 下有一整套 USGS 引擎 exe，
  可命令行直接驱动；``xms_api`` 仅能在 GMS 运行时调用。
* Grapher 不参与建模与求解，只做**后处理绘图**：由 ``Scripter.exe`` 执行
  ``.bas`` 脚本，产出 ``.grf`` 工程与 ``.png`` 图件。
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
本地 MODFLOW 建模与水文地质分析助手，可读写、运行、诊断、校验与分析 GMS 与 Visual MODFLOW 的模型，
并可驱动 Golden Software Grapher 出原生图件。

典型流程：
1. mfm_env / mfm_scan            —— 环境与模型文件探测
2. vmod_info / vmod_search_params —— 读 Visual MODFLOW 工程（.vmf）
3. mfm_load / mfm_summary        —— 加载标准 MODFLOW 模型
4. mfm_validate_model            —— 模型物理规则与边界前置体检（防抽注水正负号混淆、初始水头低于底板等）
5. mfm_set_array / mfm_bc_edit   —— 修改水文地质参数或边界条件
6. mfm_run                       —— 驱动 MODFLOW 计算引擎（支持 GMS 与 Visual MODFLOW 原厂引擎）
7. mfm_diagnose_log              —— 深入诊断运行日志（收敛性、最大残差网格、干涸网格、水均衡相对误差与调优建议）
8. mfm_heads / mfm_drawdown / mfm_budget —— 读取水头场、降深与水量均衡
9. mfm_theis_benchmark           —— 泰斯（Theis）解析解理论降深与数值解对照出图与误差评估
10. mfm_sensitivity_analysis     —— 关键水文地质参数（K、S、Q）敏感性批处理计算与响应曲线
11. mfm_check_project            —— 工程目录规范性与包依赖完整性体检
12. mfm_plot_map / mfm_plot_timeseries / mfm_plot_compare —— 结果可视化出图
13. mfm_theis_type_curve_fit     —— 抽水试验 Theis 双对数配线法自动优化拟合求参（T、S）
14. mfm_jacob_straight_line_fit  —— Cooper-Jacob 半对数直线图解法自动拟合求参（Δs、t0、T、S）
15. mfm_generate_grapher_script  —— 生成 Golden Software Grapher 16 自动化脚本（.BAS）
16. mfm_run_grapher_script       —— 调用本机 Grapher Scripter 执行脚本，导出 .grf 工程与 .png 图件

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
        print(json.dumps({"gms": env.gms_info(), "vmod": env.vmod_info(),
                          "grapher": env.grapher_info()},
                         indent=2, ensure_ascii=False))
        return
    mcp.run()


if __name__ == "__main__":
    main()
