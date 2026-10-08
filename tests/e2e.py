"""端到端验证：用 GMS 与 Visual MODFLOW 两家的引擎，各跑一个真实 MODFLOW 模型。

流程（全部走 MCP 内部同一套代码路径）：
  1. FloPy 建一个带 WEL/RCH 的小模型，写成 MODFLOW-2005 与 MODFLOW-2000 两套输入
  2. 分别用 GMS 的 mf2005.exe、VMod 的 Mf2k.exe 运行
  3. 读水头 / 算降深
  4. 出水头等值线图

用法：
    set PYTHONPATH=src
    python tests/e2e.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfmcp import env  # noqa: E402
from mfmcp.adapters import mfmodel  # noqa: E402


def build_case(version: str, ws: Path):
    """建一个 1 层 15x15 承压模型：左边界定水头补给，中间一口抽水井。

    物理上一定收敛：T = K*b = 10*50 = 500 m2/d，定水头边界供水，
    抽水 1000 m3/d 在 T=500 的含水层里降深约几米。
    """
    import numpy as np
    import flopy

    nlay, nrow, ncol = 1, 15, 15
    ws.mkdir(parents=True, exist_ok=True)
    m = flopy.modflow.Modflow("demo", version=version, model_ws=str(ws),
                              exe_name="mf2005")
    dis = flopy.modflow.ModflowDis(
        m, nlay=nlay, nrow=nrow, ncol=ncol, delr=100.0, delc=100.0,
        top=50.0, botm=0.0, nper=1, perlen=1.0, steady=True)
    ibound = np.ones((nlay, nrow, ncol), dtype=int)
    ibound[:, :, 0] = -1                      # 左边界定水头（供水边界）
    bas = flopy.modflow.ModflowBas(m, ibound=ibound, strt=30.0)
    lpf = flopy.modflow.ModflowLpf(m, hk=10.0, laytyp=0)   # 承压
    well = flopy.modflow.ModflowWel(m, stress_period_data={0: [[0, 7, 7, -1000.0]]})
    chd = flopy.modflow.ModflowChd(
        m, stress_period_data={0: [[0, r, 0, 30.0, 30.0] for r in range(nrow)]})
    oc = flopy.modflow.ModflowOc(
        m, stress_period_data={(0, 0): ["save head", "save budget"]})
    pcg = flopy.modflow.ModflowPcg(m, mxiter=200, iter1=100, hclose=1e-5,
                                   rclose=1e-5, relax=1.0)
    m.write_input()
    return m, {"nlay": nlay, "nrow": nrow, "ncol": ncol,
               "pumping": 1000.0, "hk": 10.0, "strt": 30.0}


def main() -> int:
    ws_root = env.workspace() / "e2e"
    ok_all = True

    cases = [
        ("mf2005", "MODFLOW-2005", "gms", "mf2005", "FloPy 生成 -> GMS 的 usgs/mf2005.exe"),
        ("mf2k",   "MODFLOW-2000", "vmod", "mf2000", "FloPy 生成 -> Visual MODFLOW 的 Mf2k.exe"),
    ]

    for version, label, vendor, ekey, desc in cases:
        print("=" * 70)
        print(f"### {label}  ({desc})")
        ws = ws_root / version
        try:
            model, meta = build_case(version, ws)
        except Exception as exc:
            print(f"  [FAIL] 建模失败：{exc}")
            ok_all = False
            continue
        eng = env.find_engine(ekey, vendor) or env.resolve_engine(kind="flow", vendor=vendor)
        print(f"  [info] 模型：{meta['nlay']}层 {meta['nrow']}x{meta['ncol']}，"
              f"抽水 {meta['pumping']} m3/d，K={meta['hk']} m/d")
        print(f"  [info] 引擎：{eng.name if eng else '无'}  ({eng.path if eng else '-'})")
        if eng is None:
            print("  [FAIL] 找不到该厂商引擎")
            ok_all = False
            continue

        try:
            res = mfmodel.run_model(model, engine_key=eng.key, vendor=vendor, timeout=300)
        except Exception as exc:
            print(f"  [FAIL] 运行异常：{exc}")
            ok_all = False
            continue

        good = res["success"] or res["normal_termination"]
        print(f"  {'[PASS]' if good else '[FAIL]'} 运行结束：success={res['success']} "
              f"normal_termination={res['normal_termination']} "
              f"converged={res.get('converged')} mode={res.get('mode')}")
        if not good:
            print("  ---- 日志尾部 ----")
            print("\n".join(res["log_tail"].splitlines()[-12:]))
            ok_all = False
            continue

        heads = mfmodel.read_heads(model, layer=0, kper=-1)["result"][0]
        dd = mfmodel.drawdown(model, kper=-1)["result"][0]
        print(f"  [PASS] 水头：min={heads['min']:.2f} max={heads['max']:.2f} "
              f"mean={heads['mean']:.2f}")
        print(f"  [PASS] 井心降深：{dd['max_drawdown']:.2f} m（初始 30.0 m）")

        # 出图
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            hf = heads["values"]
            fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), dpi=140)
            im0 = axes[0].imshow(hf, cmap="viridis")
            axes[0].set_title(f"{label} heads")
            fig.colorbar(im0, ax=axes[0], shrink=0.85)
            im1 = axes[1].imshow(dd["values"][0] if isinstance(dd["values"][0], list)
                                 else dd["values"], cmap="Reds")
            axes[1].set_title(f"{label} drawdown")
            fig.colorbar(im1, ax=axes[1], shrink=0.85)
            fig.tight_layout()
            figdir = env.workspace() / "figs"
            figdir.mkdir(parents=True, exist_ok=True)
            p = figdir / f"e2e-{version}.png"
            fig.savefig(p)
            plt.close(fig)
            print(f"  [PASS] 出图：{p}")
        except Exception as exc:
            print(f"  [warn] 出图失败：{exc}")

    print("=" * 70)
    print("端到端结果：" + ("全部通过" if ok_all else "存在失败项"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
