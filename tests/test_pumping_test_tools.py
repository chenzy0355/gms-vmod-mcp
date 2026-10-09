"""测试抽水试验求参与 Grapher 原生出图工具集。

测试数据使用解析解生成的标准基准算例：
- Theis 双对数配线法基准算例（T = 180 m²/d, S = 2.0e-4）
- Cooper-Jacob 半对数直线图解法基准算例（T = 180 m²/d, S = 2.0e-4）
"""

from __future__ import annotations

import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfmcp import env
from mfmcp.tools import pumping_test_tools
from mfmcp.tools.pumping_test_tools import _well_function


def _create_synthetic_test_datasets(test_dir: Path) -> tuple[Path, Path]:
    """生成标准抽水试验测试基准数据。"""
    test_dir.mkdir(parents=True, exist_ok=True)

    # 理论水文地质参数
    t_true = 180.0        # m2/d
    s_true = 2.0e-4       # 无量纲
    q_m3_d = 1200.0       # m3/d
    r_obs = 100.0         # m

    # 1. Theis 算例数据 (t/r^2 vs s)
    # 时间从 2 min 到 1440 min
    t_min_theis = np.logspace(np.log10(2.0), np.log10(1440.0), 16)
    t_day_theis = t_min_theis / 1440.0
    u_theis = (r_obs**2 * s_true) / (4.0 * t_true * t_day_theis)
    w_theis = _well_function(u_theis)
    s_theis = (q_m3_d / (4.0 * np.pi * t_true)) * w_theis
    tr2_vals = t_min_theis / (r_obs**2)

    theis_file = test_dir / "synthetic_theis_benchmark.dat"
    with open(theis_file, "w", encoding="ascii") as fp:
        fp.write("t/r2\ts\n")
        for x_val, s_val in zip(tr2_vals, s_theis):
            fp.write(f"{x_val:.6e}\t{s_val:.4f}\n")

    # 2. Cooper-Jacob 算例数据 (t vs s)
    # 选取满足小 u 条件的数据区间（t 从 10 min 到 1440 min）
    t_min_jacob = np.logspace(np.log10(10.0), np.log10(1440.0), 16)
    t_day_jacob = t_min_jacob / 1440.0
    u_jacob = (r_obs**2 * s_true) / (4.0 * t_true * t_day_jacob)
    w_jacob = _well_function(u_jacob)
    s_jacob = (q_m3_d / (4.0 * np.pi * t_true)) * w_jacob

    jacob_file = test_dir / "synthetic_jacob_benchmark.dat"
    with open(jacob_file, "w", encoding="ascii") as fp:
        fp.write("t\ts\n")
        for t_val, s_val in zip(t_min_jacob, s_jacob):
            fp.write(f"{t_val:.4f}\t{s_val:.4f}\n")

    return theis_file, jacob_file


def main():
    print("=" * 70)
    print("测试抽水试验参数求解与 Grapher 自动化工具集")
    print("=" * 70)

    class MockMCP:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

    mcp = MockMCP()
    pumping_test_tools.register(mcp)

    test_dir = env.workspace() / "test_data"
    theis_file, jacob_file = _create_synthetic_test_datasets(test_dir)
    assert theis_file.exists(), f"测试数据未生成: {theis_file}"
    assert jacob_file.exists(), f"测试数据未生成: {jacob_file}"

    # 1. 测试 Theis 配线法自动拟合
    theis_res = mcp.tools["mfm_theis_type_curve_fit"](
        data_file=str(theis_file),
        Q=1200.0,
        q_unit="m3/d",
        time_unit="min",
        plot=True,
        generate_grapher_script=True,
        render_grapher=True
    )
    assert theis_res["ok"], f"Theis 配线失败: {theis_res}"
    print("[PASS] Theis 配线法拟合计算成功:")
    print(f"       水文地质参数: T = {theis_res['parameters']['T_m2_d']} m2/d, S = {theis_res['parameters']['S']}")
    print(f"       拟合决定系数: R2 = {theis_res['fitting_quality']['r2']}")
    assert theis_res["fitting_quality"]["r2"] > 0.95, "拟合优度不足"

    gn = theis_res.get("grapher_native", {})
    if gn.get("rendered"):
        assert Path(gn["grf_path"]).exists(), f"Grapher .grf 不存在: {gn['grf_path']}"
        assert Path(gn["png_path"]).exists(), f"Grapher .png 不存在: {gn['png_path']}"
        print(f"[PASS] Grapher 原生工程文件已生成: {gn['grf_path']}")
        print(f"[PASS] Grapher 原生导出图像已生成: {gn['png_path']}")

    # 2. 测试 Cooper-Jacob 半对数直线图解法
    jacob_res = mcp.tools["mfm_jacob_straight_line_fit"](
        data_file=str(jacob_file),
        r=100.0,
        Q=1200.0,
        q_unit="m3/d",
        time_unit="min",
        plot=True,
        generate_grapher_script=True,
        render_grapher=True
    )
    assert jacob_res["ok"], f"Cooper-Jacob 拟合失败: {jacob_res}"
    print("[PASS] Cooper-Jacob 半对数直线图解法拟合成功:")
    print(f"       斜率: Δs = {jacob_res['straight_line']['delta_s_m']} m, 截距 t0 = {jacob_res['straight_line']['t0_min']} min")
    print(f"       水文地质参数: T = {jacob_res['parameters']['T_m2_d']} m2/d, S = {jacob_res['parameters']['S']}")
    print(f"       小 u 适用性校验: u_end = {jacob_res['validity_check']['u_end']} ({jacob_res['validity_check']['note']})")
    assert jacob_res["validity_check"]["u_condition_satisfied"], "小 u 条件未满足"

    j_gn = jacob_res.get("grapher_native", {})
    if j_gn.get("rendered"):
        assert Path(j_gn["grf_path"]).exists(), f"Grapher .grf 不存在: {j_gn['grf_path']}"
        assert Path(j_gn["png_path"]).exists(), f"Grapher .png 不存在: {j_gn['png_path']}"
        print(f"[PASS] Grapher Jacob 原生工程已生成: {j_gn['grf_path']}")
        print(f"[PASS] Grapher Jacob 原生图像已生成: {j_gn['png_path']}")

    # 3. 测试独立的通用脚本生成工具
    script_res = mcp.tools["mfm_generate_grapher_script"](
        script_type="theis_loglog",
        data_file=str(theis_file),
        title="Benchmark Test Plot",
        render_now=True
    )
    assert script_res["ok"], f"生成脚本失败: {script_res}"
    print(f"[PASS] 通用 Grapher 脚本与出图成功: {script_res['script_file']}")

    print("=" * 70)
    print("抽水试验与 Grapher 自动化工具集测试全部通过")
    print("=" * 70)


if __name__ == "__main__":
    main()
