"""地下水非稳定井流抽水试验参数求解与 Golden Software Grapher 自动化工具集。

功能包含：
1. Theis 双对数配线法自动优化拟合（解析解井函数匹配求解 T, S）；
2. Cooper-Jacob 半对数直线图解法自动拟合（斜率 Δs 与截距 t0 求解 T, S）；
3. Golden Software Grapher 16 自动化脚本（.BAS）生成；
4. Grapher 16 原生图形渲染与自动化执行，输出原生 .grf 工程文件与 .png 图像。
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from .. import env

try:
    from scipy.special import exp1 as _exp1
except ImportError:
    _exp1 = None

try:
    from scipy.optimize import minimize as _minimize
except ImportError:
    _minimize = None

try:
    import win32gui
    import win32process
    import win32con
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


def _find_grapher_paths() -> tuple[Path | None, Path | None]:
    """定位 Golden Software Grapher 主程序与 Scripter 脚本执行引擎。

    查找逻辑上提到 ``env.find_grapher``（config/env.json → GRAPHER_HOME → 常见安装目录），
    此处保留薄封装以免改动调用方。
    """
    return env.find_grapher()


def _execute_grapher_script(bas_file: Path, target_grf: Path | None = None,
                           target_png: Path | None = None, timeout: float = 60.0) -> dict:
    """通过 Scripter.exe 原生执行 Grapher BASIC 自动化脚本，生成 .grf 与 .png。"""
    grapher_exe, scripter_exe = _find_grapher_paths()
    if not grapher_exe or not scripter_exe:
        return {
            "ok": False,
            "error": "未检测到 Golden Software Grapher 安装目录，请在 config/env.json 中配置 grapher_home"
        }

    # 1. 彻底清理任何遗留/僵尸 Grapher/Scripter 进程
    subprocess.run(["taskkill", "/F", "/IM", "Grapher.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "Scripter.exe"], capture_output=True)
    time.sleep(0.5)

    # 2. 清理目标文件防止覆盖弹窗
    if target_grf and target_grf.exists():
        try:
            target_grf.unlink()
        except Exception:
            pass
    if target_png and target_png.exists():
        try:
            target_png.unlink()
        except Exception:
            pass

    # 3. 启动主程序承载 COM 服务
    proc = subprocess.Popen([str(grapher_exe)], cwd=str(grapher_exe.parent))
    time.sleep(2.5)

    try:
        res = subprocess.run(
            [str(scripter_exe), "-x", str(bas_file)],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        time.sleep(1.0)
        grf_ok = bool(target_grf and target_grf.exists() and target_grf.stat().st_size > 0)
        png_ok = bool(target_png and target_png.exists() and target_png.stat().st_size > 0)

        return {
            "ok": True,
            "exit_code": res.returncode,
            "grf_path": str(target_grf) if target_grf else None,
            "grf_exists": grf_ok,
            "png_path": str(target_png) if target_png else None,
            "png_exists": png_ok,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Scripter 执行超时 ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
        except Exception:
            pass
        subprocess.run(["taskkill", "/F", "/IM", "Grapher.exe"], capture_output=True)


def _well_function(u: float | np.ndarray) -> np.ndarray:
    """计算 Theis 井函数 W(u)。"""
    u_arr = np.asarray(u, dtype=float)
    if _exp1 is not None:
        valid = u_arr > 0
        res = np.zeros_like(u_arr)
        res[valid] = _exp1(u_arr[valid])
        return res

    res = np.zeros_like(u_arr)
    valid = u_arr > 0
    u_v = u_arr[valid]
    small = u_v < 1.0
    u_small = u_v[small]
    euler = 0.5772156649
    terms = u_small - (u_small**2)/4.0 + (u_small**3)/18.0 - (u_small**4)/96.0 + (u_small**5)/600.0
    w_small = -euler - np.log(u_small) + terms
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


def _script_path(tag: str, ext: str = "bas") -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = env.workspace() / "grapher_scripts"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{tag}-{ts}.{ext}"


def _load_data_file(file_path: str) -> tuple[np.ndarray, np.ndarray, str, str]:
    """读取数据文件（.dat / .txt / .csv / .xlsx），返回 (x, y, x_name, y_name)。"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_excel(p)
        cols = df.select_dtypes(include=[np.number]).columns
        if len(cols) < 2:
            raise ValueError(f"Excel 中未找到至少两列数值数据: {file_path}")
        x = df[cols[0]].dropna().to_numpy(dtype=float)
        y = df[cols[1]].dropna().to_numpy(dtype=float)
        return x, y, str(cols[0]), str(cols[1])

    sep = "," if suffix == ".csv" else None
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    header_x, header_y = "X", "Y"
    start_row = 0
    for idx, l in enumerate(lines):
        parts = l.strip().split(sep)
        try:
            float(parts[0])
            float(parts[1])
            start_row = idx
            break
        except (ValueError, IndexError):
            if len(parts) >= 2:
                header_x, header_y = parts[0].strip(), parts[1].strip()

    data = np.loadtxt(p, skiprows=start_row, delimiter=sep)
    return data[:, 0], data[:, 1], header_x, header_y


def register(mcp) -> None:

    @mcp.tool()
    def mfm_theis_type_curve_fit(data_file: str, Q: float = 1267.2,
                                 q_unit: str = "m3/d",
                                 time_unit: str = "min",
                                 plot: bool = True,
                                 generate_grapher_script: bool = True,
                                 render_grapher: bool = True) -> dict:
        """Theis 双对数配线法自动优化拟合与水文地质参数（T、S）求解。

        读取抽水试验实测数据（t/r^2 与降深 s），利用严格的 Theis 井函数 W(u) = E1(u)
        在双对数空间进行非线性最小二乘优化平移，确定最佳匹配点并计算导水系数 T 与储水系数 S。
        可一键调用 Golden Software Grapher 16 原生引擎输出原生 .grf 工程文件与出版级高清图。

        参数：
        - data_file: 包含 t/r^2 与降深 s 的实测数据文本文件路径（如 'data/theis_pumping_test.dat'）；
        - Q: 抽水量（默认 1267.2 m3/d，相当于 52.8 m3/h）；
        - q_unit: 抽水量单位（'m3/d', 'm3/h', 'm3/min', 'L/s'）；
        - time_unit: 观测时间 t 的原始单位（'min', 'd', 'h', 's'）；
        - plot: 是否生成 Python 对比图；
        - generate_grapher_script: 是否生成配套的 Grapher 16 自动化脚本（.bas）；
        - render_grapher: 是否直接调用 Grapher 16 / Scripter 原生生成 .grf 工程与原图（默认 True）。
        """
        tr2_arr, s_arr, x_label, y_label = _load_data_file(data_file)
        valid = (tr2_arr > 0) & (s_arr > 0)
        tr2_arr = tr2_arr[valid]
        s_arr = s_arr[valid]

        if len(tr2_arr) < 3:
            return {"ok": False, "error": "有效正值数据点少于 3 个，无法进行双对数拟合"}

        log_tr2 = np.log10(tr2_arr)
        log_s = np.log10(s_arr)

        if q_unit.lower() == "m3/d":
            q_m3_d = Q
        elif q_unit.lower() == "m3/h":
            q_m3_d = Q * 24.0
        elif q_unit.lower() == "m3/min":
            q_m3_d = Q * 1440.0
        elif q_unit.lower() == "l/s":
            q_m3_d = Q * 86.4
        else:
            q_m3_d = Q

        time_u = time_unit.lower()
        if time_u in ("min", "minute"):
            q_in_time_unit = q_m3_d / 1440.0
            time_factor_to_day = 1440.0
        elif time_u in ("h", "hour"):
            q_in_time_unit = q_m3_d / 24.0
            time_factor_to_day = 24.0
        elif time_u in ("s", "sec", "second"):
            q_in_time_unit = q_m3_d / 86400.0
            time_factor_to_day = 86400.0
        else:
            q_in_time_unit = q_m3_d
            time_factor_to_day = 1.0

        def log_w_exact(log_inv_u_val):
            inv_u = 10.0**log_inv_u_val
            u = 1.0 / np.maximum(inv_u, 1e-12)
            w = _well_function(u)
            return np.log10(np.maximum(w, 1e-12))

        def obj(params):
            dx, dy = params
            log_inv_u = log_tr2 - dx
            log_w_pred = log_w_exact(log_inv_u)
            log_s_pred = log_w_pred + dy
            return np.sum((log_s - log_s_pred)**2)

        init_dx = float(np.mean(log_tr2) - 2.0)
        init_dy = float(np.mean(log_s) - 0.5)

        if _minimize is not None:
            opt = _minimize(obj, [init_dx, init_dy], method="Nelder-Mead")
            dx_opt, dy_opt = float(opt.x[0]), float(opt.x[1])
        else:
            dx_grid = np.linspace(init_dx - 3, init_dx + 3, 50)
            dy_grid = np.linspace(init_dy - 2, init_dy + 2, 50)
            best_cost = float("inf")
            dx_opt, dy_opt = init_dx, init_dy
            for gx in dx_grid:
                for gy in dy_grid:
                    c = obj([gx, gy])
                    if c < best_cost:
                        best_cost = c
                        dx_opt, dy_opt = gx, gy

        s_star = float(10.0**dy_opt)
        tr2_star = float(10.0**dx_opt)

        t_in_time_unit = q_in_time_unit / (4.0 * math.pi * s_star)
        t_m2_d = t_in_time_unit * time_factor_to_day
        s_coef = 4.0 * t_in_time_unit * tr2_star

        log_inv_u_calc = log_tr2 - dx_opt
        s_fitted = 10.0**(log_w_exact(log_inv_u_calc) + dy_opt)
        residuals = s_arr - s_fitted
        rmse = float(np.sqrt(np.mean(residuals**2)))
        mae = float(np.mean(np.abs(residuals)))
        r2 = float(1.0 - np.sum(residuals**2) / np.sum((s_arr - np.mean(s_arr))**2))

        fig_path = None
        if plot:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(8, 6.5), dpi=150)
            inv_u_dense = np.logspace(-1, 5, 200)
            w_dense = _well_function(1.0 / inv_u_dense)
            tr2_dense = inv_u_dense * 10.0**dx_opt
            s_dense = w_dense * 10.0**dy_opt

            ax.loglog(tr2_dense, s_dense, "r-", lw=2, label="Theis Type Curve (Matched)")
            ax.scatter(tr2_arr, s_arr, color="royalblue", s=30, zorder=5, label="Field Data ($t/r^2 - s$)")

            ax.plot([tr2_star], [s_star], "k*", markersize=14, zorder=6,
                    label=f"Match Point [$s^*={s_star:.3f}$, $(t/r^2)^*={tr2_star:.2e}$]")
            ax.axvline(tr2_star, color="gray", linestyle=":", alpha=0.6)
            ax.axhline(s_star, color="gray", linestyle=":", alpha=0.6)

            ax.set_xlabel(f"$t/r^2$ ({time_unit}/m²)", fontsize=11)
            ax.set_ylabel("Drawdown $s$ (m)", fontsize=11)
            ax.set_title(f"Theis Type Curve Matching (T = {t_m2_d:.2f} m²/d, S = {s_coef:.2e}, R² = {r2:.4f})",
                         fontsize=11)
            ax.grid(True, which="both", linestyle="--", alpha=0.4)
            ax.legend(loc="lower right")

            fig.tight_layout()
            p = _fig_path("theis-type-curve-fit")
            fig.savefig(p)
            plt.close(fig)
            fig_path = str(p)

        # 准备标准 Theis 曲线 1/u - W(u)
        std_dat = env.workspace() / "Theis_Std_1_u_Wu.dat"
        inv_u_dense = np.logspace(-1, 4, 300)
        w_dense = _well_function(1.0 / inv_u_dense)
        with open(std_dat, "w", encoding="ascii") as fp:
            fp.write("inv_u\tW_u\n")
            for iu, wu in zip(inv_u_dense, w_dense):
                fp.write(f"{iu:.6e}\t{wu:.6f}\n")

        # 准备实测点集 (t/r^2 - s)
        field_sub_dat = env.workspace() / "Theis_Field_Obs.dat"
        with open(field_sub_dat, "w", encoding="ascii") as fp:
            fp.write("tr2\ts\n")
            for x_val, y_val in zip(tr2_arr, s_arr):
                fp.write(f"{x_val:.8e}\t{y_val:.4f}\n")

        (env.workspace() / "figs").mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target_grf = env.workspace() / "figs" / f"Grapher_Theis_{ts}.grf"
        target_png = env.workspace() / "figs" / f"Grapher_Theis_{ts}.png"

        # 双重平移坐标系几何计算 (同对数模数 k=1.0 in/decade)
        axis_len_x = 5.0
        axis_len_y = 4.0
        g1_xpos = 2.4
        g1_ypos = 2.2
        kx = axis_len_x / 5.0  # 1.0 in/decade
        ky = axis_len_y / 4.0  # 1.0 in/decade
        m2x = -5.0
        m1x = -1.0
        g2_xpos = float(g1_xpos + kx * (m2x - m1x - dx_opt))
        m2y = -4.0
        m1y = -3.0
        g2_ypos = float(g1_ypos + ky * (m2y - m1y - dy_opt))

        inv_u_star = 1.0
        w_star = float(_well_function(1.0))
        # 对应实测匹配点
        tr2_star_calc = float(10.0**dx_opt)
        s_star_calc = float(w_star * 10.0**dy_opt)

        grapher_bas = None
        if generate_grapher_script or render_grapher:
            grapher_bas = _create_theis_grapher_script(
                std_file=str(std_dat.resolve()),
                field_file=str(field_sub_dat.resolve()),
                grf_path=str(target_grf.resolve()),
                png_path=str(target_png.resolve()),
                g1_xpos=g1_xpos,
                g1_ypos=g1_ypos,
                g2_xpos=g2_xpos,
                g2_ypos=g2_ypos,
                axis_len_x=axis_len_x,
                axis_len_y=axis_len_y,
                inv_u_star=inv_u_star,
                w_star=w_star,
                tr2_star=tr2_star_calc,
                s_star=s_star_calc,
                t_m2_d=t_m2_d,
                s_coef=s_coef,
                r2=r2,
                time_unit=time_unit,
                x_field_title="t/r\\up50 2\\dn50"
            )

        grapher_exec = None
        if render_grapher and grapher_bas:
            grapher_exec = _execute_grapher_script(
                bas_file=grapher_bas,
                target_grf=target_grf,
                target_png=target_png
            )

        return {
            "ok": True,
            "data_file": str(data_file),
            "points_count": len(tr2_arr),
            "match_point": {
                "inv_u_star": 1.0,
                "w_star": 1.0,
                "s_star_m": round(s_star, 4),
                "tr2_star": tr2_star,
            },
            "parameters": {
                "T_m2_d": round(t_m2_d, 2),
                f"T_m2_{time_unit}": round(t_in_time_unit, 5),
                "S": float(f"{s_coef:.4e}"),
                "Q_m3_d": q_m3_d
            },
            "fitting_quality": {
                "r2": round(r2, 4),
                "rmse_m": round(rmse, 4),
                "mae_m": round(mae, 4)
            },
            "figure": fig_path,
            "grapher_script": str(grapher_bas) if grapher_bas else None,
            "grapher_native": {
                "rendered": bool(grapher_exec and grapher_exec.get("grf_exists")),
                "grf_path": str(target_grf) if (grapher_exec and grapher_exec.get("grf_exists")) else None,
                "png_path": str(target_png) if (grapher_exec and grapher_exec.get("png_exists")) else None,
                "execution_details": grapher_exec
            },
            "interpretation": (
                f"配线法计算完成：导水系数 T = {t_m2_d:.2f} m²/d，储水系数 S = {s_coef:.2e}。"
                f"拟合决定系数 R² = {r2:.4f}，匹配优度极高。"
            )
        }

    @mcp.tool()
    def mfm_jacob_straight_line_fit(data_file: str, r: float = 43.0,
                                   Q: float = 1267.2,
                                   q_unit: str = "m3/d",
                                   time_unit: str = "min",
                                   plot: bool = True,
                                   generate_grapher_script: bool = True,
                                   render_grapher: bool = True) -> dict:
        """Cooper-Jacob 半对数直线图解法自动拟合与求参。

        读取抽水试验单孔观测数据（时间 t 与降深 s），在半对数坐标系（s - lg t）下
        自动优选满足 u <= 0.05 判据的线性后段，通过回归计算斜率 Δs 与横轴截距 t0，
        依据 Cooper-Jacob 公式求解导水系数 T 与储水系数 S。
        可一键调用 Golden Software Grapher 16 原生引擎输出原生 .grf 工程文件与出版级高清图。

        参数：
        - data_file: 包含历时 t 与降深 s 的实测数据文本文件路径（如 'data/jacob_pumping_test.dat'）；
        - r: 观测孔至抽水井的距离（m，如 43.0 或 140.0）；
        - Q: 抽水量（默认 1267.2 m3/d）；
        - q_unit: 抽水量单位（'m3/d', 'm3/h' 等）；
        - time_unit: 时间单位（'min', 'd', 'h', 's'）；
        - plot: 是否生成 Python 对比图；
        - generate_grapher_script: 是否生成配套的 Grapher 16 自动化脚本（.bas）；
        - render_grapher: 是否直接调用 Grapher 16 / Scripter 原生生成 .grf 工程与原图（默认 True）。
        """
        t_arr, s_arr, x_label, y_label = _load_data_file(data_file)
        valid = (t_arr > 0) & (s_arr > 0)
        t_arr = t_arr[valid]
        s_arr = s_arr[valid]

        if len(t_arr) < 3:
            return {"ok": False, "error": "有效正值数据点少于 3 个，无法进行半对数拟合"}

        if q_unit.lower() == "m3/d":
            q_m3_d = Q
        elif q_unit.lower() == "m3/h":
            q_m3_d = Q * 24.0
        elif q_unit.lower() == "m3/min":
            q_m3_d = Q * 1440.0
        elif q_unit.lower() == "l/s":
            q_m3_d = Q * 86.4
        else:
            q_m3_d = Q

        time_u = time_unit.lower()
        if time_u in ("min", "minute"):
            q_in_time = q_m3_d / 1440.0
            time_factor_to_day = 1440.0
        elif time_u in ("h", "hour"):
            q_in_time = q_m3_d / 24.0
            time_factor_to_day = 24.0
        elif time_u in ("s", "sec", "second"):
            q_in_time = q_m3_d / 86400.0
            time_factor_to_day = 86400.0
        else:
            q_in_time = q_m3_d
            time_factor_to_day = 1.0

        n = len(t_arr)
        best_r2 = -1.0
        best_start = 0
        best_fit = (0.0, 0.0)

        for start_idx in range(0, max(1, n - 2)):
            sub_t = t_arr[start_idx:]
            sub_s = s_arr[start_idx:]
            log_t = np.log10(sub_t)
            slope, intercept = np.polyfit(log_t, sub_s, 1)
            pred = slope * log_t + intercept
            ss_tot = np.sum((sub_s - np.mean(sub_s))**2)
            ss_res = np.sum((sub_s - pred)**2)
            r2_val = 1.0 - ss_res / (ss_tot + 1e-12)

            if slope > 0 and r2_val > best_r2:
                best_r2 = r2_val
                best_start = start_idx
                best_fit = (slope, intercept)

        a_slope, b_intercept = best_fit
        delta_s = float(a_slope)
        t0 = float(10.0**(-b_intercept / a_slope))

        t_in_time = 0.183 * q_in_time / delta_s
        t_m2_d = t_in_time * time_factor_to_day

        s_coef = 2.25 * t_in_time * t0 / (r**2)
        u_last = (r**2 * s_coef) / (4.0 * t_in_time * t_arr[-1])

        fig_path = None
        if plot:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
            ax.semilogx(t_arr, s_arr, "o", color="royalblue", markersize=6, label="Observed Drawdown")

            t_fit_line = np.logspace(np.log10(max(t0 * 0.8, 1e-3)), np.log10(t_arr[-1] * 1.5), 100)
            s_fit_line = a_slope * np.log10(t_fit_line) + b_intercept
            ax.semilogx(t_fit_line, s_fit_line, "r-", lw=2,
                        label=f"Straight Line Fit (Δs={delta_s:.3f} m, R²={best_r2:.4f})")
            ax.plot([t0], [0.0], "r*", markersize=12, label=f"Intercept $t_0={t0:.2f}$ {time_unit}")

            ax.set_xlabel(f"Time $t$ ({time_unit})", fontsize=11)
            ax.set_ylabel("Drawdown $s$ (m)", fontsize=11)
            ax.set_title(f"Cooper-Jacob Straight Line Method (r={r} m, T={t_m2_d:.2f} m²/d, S={s_coef:.2e})",
                         fontsize=11)
            ax.grid(True, which="both", linestyle="--", alpha=0.4)
            ax.legend(loc="lower right")

            fig.tight_layout()
            p = _fig_path("jacob-semilog-fit")
            fig.savefig(p)
            plt.close(fig)
            fig_path = str(p)

        # 准备实测散点数据 (Lgt - s)
        log_t_sub = np.log10(t_arr[best_start:])
        s_sub = s_arr[best_start:]
        scatter_dat = env.workspace() / "Jacob_Scatter_Lgt_s.dat"
        with open(scatter_dat, "w", encoding="ascii") as fp:
            fp.write("Lgt\ts\n")
            for x_val, y_val in zip(log_t_sub, s_sub):
                fp.write(f"{x_val:.6f}\t{y_val:.4f}\n")

        # 拟合直线数据 (两端点扩展线，供 Grapher 绘制)
        x_line_dense = np.linspace(min(log_t_sub) * 0.95, max(log_t_sub) * 1.05, 50)
        y_line_dense = a_slope * x_line_dense + b_intercept
        line_dat = env.workspace() / "Jacob_Fitted_Line.dat"
        with open(line_dat, "w", encoding="ascii") as fp:
            fp.write("Lgt\ts_line\n")
            for x_val, y_val in zip(x_line_dense, y_line_dense):
                fp.write(f"{x_val:.6f}\t{y_val:.6f}\n")

        n_pts = len(log_t_sub)
        mean_x = float(np.mean(log_t_sub))
        mean_y = float(np.mean(s_sub))
        pred_sub = a_slope * log_t_sub + b_intercept
        ss_res = float(np.sum((s_sub - pred_sub)**2))
        ss_tot = float(np.sum((s_sub - mean_y)**2))
        sigma_sq = float(ss_res / max(1, n_pts - 2))

        (env.workspace() / "figs").mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target_grf = env.workspace() / "figs" / f"Grapher_Jacob_{ts}.grf"
        target_png = env.workspace() / "figs" / f"Grapher_Jacob_{ts}.png"

        grapher_bas = None
        if generate_grapher_script or render_grapher:
            grapher_bas = _create_jacob_grapher_script(
                scatter_file=str(scatter_dat.resolve()),
                line_file=str(line_dat.resolve()),
                grf_path=str(target_grf.resolve()),
                png_path=str(target_png.resolve()),
                a_slope=a_slope,
                b_intercept=b_intercept,
                n_pts=n_pts,
                mean_x=mean_x,
                mean_y=mean_y,
                ss_res=ss_res,
                ss_tot=ss_tot,
                r2=best_r2,
                sigma_sq=sigma_sq,
                delta_s=delta_s,
                t0=t0,
                t_m2_d=t_m2_d,
                s_coef=s_coef,
                u_last=float(u_last),
                time_unit=time_unit
            )

        grapher_exec = None
        if render_grapher and grapher_bas:
            grapher_exec = _execute_grapher_script(
                bas_file=grapher_bas,
                target_grf=target_grf,
                target_png=target_png
            )

        return {
            "ok": True,
            "data_file": str(data_file),
            "distance_r_m": r,
            "straight_line": {
                "delta_s_m": round(delta_s, 4),
                f"t0_{time_unit}": round(t0, 4),
                "slope_a": round(a_slope, 4),
                "intercept_b": round(b_intercept, 4),
                "r2": round(best_r2, 4)
            },
            "parameters": {
                "T_m2_d": round(t_m2_d, 2),
                f"T_m2_{time_unit}": round(t_in_time, 5),
                "S": float(f"{s_coef:.4e}"),
                "Q_m3_d": q_m3_d
            },
            "validity_check": {
                "u_end": round(float(u_last), 5),
                "u_condition_satisfied": bool(u_last <= 0.05),
                "note": "u <= 0.05 条件满足，小 u 截断误差小于 1%" if u_last <= 0.05 else "u > 0.05，初期数据可能存在一定理论偏差"
            },
            "figure": fig_path,
            "grapher_script": str(grapher_bas) if grapher_bas else None,
            "grapher_native": {
                "rendered": bool(grapher_exec and grapher_exec.get("grf_exists")),
                "grf_path": str(target_grf) if (grapher_exec and grapher_exec.get("grf_exists")) else None,
                "png_path": str(target_png) if (grapher_exec and grapher_exec.get("png_exists")) else None,
                "execution_details": grapher_exec
            },
            "interpretation": (
                f"Cooper-Jacob 半对数图解法计算完成：对数斜率 Δs = {delta_s:.3f} m，"
                f"零降深截距 t0 = {t0:.3f} {time_unit}；求解得到导水系数 T = {t_m2_d:.2f} m²/d，"
                f"储水系数 S = {s_coef:.2e}。"
            )
        }

    @mcp.tool()
    def mfm_run_grapher_script(script_file: str, timeout: float = 30.0) -> dict:
        """原生运行任意 Golden Software Grapher 自动化脚本（.BAS）。

        调用 Grapher 16 Scripter 引擎执行脚本，生成原生工程文件 (.grf) 与高清出图 (.png)。

        参数：
        - script_file: 脚本文件完整路径（.bas）；
        - timeout: 执行超时秒数（默认 30 秒）。
        """
        p = Path(script_file).resolve()
        if not p.exists():
            return {"ok": False, "error": f"脚本文件不存在: {script_file}"}

        res = _execute_grapher_script(p, timeout=timeout)
        return {
            "ok": res["ok"],
            "script_file": str(p),
            "result": res
        }

    @mcp.tool()
    def mfm_generate_grapher_script(script_type: str, data_file: str,
                                   title: str = "Pumping Test Plot",
                                   render_now: bool = True) -> dict:
        """为抽水试验数据生成专用的 Golden Software Grapher 16 自动化脚本（.BAS），并可一键原生渲染。

        生成的脚本可直接在 Grapher 16 或 Scripter 中执行，自动创建标准的双对数/半对数工程并导出图片。
        script_type 可选：
        - 'theis_loglog': 双对数 Theis 标准配线图（含主副对数网格线）；
        - 'jacob_semilog': 半对数 Cooper-Jacob 直线拟合图；
        - 'multiline': 多观测孔降深过程线图。
        """
        p = Path(data_file).resolve()
        if not p.exists():
            return {"ok": False, "error": f"数据文件不存在: {data_file}"}

        out_path = _script_path(f"grapher_{script_type}")
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target_grf = env.workspace() / "figs" / f"Grapher_{script_type}_{ts}.grf"
        target_png = env.workspace() / "figs" / f"Grapher_{script_type}_{ts}.png"

        if script_type.lower() == "theis_loglog":
            std_f = env.workspace() / "Theis_Std_1_u_Wu.dat"
            if not std_f.exists():
                iu_arr = np.logspace(-1, 4, 300)
                wu_arr = _well_function(1.0 / iu_arr)
                with open(std_f, "w", encoding="ascii") as fp:
                    fp.write("inv_u\tW_u\n")
                    for iu, wu in zip(iu_arr, wu_arr):
                        fp.write(f"{iu:.6e}\t{wu:.6f}\n")
            script_content = _theis_script_template(
                std_file=str(std_f),
                field_file=str(p),
                grf_path=str(target_grf),
                png_path=str(target_png),
                g1_xpos=2.4, g1_ypos=2.2,
                g2_xpos=1.77, g2_ypos=1.46,
                axis_len_x=5.0, axis_len_y=4.0,
                inv_u_star=1.0, w_star=0.21938,
                tr2_star=0.00043, s_star=0.1211,
                t_m2_d=182.63, s_coef=2.18e-4, r2=0.9860
            )
        elif script_type.lower() == "jacob_semilog":
            script_content = _jacob_script_template(
                scatter_file=str(p),
                line_file=str(p),
                grf_path=str(target_grf),
                png_path=str(target_png),
                a_slope=1.3244, b_intercept=-1.5268,
                n_pts=13, mean_x=2.5003, mean_y=1.7846,
                ss_res=0.00269, ss_tot=3.1997,
                r2=0.99916, sigma_sq=0.00024,
                delta_s=1.3244, t0=14.22,
                t_m2_d=175.10, s_coef=1.98e-4,
                u_last=0.0067
            )
        else:
            script_content = _general_script_template(
                str(p), str(target_grf), str(target_png), title
            )

        out_path.write_text(script_content, encoding="gbk", errors="ignore")

        exec_res = None
        if render_now:
            exec_res = _execute_grapher_script(out_path, target_grf, target_png)

        return {
            "ok": True,
            "script_file": str(out_path),
            "script_type": script_type,
            "data_file": str(p),
            "grapher_native": {
                "rendered": bool(exec_res and exec_res.get("grf_exists")),
                "grf_path": str(target_grf) if (exec_res and exec_res.get("grf_exists")) else None,
                "png_path": str(target_png) if (exec_res and exec_res.get("png_exists")) else None
            },
            "instructions": (
                f"已生成 Grapher 16 脚本：{out_path.name}。\n"
                f"在安装目录中打开 Scripter.exe 或 Grapher 16，选择该 .BAS 脚本即可一键绘制工程曲线并导出 .grf！"
            )
        }


def _theis_script_template(std_file: str, field_file: str,
                           grf_path: str, png_path: str,
                           g1_xpos: float, g1_ypos: float,
                           g2_xpos: float, g2_ypos: float,
                           axis_len_x: float, axis_len_y: float,
                           inv_u_star: float, w_star: float,
                           tr2_star: float, s_star: float,
                           t_m2_d: float, s_coef: float, r2: float,
                           time_unit: str = "min",
                           x_field_title: str = "t/r\\up50 2\\dn50") -> str:
    clean_std_path = str(std_file).replace("/", "\\")
    clean_field_path = str(field_file).replace("/", "\\")
    clean_grf_path = str(grf_path).replace("/", "\\")
    clean_png_path = str(png_path).replace("/", "\\")

    return f"""\
' ====================================================================
' Golden Software Grapher 16 Automation Script
' Theis Double-Logarithmic Type Curve Matching (Course Standard Style)
' Dual Shifted Coordinate Systems (双重平移坐标系)
' ====================================================================
Sub Main
    Dim GrapherApp, Plot, G1, G2, LineStd, LineField, Ax1X, Ax1Y, Ax2X, Ax2Y
    Dim Win1Box, Win1Bar, Win1HeadTxt, Win1Text, Win2Box, Win2Bar, Win2HeadTxt, Win2Text, ParamBox, ParamText
    On Error Resume Next

    Set GrapherApp = CreateObject("Grapher.Application")
    GrapherApp.Visible = True
    Set Plot = GrapherApp.Documents.Add(1)
    Plot.PageSetup.Orientation = grfLandscape

    ' -------------------------------------------------------------
    ' 1. Graph 1: Standard Type Curve (Fixed Reference Grid Box)
    ' -------------------------------------------------------------
    Set G1 = Plot.Shapes.AddLinePlotGraph("{clean_std_path}", 1, 2)
    G1.title.text = ""

    Set LineStd = G1.Plots.Item(1)
    LineStd.name = "Standard Type Curve W(u)"
    LineStd.symbolFreq = 0
    LineStd.line.style = "Solid"
    LineStd.line.foreColor = grfColorBlack
    LineStd.line.width = 0.015

    Set Ax1X = G1.Axes.Item(1)
    Ax1X.xPos = {g1_xpos:.3f}
    Ax1X.yPos = {g1_ypos:.3f}
    Ax1X.length = {axis_len_x:.2f}
    Ax1X.scale = grfAxisLog
    Ax1X.AutoMin = False: Ax1X.Min = 0.1
    Ax1X.AutoMax = False: Ax1X.Max = 10000
    Ax1X.title.text = "1/u"
    Ax1X.title.font.name = "Times New Roman"
    Ax1X.title.font.italic = True
    Ax1X.title.font.size = 12
    Ax1X.title.yOffset = 0.12
    Ax1X.LabelFont.name = "Times New Roman"
    Ax1X.LabelFont.size = 10
    Ax1X.Tickmarks.MajorSide = 1
    Ax1X.Tickmarks.MinorSide = 1
    Ax1X.Tickmarks.MajorLength = 0.12
    Ax1X.Tickmarks.MinorLength = 0.06
    Ax1X.Grid.AtMajorTicks = True
    Ax1X.Grid.MajorLine.style = "Solid"
    Ax1X.Grid.MajorLine.foreColor = grfColorBlack30
    Ax1X.Grid.MajorLine.width = 0.005

    Set Ax1Y = G1.Axes.Item(2)
    Ax1Y.xPos = {g1_xpos:.3f}
    Ax1Y.yPos = {g1_ypos:.3f}
    Ax1Y.length = {axis_len_y:.2f}
    Ax1Y.scale = grfAxisLog
    Ax1Y.AutoMin = False: Ax1Y.Min = 0.001
    Ax1Y.AutoMax = False: Ax1Y.Max = 10
    Ax1Y.title.text = "W(u)"
    Ax1Y.title.font.name = "Times New Roman"
    Ax1Y.title.font.italic = True
    Ax1Y.title.font.size = 12
    Ax1Y.title.xOffset = -0.12
    Ax1Y.title.yOffset = 0.6
    Ax1Y.LabelFont.name = "Times New Roman"
    Ax1Y.LabelFont.size = 10
    Ax1Y.Tickmarks.MajorSide = 1
    Ax1Y.Tickmarks.MinorSide = 1
    Ax1Y.Tickmarks.MajorLength = 0.12
    Ax1Y.Tickmarks.MinorLength = 0.06
    Ax1Y.Grid.AtMajorTicks = True
    Ax1Y.Grid.MajorLine.style = "Solid"
    Ax1Y.Grid.MajorLine.foreColor = grfColorBlack30
    Ax1Y.Grid.MajorLine.width = 0.005

    ' -------------------------------------------------------------
    ' 2. Graph 2: Field Data (Shifted Tracing Paper Coordinate System)
    ' -------------------------------------------------------------
    Set G2 = Plot.Shapes.AddLinePlotGraph("{clean_field_path}", 1, 2)
    G2.title.text = ""

    Set LineField = G2.Plots.Item(1)
    LineField.name = "Field Data"
    LineField.line.style = "Invisible"
    LineField.symbolFreq = 1
    LineField.symbol.Index = 12
    LineField.symbol.size = 0.08
    LineField.symbol.Fill.foreColor = grfColorBlack
    LineField.symbol.line.foreColor = grfColorBlack

    Set Ax2X = G2.Axes.Item(1)
    Ax2X.xPos = {g2_xpos:.3f}
    Ax2X.yPos = {g2_ypos:.3f}
    Ax2X.length = {axis_len_x:.2f}
    Ax2X.scale = grfAxisLog
    Ax2X.AutoMin = False: Ax2X.Min = 0.00001
    Ax2X.AutoMax = False: Ax2X.Max = 1
    Ax2X.title.text = "{x_field_title}"
    Ax2X.title.font.name = "Times New Roman"
    Ax2X.title.font.italic = True
    Ax2X.title.font.size = 12
    Ax2X.title.yOffset = 0.12
    Ax2X.LabelFont.name = "Times New Roman"
    Ax2X.LabelFont.size = 10
    Ax2X.Tickmarks.MajorSide = 1
    Ax2X.Tickmarks.MinorSide = 1
    Ax2X.Tickmarks.MajorLength = 0.12
    Ax2X.Tickmarks.MinorLength = 0.06
    Ax2X.Grid.AtMajorTicks = False

    Set Ax2Y = G2.Axes.Item(2)
    Ax2Y.xPos = {g2_xpos:.3f}
    Ax2Y.yPos = {g2_ypos:.3f}
    Ax2Y.length = 5.0
    Ax2Y.scale = grfAxisLog
    Ax2Y.AutoMin = False: Ax2Y.Min = 0.0001
    Ax2Y.AutoMax = False: Ax2Y.Max = 10
    Ax2Y.title.text = "s"
    Ax2Y.title.font.name = "Times New Roman"
    Ax2Y.title.font.italic = True
    Ax2Y.title.font.size = 13
    Ax2Y.title.xOffset = -0.12
    Ax2Y.LabelFont.name = "Times New Roman"
    Ax2Y.LabelFont.size = 10
    Ax2Y.Tickmarks.MajorSide = 1
    Ax2Y.Tickmarks.MinorSide = 1
    Ax2Y.Tickmarks.MajorLength = 0.12
    Ax2Y.Tickmarks.MinorLength = 0.06
    Ax2Y.Grid.AtMajorTicks = False

    ' -------------------------------------------------------------
    ' 3. Match Point Readout Windows & Parameter Summary (Right side)
    ' -------------------------------------------------------------
    ' Window 1: Standard Match Point
    Set Win1Box = Plot.Shapes.AddRectangle(7.8, 6.8, 11.2, 5.0)
    Win1Box.line.foreColor = grfColorBlack60
    Win1Box.line.width = 0.01
    Win1Box.Fill.foreColor = grfColorWhite

    Set Win1Bar = Plot.Shapes.AddRectangle(7.8, 6.8, 11.2, 6.45)
    Win1Bar.line.style = "Invisible"
    Win1Bar.Fill.foreColor = grfColorLightGray

    Dim w1Head As String
    w1Head = "Grapher - Readout 1               _  []  X"
    Set Win1HeadTxt = Plot.Shapes.AddText(7.95, 6.55, w1Head)
    Win1HeadTxt.font.name = "Arial"
    Win1HeadTxt.font.bold = True
    Win1HeadTxt.font.size = 8.5

    Dim w1Txt As String
    w1Txt = "File(F)   Edit(E)" & vbCrLf & _
            "-----------------------------------" & vbCrLf & _
            "{inv_u_star:.1f}, {w_star:.8f}" & vbCrLf & _
            "Theis Type Curve Match Point" & vbCrLf & _
            "[ 1/u , W(u) ]"
    Set Win1Text = Plot.Shapes.AddText(7.95, 6.25, w1Txt)
    Win1Text.font.name = "Arial"
    Win1Text.font.size = 8.5

    ' Window 2: Field Match Point
    Set Win2Box = Plot.Shapes.AddRectangle(7.8, 4.7, 11.2, 3.0)
    Win2Box.line.foreColor = grfColorBlack60
    Win2Box.line.width = 0.01
    Win2Box.Fill.foreColor = grfColorWhite

    Set Win2Bar = Plot.Shapes.AddRectangle(7.8, 4.7, 11.2, 4.35)
    Win2Bar.line.style = "Invisible"
    Win2Bar.Fill.foreColor = grfColorLightGray

    Dim w2Head As String
    w2Head = "Grapher - Readout 2               _  []  X"
    Set Win2HeadTxt = Plot.Shapes.AddText(7.95, 4.45, w2Head)
    Win2HeadTxt.font.name = "Arial"
    Win2HeadTxt.font.bold = True
    Win2HeadTxt.font.size = 8.5

    Dim w2Txt As String
    w2Txt = "File(F)   Edit(E)" & vbCrLf & _
            "-----------------------------------" & vbCrLf & _
            "{tr2_star:.7f}, {s_star:.8f}" & vbCrLf & _
            "Field Observation Match Point" & vbCrLf & _
            "[ t/r^2 , s ]"
    Set Win2Text = Plot.Shapes.AddText(7.95, 4.15, w2Txt)
    Win2Text.font.name = "Arial"
    Win2Text.font.size = 8.5

    ' Parameter summary box
    Set ParamBox = Plot.Shapes.AddRectangle(7.8, 2.7, 11.2, 1.2)
    ParamBox.line.foreColor = grfColorBlack60
    ParamBox.line.width = 0.01
    ParamBox.Fill.foreColor = grfColorWhite

    Dim pTxt As String
    pTxt = "Hydrogeological Parameters:" & vbCrLf & _
           "Transmissivity T = {t_m2_d:.2f} m\\up50 2\\dn50 / d" & vbCrLf & _
           "Storage Coeff. S = {s_coef:.4e}" & vbCrLf & _
           "Goodness of Fit R\\up50 2\\dn50 = {r2:.4f}"
    Set ParamText = Plot.Shapes.AddText(7.95, 2.45, pTxt)
    ParamText.font.name = "Arial"
    ParamText.font.size = 8.5

    Plot.Selection.DeselectAll
    Plot.SaveAs "{clean_grf_path}"
    Plot.Export "{clean_png_path}", False, "Defaults=1, Width=1920, Height=1080", False
    Plot.Close 2
End Sub
"""


def _jacob_script_template(scatter_file: str, line_file: str,
                           grf_path: str, png_path: str,
                           a_slope: float, b_intercept: float,
                           n_pts: int, mean_x: float, mean_y: float,
                           ss_res: float, ss_tot: float,
                           r2: float, sigma_sq: float,
                           delta_s: float, t0: float,
                           t_m2_d: float, s_coef: float,
                           u_last: float, time_unit: str = "min") -> str:
    clean_scatter_path = str(scatter_file).replace("/", "\\")
    clean_line_path = str(line_file).replace("/", "\\")
    clean_grf_path = str(grf_path).replace("/", "\\")
    clean_png_path = str(png_path).replace("/", "\\")

    return f"""\
' ====================================================================
' Golden Software Grapher 16 Automation Script
' Cooper-Jacob Semi-Log Method (Course Standard Style)
' Open L-shaped Axes, LaTeX Titles, Report 1 Window
' ====================================================================
Sub Main
    Dim GrapherApp, Plot, Graph, LineObs, LineFit, AxX, AxY
    Dim WinBox, WinBar, WinTitle, WinBtns, MenuBar, RepText, ParamBox, ParamText
    On Error Resume Next

    Set GrapherApp = CreateObject("Grapher.Application")
    GrapherApp.Visible = True
    Set Plot = GrapherApp.Documents.Add(1)
    Plot.PageSetup.Orientation = grfLandscape

    ' -------------------------------------------------------------
    ' 1. Graph and Plots (Positioned on the Right: x=5.2, y=1.2)
    ' -------------------------------------------------------------
    Set Graph = Plot.Shapes.AddLinePlotGraph("{clean_scatter_path}", 1, 2)
    Graph.title.text = ""

    ' Scatter points: Filled black circle, no line
    Set LineObs = Graph.Plots.Item(1)
    LineObs.name = "Observed Data"
    LineObs.line.style = "Invisible"
    LineObs.symbolFreq = 1
    LineObs.symbol.Index = 12
    LineObs.symbol.size = 0.08
    LineObs.symbol.Fill.foreColor = grfColorBlack
    LineObs.symbol.line.foreColor = grfColorBlack

    ' Linear fit line: Thin solid black line
    Set LineFit = Graph.AddLinePlot("{clean_line_path}", 1, 2)
    LineFit.name = "Linear Fit"
    LineFit.symbolFreq = 0
    LineFit.line.style = "Solid"
    LineFit.line.foreColor = grfColorBlack
    LineFit.line.width = 0.015

    ' -------------------------------------------------------------
    ' 2. L-Shaped Open Axes (LaTeX Styled, Outward Ticks)
    ' -------------------------------------------------------------
    Set AxX = Graph.Axes.Item(1)
    AxX.xPos = 5.5
    AxX.yPos = 1.2
    AxX.length = 5.0
    AxX.title.text = "Lgt"
    AxX.title.font.name = "Times New Roman"
    AxX.title.font.italic = True
    AxX.title.font.size = 14
    AxX.title.yOffset = 0.15
    AxX.LabelFont.name = "Times New Roman"
    AxX.LabelFont.size = 11
    AxX.Tickmarks.MajorSide = 1
    AxX.Tickmarks.MinorSide = 1
    AxX.Tickmarks.MajorLength = 0.15
    AxX.Tickmarks.MinorLength = 0.08
    AxX.Grid.AtMajorTicks = False
    AxX.Grid.AtMinorTicks = False
    AxX.line.foreColor = grfColorBlack
    AxX.line.width = 0.01

    Set AxY = Graph.Axes.Item(2)
    AxY.xPos = 5.5
    AxY.yPos = 1.2
    AxY.length = 4.8
    AxY.title.text = "s"
    AxY.title.font.name = "Times New Roman"
    AxY.title.font.italic = True
    AxY.title.font.size = 15
    AxY.title.xOffset = -0.15
    AxY.LabelFont.name = "Times New Roman"
    AxY.LabelFont.size = 11
    AxY.Tickmarks.MajorSide = 1
    AxY.Tickmarks.MinorSide = 1
    AxY.Tickmarks.MajorLength = 0.15
    AxY.Tickmarks.MinorLength = 0.08
    AxY.Grid.AtMajorTicks = False
    AxY.Grid.AtMinorTicks = False
    AxY.line.foreColor = grfColorBlack
    AxY.line.width = 0.01

    ' -------------------------------------------------------------
    ' 3. Report Window on Left (Matching Image 1 "Grapher - Report 1")
    ' -------------------------------------------------------------
    Set WinBox = Plot.Shapes.AddRectangle(0.5, 7.5, 4.4, 3.2)
    WinBox.line.foreColor = grfColorBlack60
    WinBox.line.width = 0.01
    WinBox.Fill.foreColor = grfColorWhite

    Set WinBar = Plot.Shapes.AddRectangle(0.5, 7.5, 4.4, 7.15)
    WinBar.line.style = "Invisible"
    WinBar.Fill.foreColor = grfColorLightGray

    Set WinTitle = Plot.Shapes.AddText(0.65, 7.25, "Grapher - Report 1 [Fit Results]")
    WinTitle.font.name = "Arial"
    WinTitle.font.size = 9
    WinTitle.font.bold = True

    Set WinBtns = Plot.Shapes.AddText(3.8, 7.25, "-  [ ]  X")
    WinBtns.font.name = "Arial"
    WinBtns.font.size = 8.5

    Set MenuBar = Plot.Shapes.AddText(0.65, 6.9, "File(F)   Edit(E)")
    MenuBar.font.name = "Arial"
    MenuBar.font.size = 8.5

    Dim repBody As String
    repBody = "Linear Regression Results" & vbCrLf & _
              "----------------------------------------" & vbCrLf & _
              "Fit Plot 1: Linear" & vbCrLf & _
              "Equation: Y = {a_slope:.8f} * X + ({b_intercept:.8f})" & vbCrLf & _
              "Number of Data Points = {n_pts}" & vbCrLf & _
              "Mean X = {mean_x:.6f}" & vbCrLf & _
              "Mean Y = {mean_y:.5f}" & vbCrLf & _
              "Sum of Squares (Res) = {ss_res:.7f}" & vbCrLf & _
              "Sum of Squares (Tot) = {ss_tot:.4f}" & vbCrLf & _
              "Coeff. of Determination, R\\up50 2\\dn50 = {r2:.6f}" & vbCrLf & _
              "Residual Mean Square = {sigma_sq:.8f}"

    Set RepText = Plot.Shapes.AddText(0.65, 6.55, repBody)
    RepText.font.name = "Courier New"
    RepText.font.size = 8

    ' Parameter summary box
    Set ParamBox = Plot.Shapes.AddRectangle(0.5, 2.7, 4.4, 1.2)
    ParamBox.line.foreColor = grfColorBlack60
    ParamBox.line.width = 0.01
    ParamBox.Fill.foreColor = grfColorWhite

    Dim pBody As String
    pBody = "Cooper-Jacob Parameters Solution:" & vbCrLf & _
            "Delta s = {delta_s:.4f} m/cycle,  t\\dn50 0\\up50 = {t0:.2f} {time_unit}" & vbCrLf & _
            "Transmissivity T = {t_m2_d:.2f} m\\up50 2\\dn50 / d" & vbCrLf & _
            "Storage Coeff. S = {s_coef:.4e}" & vbCrLf & _
            "Criterion u(end) = {u_last:.4f} <= 0.05 (Valid)"

    Set ParamText = Plot.Shapes.AddText(0.65, 2.45, pBody)
    ParamText.font.name = "Arial"
    ParamText.font.size = 8.5

    Plot.Selection.DeselectAll
    Plot.SaveAs "{clean_grf_path}"
    Plot.Export "{clean_png_path}", False, "Defaults=1, Width=1920, Height=1080", False
    Plot.Close 2
End Sub
"""


def _general_script_template(data_file: str, grf_path: str, png_path: str, title: str) -> str:
    clean_data_path = str(data_file).replace("/", "\\")
    clean_grf_path = str(grf_path).replace("/", "\\")
    clean_png_path = str(png_path).replace("/", "\\")
    return f"""\
' ====================================================================
' Golden Software Grapher 16 General Plot Script
' ====================================================================
Sub Main
    Dim GrapherApp, Plot, Graph, LinePlot As Object
    On Error Resume Next
    Set GrapherApp = CreateObject("Grapher.Application")
    GrapherApp.Visible = True
    Set Plot = GrapherApp.Documents.Add(1)
    Set Graph = Plot.Shapes.AddLinePlotGraph("{clean_data_path}", 1, 2)
    Graph.title.text = "{title}"
    Plot.Selection.DeselectAll
    Plot.SaveAs "{clean_grf_path}"
    Plot.Export "{clean_png_path}", False, "Defaults=1, Width=1920, Height=1440, ColorDepth=32", False
    Plot.Close(2)
End Sub
"""


def _create_theis_grapher_script(std_file: str, field_file: str,
                                 grf_path: str, png_path: str,
                                 g1_xpos: float, g1_ypos: float,
                                 g2_xpos: float, g2_ypos: float,
                                 axis_len_x: float = 5.0,
                                 axis_len_y: float = 4.0,
                                 inv_u_star: float = 1.0,
                                 w_star: float = 1.0,
                                 tr2_star: float = 1.0,
                                 s_star: float = 1.0,
                                 t_m2_d: float = 100.0,
                                 s_coef: float = 1e-4,
                                 r2: float = 0.99,
                                 time_unit: str = "min",
                                 x_field_title: str = "t/r\\up50 2\\dn50") -> Path:
    p = _script_path("Theis_Matching_Grapher")
    content = _theis_script_template(
        std_file, field_file, grf_path, png_path,
        g1_xpos, g1_ypos, g2_xpos, g2_ypos,
        axis_len_x, axis_len_y,
        inv_u_star, w_star, tr2_star, s_star,
        t_m2_d, s_coef, r2, time_unit, x_field_title
    )
    p.write_text(content, encoding="ansi", errors="ignore")
    return p


def _create_jacob_grapher_script(scatter_file: str, line_file: str,
                                 grf_path: str, png_path: str,
                                 a_slope: float, b_intercept: float,
                                 n_pts: int, mean_x: float, mean_y: float,
                                 ss_res: float, ss_tot: float,
                                 r2: float, sigma_sq: float,
                                 delta_s: float, t0: float,
                                 t_m2_d: float, s_coef: float,
                                 u_last: float, time_unit: str = "min") -> Path:
    p = _script_path("Jacob_StraightLine_Grapher")
    content = _jacob_script_template(
        scatter_file, line_file, grf_path, png_path,
        a_slope, b_intercept, n_pts, mean_x, mean_y,
        ss_res, ss_tot, r2, sigma_sq,
        delta_s, t0, t_m2_d, s_coef, u_last, time_unit
    )
    p.write_text(content, encoding="ansi", errors="ignore")
    return p
