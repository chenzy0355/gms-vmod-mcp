"""测试新增水文地质分析与诊断工具集。

验证项：
1. mfm_validate_model 前置规则校验（正常与异常参数检出）；
2. mfm_diagnose_log 运行日志深度诊断；
3. mfm_theis_benchmark 泰斯公式解析解与数值解自动比对；
4. mfm_sensitivity_analysis 渗透系数 K 敏感性批处理计算；
5. mfm_check_project 工程目录结构与文件规范性体检。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import flopy

from mfmcp import env, state
from mfmcp.adapters import mfmodel
from mfmcp.tools import diagnose_tools, analysis_tools


def build_test_model(ws: Path, well_q: float = -1000.0, strt_val: float = 30.0, top_val: float = 50.0):
    ws.mkdir(parents=True, exist_ok=True)
    m = flopy.modflow.Modflow("test_demo", version="mf2005", model_ws=str(ws), exe_name="mf2005")
    dis = flopy.modflow.ModflowDis(
        m, nlay=1, nrow=15, ncol=15, delr=100.0, delc=100.0,
        top=top_val, botm=0.0, nper=1, perlen=1.0, steady=True
    )
    ibound = np.ones((1, 15, 15), dtype=int)
    ibound[:, :, 0] = -1
    bas = flopy.modflow.ModflowBas(m, ibound=ibound, strt=strt_val)
    lpf = flopy.modflow.ModflowLpf(m, hk=10.0, laytyp=0)
    wel = flopy.modflow.ModflowWel(m, stress_period_data={0: [[0, 7, 7, well_q]]})
    chd = flopy.modflow.ModflowChd(m, stress_period_data={0: [[0, r, 0, strt_val, strt_val] for r in range(15)]})
    oc = flopy.modflow.ModflowOc(m, stress_period_data={(0, 0): ["save head", "save budget"]})
    pcg = flopy.modflow.ModflowPcg(m, mxiter=100, iter1=50, hclose=1e-5, rclose=1e-5)
    m.write_input()
    return m


def main():
    print("=" * 70)
    print("开始运行新增水文地质诊断与分析工具集测试")
    print("=" * 70)

    ws_root = env.workspace() / "test_tools"
    ws_root.mkdir(parents=True, exist_ok=True)

    # 1. 测试模型前置校验 (mfm_validate_model)
    normal_ws = ws_root / "normal_case"
    m_normal = build_test_model(normal_ws, well_q=-1000.0, strt_val=30.0)
    state.put("normal_case", m_normal)

    # 注册一个 mock mcp 用于测试内部函数
    class MockMCP:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

    mcp = MockMCP()
    diagnose_tools.register(mcp)
    analysis_tools.register(mcp)

    v_res = mcp.tools["mfm_validate_model"](alias="normal_case")
    assert v_res["overall_status"] == "PASS", f"Normal case validation failed: {v_res}"
    print("[PASS] 正常模型物理规则校验通过 (status = PASS)")

    # 异常测试：抽水井正值 + STRT <= BOTM
    flawed_ws = ws_root / "flawed_case"
    m_flawed = build_test_model(flawed_ws, well_q=500.0, strt_val=0.0)  # Q>0 且 STRT=0 <= BOTM=0
    state.put("flawed_case", m_flawed)
    v_flawed = mcp.tools["mfm_validate_model"](alias="flawed_case")
    assert v_flawed["warnings_count"] >= 1, "Failed to detect warnings in flawed case"
    print(f"[PASS] 异常参数成功拦截预警 (检出 warnings: {v_flawed['warnings_count']})")

    # 2. 运行正常算例并进行日志诊断与泰斯解析解比对
    eng = env.find_engine("mf2005") or env.find_engine("mf2k")
    assert eng is not None, "未找到可用计算引擎"
    print(f"使用引擎: {eng.name} ({eng.path})")

    run_res = mfmodel.run_model(m_normal, engine_key=eng.key)
    assert run_res.get("success"), f"模型运行失败: {run_res}"
    print("[PASS] 基础模型运行成功")

    # 3. 测试日志深度诊断 (mfm_diagnose_log)
    diag_res = mcp.tools["mfm_diagnose_log"](alias="normal_case")
    assert diag_res.get("ok"), f"日志诊断失败: {diag_res}"
    assert diag_res.get("converged"), "日志识别未收敛"
    print(f"[PASS] 日志深度诊断通过 (收敛: {diag_res['converged']}, 均衡误差: {diag_res.get('percent_discrepancy')}%)")

    # 4. 测试 Theis 泰斯解析解比对 (mfm_theis_benchmark)
    theis_res = mcp.tools["mfm_theis_benchmark"](alias="normal_case", t_days=1.0, plot=True)
    assert theis_res.get("ok"), f"Theis 对比失败: {theis_res}"
    metrics = theis_res["metrics"]
    print(f"[PASS] Theis 理论解对比完成: MAE = {metrics['mae_m']} m, RMSE = {metrics['rmse_m']} m")
    assert theis_res.get("figure") and Path(theis_res["figure"]).exists(), "Theis 对比图未生成"
    print(f"[PASS] Theis 降深漏斗对比图已生成: {theis_res['figure']}")

    # 5. 测试参数敏感性批处理分析 (mfm_sensitivity_analysis)
    sens_res = mcp.tools["mfm_sensitivity_analysis"](
        alias="normal_case", param_name="k",
        scale_factors=[0.8, 1.0, 1.2],
        engine=eng.key
    )
    assert sens_res.get("ok"), f"敏感性分析失败: {sens_res}"
    print(f"[PASS] 参数敏感性批量计算完成 ({len(sens_res['runs'])} 组扰动算例)")
    assert sens_res.get("figure") and Path(sens_res["figure"]).exists(), "敏感性响应曲线图未生成"
    print(f"[PASS] 敏感性曲线图已生成: {sens_res['figure']}")

    # 6. 测试工程体检工具 (mfm_check_project)
    chk_res = mcp.tools["mfm_check_project"](project_path=str(normal_ws), run_test=True)
    assert chk_res.get("ok"), f"工程体检失败: {chk_res}"
    print(f"[PASS] 工程规范性体检通过 (综合得分: {chk_res['score']}, 评级: {chk_res['rating']})")

    print("=" * 70)
    print("全部 5 项新增水文地质专业工具测试均 100% 通过！")
    print("=" * 70)


if __name__ == "__main__":
    main()
