"""自检脚本：不依赖网络，验证适配器能在真实文件上工作。

用法（在 modflow-mcp 目录下）：
    set PYTHONPATH=src
    python tests/smoke.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfmcp import env  # noqa: E402
from mfmcp.adapters import gms, vmod  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{PASS if cond else FAIL} {label}" + (f"  -> {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    print("=" * 68)
    print("modflow-mcp 自检")
    print("=" * 68)

    # 1) 环境
    gi = env.gms_info()
    vi = env.vmod_info()
    check("GMS 安装可识别", gi["installed"], gi["home"])
    check("Visual MODFLOW 安装可识别", vi["installed"], vi["home"])

    # 2) 引擎
    gms_eng = env.list_gms_engines()
    vmod_eng = env.list_vmod_engines()
    check("GMS 引擎扫描到", len(gms_eng) > 0, f"{len(gms_eng)} 个: "
          + ", ".join(sorted({e.key for e in gms_eng})))
    check("VMod 引擎扫描到", len(vmod_eng) > 0, f"{len(vmod_eng)} 个: "
          + ", ".join(e.key for e in vmod_eng))
    check("引擎 exe 均真实存在", all(e.exists() for e in gms_eng + vmod_eng))

    # 3) 默认引擎解析
    flow = env.resolve_engine(kind="flow")
    check("能解析出默认水流引擎", flow is not None,
          flow.name if flow else "无")

    # 4) 真实 VMod 工程解析
    tut = Path(vi["home"]) / "Tutorial"
    vmfs = sorted(tut.glob("*.vmf")) if tut.is_dir() else []
    check("找到 Tutorial 示例工程", len(vmfs) > 0, ", ".join(p.name for p in vmfs))
    if vmfs:
        info = vmod.project_info(vmfs[0])
        g = info["grid"]
        check("VMod 网格解析正确", g["layers"] > 0 and g["rows"] > 0 and g["cols"] > 0,
              f"{g['layers']}层 x {g['rows']}行 x {g['cols']}列")
        check("VMod 引擎识别", bool(info["engines"]),
              json.dumps(info["engines"], ensure_ascii=False))

        params = list(vmod.iter_params(vmfs[0]))
        check("VMod 参数可枚举", len(params) > 0, f"{len(params)} 个")
        writable = [p for p in params if p["writable"]]
        check("存在可写参数", len(writable) > 0, f"{len(writable)} 个可写")
        check("参数带唯一路径", all(p["path"].count("/") >= 2 for p in params[:20]),
              params[0]["path"][:70] if params else "")

        hits = vmod.search_params(vmfs[0], "k", limit=5)
        check("参数模糊检索可用", isinstance(hits, list),
              f"'k' 命中 {len(hits)} 条")

        # 5) 干跑改参数（dry_run=True，不落盘）
        if writable:
            tgt = writable[0]
            r = vmod.set_params(vmfs[0], [{"path": tgt["path"], "name": tgt["name"],
                                           "value": tgt["value"]}], dry_run=True)
            check("改参数(dry_run) 成功", r["changed"] and not r["failed"],
                  f"{tgt['name']} = {tgt['value']}")

    # 6) MODFLOW 文件发现
    found = vmod.find_modflow_files(Path(vi["home"]) / "Tutorial")
    check("Tutorial 里发现 MODFLOW 文件", isinstance(found, list),
          f"{len(found)} 组")

    # 7) GMS 导出提示 & xms 探测（不强制可用）
    hint = gms.gms_export_hint()
    check("GMS 导出提示可用", "how" in hint)
    xms = gms.xms_api_status()
    print(f"     [info] xms_api 状态: available={xms['available']} "
          f"({xms.get('reason', '')})")

    print("-" * 68)
    if _failures:
        print(f"结果：{len(_failures)} 项失败 -> {_failures}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
