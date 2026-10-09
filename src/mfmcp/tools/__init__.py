"""按模块注册全部 MCP 工具。新增工具只需在本目录加一个 register(mcp) 模块并登记到 _MODULES。"""

from __future__ import annotations

_MODULES = (
    "discover",
    "vmod_tools",
    "model_tools",
    "run_tools",
    "plot_tools",
    "diagnose_tools",
    "analysis_tools",
    "pumping_test_tools",
)


def register_all(mcp) -> list[str]:
    import importlib
    loaded = []
    for name in _MODULES:
        mod = importlib.import_module(f".{name}", __package__)
        mod.register(mcp)
        loaded.append(name)
    return loaded
