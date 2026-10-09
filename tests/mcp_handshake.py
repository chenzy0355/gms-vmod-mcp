"""按 mcp.json 里的注册方式启动服务，做一次真实 MCP 握手，验证注册可用。

用法：
    python tests/mcp_handshake.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(__file__).resolve().parent.parent
    server_env = dict(os.environ)
    server_env.update({
        "PYTHONPATH": str(root / "src"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    server_env.pop("PYTHONWARNINGS", None)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mfmcp.server"],
        env=server_env
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            print("[PASS] 握手成功")
            print("       server:", getattr(info, "name", "?"),
                  getattr(info, "version", ""))

            # 工具清单有时会因服务端初始化竞态而失败，重试几次
            names: list[str] = []
            last_err: BaseException | None = None
            for attempt in range(4):
                try:
                    tools = await session.list_tools()
                    names = [t.name for t in tools.tools]
                    break
                except BaseException as exc:  # noqa: BLE001
                    last_err = exc
                    await asyncio.sleep(0.5)
            if not names:
                print(f"[FAIL] list_tools 失败：{type(last_err).__name__}: {last_err}")
                _dump(last_err)
                return 1

            print(f"[PASS] 工具数：{len(names)}")
            for n in names:
                print("       -", n)

            res = await session.call_tool("mfm_env", {})
            payload = res.content[0].text if res.content else "{}"
            data = json.loads(payload)
            gms = data["gms"]
            vm = data["visual_modflow"]
            print("[PASS] mfm_env 调用成功")
            print(f"       GMS installed={gms['installed']} engines={len(gms['engines'])}")
            print(f"       VMod installed={vm['installed']} engines={len(vm['engines'])}")
            print(f"       引擎总数={data['engines_total']}")
    return 0


def _dump(exc: BaseException) -> None:
    """把 ExceptionGroup 里的子异常也打出来。"""
    subs = getattr(exc, "exceptions", None)
    if subs:
        for s in subs:
            print(f"        子异常: {type(s).__name__}: {s}")
            _dump(s)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except BaseException as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        subs = getattr(exc, "exceptions", None)
        if subs:
            for s in subs:
                print(f"        子异常: {type(s).__name__}: {s}")
                for s2 in getattr(s, "exceptions", ()) or ():
                    print(f"          孙异常: {type(s2).__name__}: {s2}")
        raise SystemExit(1)
