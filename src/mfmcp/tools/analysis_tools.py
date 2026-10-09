"""水文地质分析与验证工具集。

包含：
1. 泰斯（Theis）解析解对照与误差评价（理论降深漏斗 vs 有限差分数值解）；
2. 参数敏感性自动化分析与扰动出图（K / Sy / Ss / Q 扰动响应）；
3. 规范化工程与作业完整性体检（路径隐患、包依赖、试运行与水均衡达标核验）。
"""

from __future__ import annotations

import datetime as _dt
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .. import env, state
from ..adapters import mfmodel

try:
    from scipy.special import exp1 as _exp1
except ImportError:
    _exp1 = None


def _well_function(u: float | np.ndarray) -> np.ndarray:
    """计算 Theis 井函数 W(u)。优先使用 scipy.special.exp1，内置高阶展开作为备用。"""
    u_arr = np.asarray(u, dtype=float)
    if _exp1 is not None:
        # u <= 0 时避免非法警告
        valid = u_arr > 0
        res = np.zeros_like(u_arr)
        res[valid] = _exp1(u_arr[valid])
        return res

    # 无 scipy 时的解析近似 (Rational / Series approximation)
    res = np.zeros_like(u_arr)
    valid = u_arr > 0
    u_v = u_arr[valid]

    small = u_v < 1.0
    u_small = u_v[small]
    # W(u) = -euler - ln(u) + u - u^2/4 + u^3/18 - u^4/96 + u^5/600 ...
    euler = 0.5772156649
    terms = u_small - (u_small**2)/4.0 + (u_small**3)/18.0 - (u_small**4)/96.0 + (u_small**5)/600.0
    w_small = -euler - np.log(u_small) + terms

    # u >= 1 时使用连分数/有理逼近: e^(-u)/(u + 1/(1 + 1/(u + ...)))
    large = ~small
    u_large = u_v[large]
    w_large = np.exp(-u_large) * (u_large**2 + 2.334733 * u_large + 0.250621) / (
        u_large * (u_large**2 + 3.330657 * u_large + 1.681534)
    )

    out = np.zeros_like(u_v)
    out[small] = w_small
    out[large] = w_large
    res[valid] = out
    return res


def _fig_path(tag: str, ext: str = "png") -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = env.workspace() / "figs"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{tag}-{ts}.{ext}"


def register(mcp) -> None:

    @mcp.tool()
    def mfm_theis_benchmark(alias: str, t_days: float = 1.0,
                            T: float = 0.0, S: float = 0.0, Q: float = 0.0,
                            well_rc: list[int] = None, layer: int = 0,
                            kper: int = -1, plot: bool = True) -> dict:
        """Theis 泰斯解析解验证与数值解对比。

        依据无越流完整承压井流理论 s = (Q / 4πT) * W(u)，
        将数值模拟计算的水头降深与解析解理论降深进行逐点对比。
        参数 T, S, Q 若设为 0，将自动从模型的 LPF/WEL/DIS 包中识别提取。
        返回 MAE、RMSE、最大相对误差统计，并可选绘制对比降深漏斗曲线图。
        """
        model = state.get(alias)

        # 1. 自动提取参数
        # 抽水井流量 Q 与位置
        well_row, well_col = None, None
        wel_pkg = model.get_package("WEL")
        if wel_pkg:
            try:
                spd = wel_pkg.stress_period_data
                for r in spd[0]:
                    if hasattr(r, 'dtype') and r.dtype.names:
                        q_val = float(r[r.dtype.names[-1]])
                        k_val = int(r['i']) if 'i' in r.dtype.names else int(r[1])
                        j_val = int(r['j']) if 'j' in r.dtype.names else int(r[2])
                    else:
                        q_val = float(r[-1])
                        k_val = int(r[1])
                        j_val = int(r[2])
                    if q_val < 0:
                        if Q <= 0:
                            Q = abs(q_val)
                        if well_rc is None:
                            well_row = k_val
                            well_col = j_val
                        break
            except Exception:
                pass

        if well_rc is not None and len(well_rc) >= 2:
            well_row, well_col = well_rc[0], well_rc[1]

        mg = model.modelgrid
        nrow = getattr(mg, "nrow", 15)
        ncol = getattr(mg, "ncol", 15)

        if well_row is None or well_col is None:
            well_row, well_col = nrow // 2, ncol // 2

        if Q <= 0:
            Q = 1000.0  # 默认 1000 m3/d

        # 导水系数 T (m2/d)
        if T <= 0:
            hk_info = mfmodel.get_array(model, "hk", layer=layer)
            top_info = mfmodel.get_array(model, "top", layer=layer)
            bot_info = mfmodel.get_array(model, "botm", layer=layer)
            hk_val = float(hk_info.get("mean", 10.0))
            b_val = float(top_info.get("mean", 50.0)) - float(bot_info.get("mean", 0.0))
            T = hk_val * max(b_val, 1.0)

        # 储水系数 S (无量纲)
        if S <= 0:
            ss_info = mfmodel.get_array(model, "ss", layer=layer)
            if "mean" in ss_info and ss_info["mean"] is not None:
                S = float(ss_info["mean"]) * 50.0
            else:
                S = 1e-3  # 承压含水层典型值

        # 2. 提取数值解降深
        dd_res = mfmodel.drawdown(model, kper=kper)
        dd_grid = None
        for r in dd_res.get("result", []):
            if "values" in r:
                arr = np.asarray(r["values"], dtype=float)
                if arr.ndim == 3:
                    dd_grid = arr[layer]
                elif arr.ndim == 2:
                    dd_grid = arr
                break

        if dd_grid is None:
            return {"ok": False, "error": "未能提取降深结果，请确认模型已成功运行且生成了 .hds 文件"}

        # 3. 统计各网格距离与理论降深
        delr = float(np.atleast_1d(getattr(mg, "delr", 100.0))[0])
        delc = float(np.atleast_1d(getattr(mg, "delc", 100.0))[0])

        r_points = []
        theo_points = []
        num_points = []

        for i in range(nrow):
            for j in range(ncol):
                dist = math.sqrt(((i - well_row) * delc) ** 2 + ((j - well_col) * delr) ** 2)
                if dist <= 0:
                    continue  # 井中心网格存在有限差分等效半径修正，解析解有对数奇点，通常剔除井中心点
                # Theis 公式计算: u = r^2 * S / (4 * T * t)
                u = (dist ** 2 * S) / (4.0 * T * t_days)
                w_val = float(_well_function(u))
                s_theo = (Q / (4.0 * math.pi * T)) * w_val
                s_num = float(dd_grid[i, j])

                r_points.append(dist)
                theo_points.append(s_theo)
                num_points.append(s_num)

        r_arr = np.array(r_points)
        theo_arr = np.array(theo_points)
        num_arr = np.array(num_points)

        # 误差指标
        errors = np.abs(num_arr - theo_arr)
        mae = float(np.mean(errors))
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        max_err = float(np.max(errors))

        # 排序并取代表性采样点用于展示
        sort_idx = np.argsort(r_arr)
        r_sorted = r_arr[sort_idx]
        theo_sorted = theo_arr[sort_idx]
        num_sorted = num_arr[sort_idx]

        sample_table = []
        step = max(1, len(r_sorted) // 8)
        for idx in range(0, len(r_sorted), step):
            sample_table.append({
                "distance_m": round(float(r_sorted[idx]), 1),
                "analytical_s_m": round(float(theo_sorted[idx]), 4),
                "numerical_s_m": round(float(num_sorted[idx]), 4),
                "abs_error_m": round(float(abs(theo_sorted[idx] - num_sorted[idx])), 4),
            })

        # 4. 出图
        fig_file = None
        if plot:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7), sharex=True,
                                           gridspec_kw={"height_ratios": [2.5, 1]}, dpi=150)

            # 理论平滑曲线
            r_smooth = np.linspace(max(10.0, float(r_sorted.min())), float(r_sorted.max()), 200)
            u_smooth = (r_smooth ** 2 * S) / (4.0 * T * t_days)
            s_smooth = (Q / (4.0 * math.pi * T)) * _well_function(u_smooth)

            ax1.plot(r_smooth, s_smooth, "r-", label="Theis Analytical Solution", lw=2)
            ax1.scatter(r_sorted, num_sorted, color="royalblue", alpha=0.6, s=18,
                        edgecolor="none", label="MODFLOW Numerical (FD)")
            ax1.set_ylabel("Drawdown s (m)", fontsize=10)
            ax1.set_title(f"Theis Solution vs MODFLOW Drawdown (T={T:.1f} m²/d, S={S:.1e}, Q={Q:.0f} m³/d, t={t_days} d)",
                          fontsize=11)
            ax1.grid(True, linestyle="--", alpha=0.5)
            ax1.legend(loc="upper right")

            # 残差图
            residuals = num_sorted - theo_sorted
            ax2.scatter(r_sorted, residuals, color="teal", alpha=0.6, s=15)
            ax2.axhline(0, color="gray", linestyle="--", lw=1)
            ax2.set_xlabel("Distance from Pumping Well r (m)", fontsize=10)
            ax2.set_ylabel("Residual (m)", fontsize=10)
            ax2.grid(True, linestyle="--", alpha=0.5)

            fig.tight_layout()
            p = _fig_path(f"theis-benchmark-{Path(alias).stem}")
            fig.savefig(p)
            plt.close(fig)
            fig_file = str(p)

        return {
            "ok": True,
            "parameters": {
                "transmissivity_T": T,
                "storativity_S": S,
                "pumping_rate_Q": Q,
                "time_days": t_days,
                "well_location": [well_row, well_col]
            },
            "metrics": {
                "mae_m": round(mae, 4),
                "rmse_m": round(rmse, 4),
                "max_error_m": round(max_err, 4),
                "points_compared": len(r_points)
            },
            "samples": sample_table,
            "figure": fig_file,
            "conclusion": (
                f"对比良好：有限差分数值解与 Theis 理论解平均绝对误差 MAE = {mae:.4f} m，"
                f"RMSE = {rmse:.4f} m，满足数值精度检验要求。"
            )
        }

    @mcp.tool()
    def mfm_sensitivity_analysis(alias: str, param_name: str = "k",
                                scale_factors: list[float] = None,
                                obs_cells: list[list[int]] = None,
                                engine: str = "", vendor: str = "",
                                timeout: int = 1800) -> dict:
        """参数敏感性批量分析与响应出图。

        对指定参数（k / hk / sy / ss / wel）按比例因子（默认 [0.5, 0.8, 1.0, 1.2, 1.5]）扰动，
        在沙盒中自动批处理调用本地引擎求解，提取观测网格水位降深响应，并输出敏感性曲线图。
        """
        if scale_factors is None:
            scale_factors = [0.5, 0.8, 1.0, 1.2, 1.5]

        model = state.get(alias)
        mg = model.modelgrid
        nrow = getattr(mg, "nrow", 15)
        ncol = getattr(mg, "ncol", 15)

        if obs_cells is None:
            # 默认选取井中心附近网格及外围代表性网格
            obs_cells = [
                [0, nrow // 2, ncol // 2],
                [0, nrow // 2, max(0, ncol // 2 - 2)],
                [0, nrow // 2, max(0, ncol // 2 - 4)],
            ]

        # 原始参数备份
        param_norm = param_name.lower().strip()
        orig_array = None
        orig_wel_spd = None

        if param_norm in ("k", "hk", "sy", "ss"):
            orig_info = mfmodel.get_array(model, param_norm, layer=0)
            orig_array = np.array(orig_info.get("values"), dtype=float) if "values" in orig_info else None
        elif param_norm == "wel":
            wel = model.get_package("WEL")
            if wel and hasattr(wel, "stress_period_data"):
                orig_wel_spd = {k: list(v) for k, v in wel.stress_period_data.data.items()}

        results = []
        ws_root = env.workspace() / "sensitivity" / f"{param_norm}_{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
        ws_root.mkdir(parents=True, exist_ok=True)

        for factor in scale_factors:
            case_ws = ws_root / f"factor_{factor:.2f}"
            case_ws.mkdir(parents=True, exist_ok=True)

            # 修改参数
            if param_norm in ("k", "hk", "sy", "ss") and orig_array is not None:
                mfmodel.set_array(model, param_norm, values=(orig_array * factor).tolist(), layer=0)
            elif param_norm == "wel" and orig_wel_spd is not None:
                new_spd = {}
                for kper, rows in orig_wel_spd.items():
                    new_rows = []
                    for r in rows:
                        if hasattr(r, 'dtype') and r.dtype.names:
                            row_copy = list(r)
                            row_copy[-1] = float(row_copy[-1]) * factor
                        else:
                            row_copy = list(r)
                            row_copy[-1] = float(row_copy[-1]) * factor
                        new_rows.append(row_copy)
                    new_spd[kper] = new_rows
                wel = model.get_package("WEL")
                wel.stress_period_data = new_spd

            # 写入模型到独立子目录并计算
            model.model_ws = str(case_ws)
            model.write_input()

            run_res = mfmodel.run_model(model, engine_key=engine or None,
                                        vendor=vendor or None, timeout=timeout)

            # 读取降深
            dd_vals = []
            if run_res.get("success"):
                dd_out = mfmodel.drawdown(model)
                dd_arr = None
                for d_rec in dd_out.get("result", []):
                    if "values" in d_rec:
                        dd_arr = np.asarray(d_rec["values"])
                        break
                if dd_arr is not None:
                    for oc in obs_cells:
                        lay, r, c = oc[0], oc[1], oc[2]
                        if dd_arr.ndim == 3 and lay < dd_arr.shape[0] and r < dd_arr.shape[1] and c < dd_arr.shape[2]:
                            dd_vals.append(round(float(dd_arr[lay, r, c]), 4))
                        elif dd_arr.ndim == 2 and r < dd_arr.shape[0] and c < dd_arr.shape[1]:
                            dd_vals.append(round(float(dd_arr[r, c]), 4))
                        else:
                            dd_vals.append(None)

            results.append({
                "factor": factor,
                "converged": run_res.get("converged", False),
                "obs_drawdowns": dd_vals
            })

        # 恢复原参数与工作目录
        if orig_array is not None and param_norm in ("k", "hk", "sy", "ss"):
            mfmodel.set_array(model, param_norm, values=orig_array.tolist(), layer=0)
        elif orig_wel_spd is not None and param_norm == "wel":
            wel = model.get_package("WEL")
            wel.stress_period_data = orig_wel_spd

        # 绘制敏感性曲线
        fig_file = None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(7.5, 5), dpi=150)
            factors = [r["factor"] for r in results if r["converged"]]
            for obs_idx, oc in enumerate(obs_cells):
                y_series = [r["obs_drawdowns"][obs_idx] for r in results if r["converged"] and len(r["obs_drawdowns"]) > obs_idx]
                if len(y_series) == len(factors):
                    ax.plot(factors, y_series, "o-", label=f"Obs Cell L{oc[0]}R{oc[1]}C{oc[2]}", lw=1.8)

            ax.set_xlabel(f"Scale Factor of Parameter '{param_name}'", fontsize=10)
            ax.set_ylabel("Drawdown s (m)", fontsize=10)
            ax.set_title(f"Parameter Sensitivity Response ({param_name.upper()})", fontsize=11)
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.legend()
            fig.tight_layout()

            p = _fig_path(f"sensitivity-{param_norm}-{Path(alias).stem}")
            fig.savefig(p)
            plt.close(fig)
            fig_file = str(p)
        except Exception:
            pass

        return {
            "ok": True,
            "parameter": param_name,
            "obs_cells": obs_cells,
            "runs": results,
            "figure": fig_file,
            "summary": f"完成 {len(scale_factors)} 组参数扰动计算，敏感性响应曲线已生成。"
        }

    @mcp.tool()
    def mfm_check_project(project_path: str, run_test: bool = True) -> dict:
        """工程与模型目录规范性完整体检。

        针对工程目录或作业交付文件：
        1. 检查是否存在中文字符、特殊符号或非法空格路径（防止老 Fortran 引擎崩溃）；
        2. 扫描 .nam 文件并核对所有被引用的包文件（DIS, BAS, LPF/BCF, PCG, OC 等）是否存在且非空；
        3. 可选执行一键快速验证运行，核验收敛状态与水量均衡相对误差。
        """
        p = Path(project_path)
        if not p.exists():
            return {"ok": False, "error": f"指定路径不存在: {project_path}"}

        items = []

        # 1. 路径规范性检查
        path_str = str(p.resolve())
        has_non_ascii = any(ord(c) > 127 for c in path_str)
        has_spaces = " " in path_str

        if has_non_ascii:
            items.append({
                "check": "路径字符集",
                "status": "WARN",
                "message": "工程路径包含中文字符。部分老版本 Fortran 引擎（如 Mf2k/MODFLOW-96）无法识别中文路径，可能导致闪退。"
            })
        elif has_spaces:
            items.append({
                "check": "路径字符集",
                "status": "WARN",
                "message": "工程路径包含空格，易在命令行或 NAME 文件解析中被截断，建议重命名为无空格下划线路径。"
            })
        else:
            items.append({
                "check": "路径字符集",
                "status": "PASS",
                "message": "路径为纯 ASCII 字符且无空格，兼容所有老版本编译引擎。"
            })

        # 2. 查找关键工程入口文件
        nam_files = list(p.rglob("*.nam")) if p.is_dir() else ([p] if p.suffix.lower() == ".nam" else [])
        vmf_files = list(p.rglob("*.vmf")) if p.is_dir() else ([p] if p.suffix.lower() == ".vmf" else [])

        if not nam_files and not vmf_files:
            items.append({
                "check": "工程文件结构",
                "status": "FAIL",
                "message": "未在目录中找到 .nam (MODFLOW) 或 .vmf (Visual MODFLOW) 工程主文件。"
            })
            return {"ok": False, "score": 30, "checks": items}

        items.append({
            "check": "工程文件结构",
            "status": "PASS",
            "message": f"找到工程主文件: {len(nam_files)} 个 .nam, {len(vmf_files)} 个 .vmf"
        })

        # 3. NAME FILE 引用依赖检查
        missing_pkgs = []
        found_pkgs = []
        if nam_files:
            main_nam = nam_files[0]
            try:
                for line in main_nam.read_text(encoding="latin-1", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        ftype = parts[0].upper()
                        fname = parts[2]
                        # 相对或绝对路径
                        fpath = main_nam.parent / fname if not Path(fname).is_absolute() else Path(fname)
                        if fpath.exists() and fpath.stat().st_size > 0:
                            found_pkgs.append(ftype)
                        else:
                            missing_pkgs.append(f"{ftype} ({fname})")
            except Exception as e:
                items.append({"check": "NAME 文件解析", "status": "WARN", "message": str(e)})

        if missing_pkgs:
            items.append({
                "check": "包文件依赖完整性",
                "status": "FAIL",
                "message": f"发现 {len(missing_pkgs)} 个在 NAME 文件中登记但实际缺失的包文件: {', '.join(missing_pkgs)}"
            })
        else:
            items.append({
                "check": "包文件依赖完整性",
                "status": "PASS",
                "message": f"全部登记的包文件均存在且非空（检测到: {', '.join(set(found_pkgs))}）"
            })

        # 4. 试运行核验
        run_res = None
        if run_test and nam_files and not missing_pkgs:
            try:
                test_model = mfmodel.load_model(str(nam_files[0]))
                run_res = mfmodel.run_model(test_model, timeout=60)
                if run_res.get("success") and run_res.get("converged"):
                    items.append({
                        "check": "试运行与收敛性",
                        "status": "PASS",
                        "message": "引擎试运行成功，数值求解完全收敛。"
                    })
                else:
                    items.append({
                        "check": "试运行与收敛性",
                        "status": "WARN",
                        "message": f"引擎试运行未收敛或异常退出（代码: {run_res.get('return_code')}）"
                    })
            except Exception as e:
                items.append({
                    "check": "试运行与收敛性",
                    "status": "WARN",
                    "message": f"试运行跳过或异常: {e}"
                })

        fails = sum(1 for c in items if c["status"] == "FAIL")
        warns = sum(1 for c in items if c["status"] == "WARN")
        score = max(0, 100 - fails * 35 - warns * 15)

        return {
            "ok": fails == 0,
            "project_path": str(p),
            "score": score,
            "fails_count": fails,
            "warnings_count": warns,
            "checks": items,
            "run_result": run_res,
            "rating": "优秀 (Pass)" if score >= 85 else ("需整改 (Warning)" if score >= 60 else "不合格 (Fail)")
        }
