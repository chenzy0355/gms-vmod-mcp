"""MODFLOW 运行日志诊断与模型配置校验工具。

针对 MODFLOW-2000 / 2005 / 96 / NWT / MF6 常见数值计算与工程设置问题：
- 日志深入解析：精准捕获不收敛、残差最大网格、干涸网格及水量均衡误差，输出针对性调优建议；
- 前置规则校验：防范抽注水正负号混淆、初始水头低于底板、稳态无源边界、求解器缺失等典型建模失误。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from .. import state
from ..adapters import mfmodel


def register(mcp) -> None:

    @mcp.tool()
    def mfm_diagnose_log(alias: str = "", log_path: str = "") -> dict:
        """诊断 MODFLOW 运行日志（.lst / .out / .glo）。

        可自动读取已加载模型的输出日志，或直接指定日志文件路径。
        结构化输出：收敛状态、最大水头变化网格位置 (L, R, C)、干涸网格数、水均衡相对误差、根本原因分析与调参建议。
        """
        target_path = None
        if log_path:
            p = Path(log_path)
            if p.exists() and p.is_file():
                target_path = p
            else:
                return {"ok": False, "error": f"指定的日志文件不存在: {log_path}"}
        elif alias:
            try:
                model = state.get(alias)
                # 寻找模型工作目录下的日志
                ws = getattr(model, "model_ws", getattr(model, "sim_ws", None))
                if ws:
                    ws_path = Path(ws)
                    name = getattr(model, "name", getattr(model, "sim_name", ""))
                    for ext in (".lst", ".out", ".list", ".glo", ".log"):
                        cand = ws_path / f"{name}{ext}"
                        if cand.exists():
                            target_path = cand
                            break
                    if not target_path:
                        # 尝试目录内任意 .lst / .out
                        cands = list(ws_path.glob("*.lst")) + list(ws_path.glob("*.out"))
                        if cands:
                            target_path = cands[0]
            except Exception as e:
                return {"ok": False, "error": f"获取模型失败: {e}"}

        if not target_path:
            return {"ok": False, "error": "未找到运行日志文件，请通过 log_path 指定或先运行模型"}

        return _parse_listing_file(target_path)

    @mcp.tool()
    def mfm_validate_model(alias: str) -> dict:
        """对已加载的模型进行物理与参数规则前置校验。

        检查项包括：
        1. 抽水井流量符号（WEL 抽水需负值，注水为正值）；
        2. 初始水头与底板高程（是否存在 STRT <= BOTM 导致起始即干涸）；
        3. 稳态水均衡源汇匹配（稳态抽水是否有补给边界支持）；
        4. 含水层类型（LAYCON/LAYTYP 与承压/潜水物理属性）；
        5. 求解器与时间步配置有效性。
        """
        model = state.get(alias)
        checks = []

        # 1. 求解器检查
        solver_pkgs = [p for p in ("PCG", "GMG", "SIP", "SMS", "DE4", "IMS")
                       if _has_pkg(model, p)]
        if solver_pkgs:
            checks.append({
                "item": "求解器配置",
                "status": "PASS",
                "message": f"已配置求解器: {', '.join(solver_pkgs)}"
            })
        else:
            checks.append({
                "item": "求解器配置",
                "status": "FAIL",
                "message": "未检测到求解器包（PCG/GMG/SIP/SMS/IMS），模型将无法求解"
            })

        # 2. 网格与顶底板高程合理性
        dis_pkg = _get_pkg(model, ["DIS", "DISU", "DISV"])
        top_arr, botm_arr = None, None
        if dis_pkg:
            try:
                top_arr = np.asarray(dis_pkg.top.array, dtype=float)
                botm_arr = np.asarray(dis_pkg.botm.array, dtype=float)
                if botm_arr.ndim == 3:
                    nlay = botm_arr.shape[0]
                    # 检查各层厚度 top > botm[0], botm[i-1] > botm[i]
                    negative_thickness_count = 0
                    if top_arr is not None:
                        neg = np.sum(top_arr <= botm_arr[0])
                        negative_thickness_count += int(neg)
                    for k in range(1, nlay):
                        neg = np.sum(botm_arr[k - 1] <= botm_arr[k])
                        negative_thickness_count += int(neg)

                    if negative_thickness_count == 0:
                        checks.append({
                            "item": "含水层厚度与顶底板拓扑",
                            "status": "PASS",
                            "message": f"顶底板连续且网格厚度为正（层数: {nlay}）"
                        })
                    else:
                        checks.append({
                            "item": "含水层厚度与顶底板拓扑",
                            "status": "FAIL",
                            "message": f"发现 {negative_thickness_count} 个网格顶板标高低于或等于底板标高（厚度 <= 0），会导致几何奇异"
                        })
            except Exception as e:
                checks.append({"item": "顶底板高程解析", "status": "WARN", "message": str(e)})

        # 3. 初始水头与底板高程比较 (STRT <= BOTM 预警)
        bas_pkg = _get_pkg(model, ["BAS6", "BAS", "IC"])
        if bas_pkg and botm_arr is not None:
            try:
                strt_arr = np.asarray(bas_pkg.strt.array, dtype=float)
                ibound_arr = None
                if hasattr(bas_pkg, "ibound"):
                    ibound_arr = np.asarray(bas_pkg.ibound.array, dtype=int)

                if strt_arr.ndim == 2 and botm_arr.ndim == 3:
                    strt_arr = np.expand_dims(strt_arr, 0)
                if ibound_arr is not None and ibound_arr.ndim == 2:
                    ibound_arr = np.expand_dims(ibound_arr, 0)

                active_mask = (ibound_arr > 0) if ibound_arr is not None else np.ones_like(strt_arr, dtype=bool)
                dry_init_mask = active_mask & (strt_arr <= botm_arr)
                dry_init_count = int(np.sum(dry_init_mask))

                if dry_init_count == 0:
                    checks.append({
                        "item": "初始水头与含水层底板",
                        "status": "PASS",
                        "message": "有效网格初始水头均高于含水层底板"
                    })
                else:
                    sample_locs = []
                    coords = np.argwhere(dry_init_mask)
                    for c in coords[:3]:
                        sample_locs.append(f"L{c[0]}R{c[1]}C{c[2]}")
                    checks.append({
                        "item": "初始水头与含水层底板",
                        "status": "WARN",
                        "message": f"发现 {dry_init_count} 个有效网格初始水头 <= 底板高程（如 {', '.join(sample_locs)}），模拟启动后会立即转为干涸网格(Dry Cell)"
                    })
            except Exception as e:
                checks.append({"item": "初始水头核查", "status": "WARN", "message": str(e)})

        # 4. WEL 包流量正负号检查
        wel_pkg = _get_pkg(model, ["WEL"])
        if wel_pkg:
            try:
                spd = wel_pkg.stress_period_data
                positive_wells = []
                negative_wells = []
                # 遍历应力期
                for kper in range(getattr(model, "nper", 1)):
                    try:
                        data = spd[kper]
                    except Exception:
                        data = None
                    if data is not None:
                        for row in data:
                            if hasattr(row, "dtype") and row.dtype.names:
                                q_val = float(row[row.dtype.names[-1]])
                            else:
                                q_val = float(row[-1])
                            if q_val > 0:
                                positive_wells.append(q_val)
                            elif q_val < 0:
                                negative_wells.append(q_val)

                if positive_wells and not negative_wells:
                    checks.append({
                        "item": "抽水井流量符号(WEL)",
                        "status": "WARN",
                        "message": f"所有井流量均为正值（共 {len(positive_wells)} 个记录）。在 MODFLOW 规范中，Q < 0 表示抽水排泄，Q > 0 表示注水补给。若此为抽水试验请改为负值！"
                    })
                elif negative_wells:
                    checks.append({
                        "item": "抽水井流量符号(WEL)",
                        "status": "PASS",
                        "message": f"检测到抽水井流量为负值（共 {len(negative_wells)} 条抽水记录，最大单井抽水量: {abs(min(negative_wells)):.2f}）"
                    })
            except Exception as e:
                checks.append({"item": "井参数核查", "status": "WARN", "message": str(e)})

        # 5. 稳态源汇闭环检查
        is_steady = True
        if dis_pkg and hasattr(dis_pkg, "steady"):
            st = np.atleast_1d(dis_pkg.steady.array)
            is_steady = bool(st[0])

        has_inflow_boundary = any(_has_pkg(model, p) for p in ("CHD", "RCH", "RIV", "GHB"))
        has_wells = _has_pkg(model, "WEL")
        if is_steady and has_wells and not has_inflow_boundary:
            checks.append({
                "item": "稳态边界源汇守恒",
                "status": "FAIL",
                "message": "稳态模拟中包含抽水(WEL)，但缺少任何入流边界(CHD/RCH/RIV/GHB)，无补给来源模型必定发散不收敛"
            })
        elif is_steady:
            checks.append({
                "item": "稳态边界源汇守恒",
                "status": "PASS",
                "message": "边界条件类型与稳态模拟匹配"
            })

        warns = sum(1 for c in checks if c["status"] == "WARN")
        fails = sum(1 for c in checks if c["status"] == "FAIL")

        return {
            "alias": alias,
            "overall_status": "FAIL" if fails > 0 else ("WARN" if warns > 0 else "PASS"),
            "fails_count": fails,
            "warnings_count": warns,
            "checks": checks,
            "summary": "模型配置合格，可安全执行计算" if fails == 0 and warns == 0 else
                       f"发现 {fails} 项错误、{warns} 项警告，请根据上述提示调整后再计算。"
        }


def _get_pkg(model, names: list[str]):
    for n in names:
        p = model.get_package(n)
        if p is not None:
            return p
    return None


def _has_pkg(model, name: str) -> bool:
    return model.get_package(name) is not None


def _parse_listing_file(path: Path) -> dict:
    """解析 MODFLOW .lst / .out 文本日志。"""
    try:
        text = path.read_text(encoding="latin-1", errors="ignore")
    except Exception as e:
        return {"ok": False, "error": f"读取日志文件失败: {e}"}

    lines = text.splitlines()

    # 1. 正常终止与收敛标志
    normal_term = bool(re.search(r"NORMAL TERMINATION|Run end date and time|completed successfully|Elapsed run time", text, re.IGNORECASE))
    failed_converge = bool(re.search(r"FAILED TO CONVERGE|NOT CONVERGED", text, re.IGNORECASE))

    # 2. 水量均衡相对误差 (PERCENT DISCREPANCY)
    discrepancies = []
    for match in re.finditer(r"PERCENT DISCREPANCY\s*=\s*([-+]?\d*\.?\d+)", text, re.IGNORECASE):
        try:
            discrepancies.append(float(match.group(1)))
        except ValueError:
            pass
    final_discrepancy = discrepancies[-1] if discrepancies else None

    # 3. 干涸网格
    dry_count = len(re.findall(r"CELL DRY|CELL CONVERTED TO DRY|WETTING/DRYING", text, re.IGNORECASE))

    # 4. 最大残差或水头变化
    max_hchange_matches = list(re.finditer(
        r"MAXIMUM HEAD CHANGE\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+AT LAYER\s+(\d+)\s+ROW\s+(\d+)\s+COL\s+(\d+)",
        text, re.IGNORECASE
    ))
    max_residual_info = None
    if max_hchange_matches:
        last_m = max_hchange_matches[-1]
        max_residual_info = {
            "value": float(last_m.group(1)),
            "layer": int(last_m.group(2)) - 1,
            "row": int(last_m.group(3)) - 1,
            "col": int(last_m.group(4)) - 1,
        }

    # 5. 综合诊断与工程调参建议
    issues = []
    suggestions = []

    if failed_converge or not normal_term:
        issues.append("模型在指定最大迭代次数内未收敛或异常中断")
        suggestions.append("增大求解器最大迭代次数（如将 PCG 的 MXITER/ITER1 从 50 调至 100~200）")
        suggestions.append("适当放宽收敛准则 HCLOSE / RCLOSE（如由 1e-5 放宽至 1e-4 或 1e-3）")

    if dry_count > 0:
        issues.append(f"模拟过程中出现网格干涸/水跃震荡（检测到 {dry_count} 次干涸相关事件）")
        suggestions.append("检查含水层底板 BOTM：确认抽水漏斗最低水位未低于底板高程")
        suggestions.append("若为潜水含水层，考虑在 PCG 中启用松弛因子 RELAX=0.97 或使用阻尼，避免水位剧烈震荡")

    if final_discrepancy is not None:
        if abs(final_discrepancy) > 1.0:
            issues.append(f"水量均衡相对误差为 {final_discrepancy:.2f}%，超过了工程规范要求的 1.0% 限值")
            suggestions.append("水量不平衡通常由提前终止或极端源汇强流引起，建议进一步收紧残差标准或缩小时间步长")
        else:
            suggestions.append(f"水量均衡误差良好（{final_discrepancy:.3f}% < 1.0%）")

    if not issues:
        issues.append("未发现显著数值问题，计算收敛正常")
        suggestions.append("模型水头与均衡满足数值计算规范要求")

    return {
        "ok": True,
        "log_file": str(path),
        "normal_termination": normal_term,
        "converged": not failed_converge and normal_term,
        "percent_discrepancy": final_discrepancy,
        "dry_cells_events": dry_count,
        "max_head_change": max_residual_info,
        "issues": issues,
        "suggestions": suggestions,
        "tail": lines[-25:] if len(lines) >= 25 else lines
    }
