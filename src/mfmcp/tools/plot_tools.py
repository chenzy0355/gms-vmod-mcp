"""出图工具：水头/降深平面图、时间序列、剖面对比。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .. import env, state
from ..adapters import mfmodel


def _fig_path(tag: str, ext: str = "png") -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = env.workspace() / "figs"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{tag}-{ts}.{ext}"


def register(mcp) -> None:

    @mcp.tool()
    def mfm_plot_map(alias: str, array: str = "head", layer: int = 0, kper: int = -1,
                     cmap: str = "viridis", contour: bool = True) -> dict:
        """画平面等值线图。array 取 'head'(水头) / 'drawdown'(降深) / 或模型数组名如 'k'。"""
        model = state.get(alias)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        data, label = _extract(model, array, layer, kper)
        mg = model.modelgrid if not hasattr(model, "model_names") else model.get_model(
            model.model_names[0]).modelgrid
        try:
            ext = mg.extent
        except Exception:
            ext = None
        fig, ax = plt.subplots(figsize=(8, 6.5), dpi=150)
        im = ax.imshow(data, cmap=cmap, extent=ext)
        if contour:
            try:
                cs = ax.contour(data, levels=8, colors="white", linewidths=0.5,
                                extent=ext, alpha=0.6)
                ax.clabel(cs, inline=True, fontsize=7, fmt="%.1f")
            except Exception:
                pass
        cb = fig.colorbar(im, ax=ax, shrink=0.85)
        cb.set_label(label, fontsize=10)
        ax.set_title(f"{array} | layer={layer} | kper={kper}", fontsize=11)
        ax.set_xlabel("X"); ax.set_ylabel("Y")
        fig.tight_layout()
        p = _fig_path(f"{Path(alias).stem}-{array}-L{layer}")
        fig.savefig(p); plt.close(fig)
        return {"figure": str(p), "array": array, "layer": layer, "kper": kper,
                "min": float(np.nanmin(data)), "max": float(np.nanmax(data)),
                "shape": list(np.shape(data))}

    @mcp.tool()
    def mfm_plot_timeseries(alias: str, row: int = 0, col: int = 0, layer: int = 0) -> dict:
        """画指定格点的水头过程线（跨所有应力期）。"""
        model = state.get(alias)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from flopy.utils import HeadFile

        m = model if not hasattr(model, "model_names") else model.get_model(model.model_names[0])
        cand = Path(m.model_ws) / f"{m.name}.hds"
        if not cand.exists():
            return {"error": f"找不到水头文件：{cand}"}
        hf = HeadFile(str(cand))
        series, times = [], []
        for t in hf.get_times():
            a = hf.get_data(totim=t)
            series.append(float(a[layer][row][col]))
            times.append(float(t))
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        ax.plot(times, series, marker="o", ms=3)
        ax.set_xlabel("Time"); ax.set_ylabel("Head")
        ax.set_title(f"{alias} head at (L{layer}, {row}, {col})", fontsize=11)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        p = _fig_path(f"{Path(alias).stem}-ts-L{layer}-{row}-{col}")
        fig.savefig(p); plt.close(fig)
        return {"figure": str(p), "times": times, "values": series}

    @mcp.tool()
    def mfm_plot_compare(alias_a: str, alias_b: str, array: str = "drawdown",
                         layer: int = 0, kper: int = -1) -> dict:
        """对比两个已加载模型的结果（默认降深），画并排图并给出差值统计。"""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        a = _extract(state.get(alias_a), array, layer, kper)[0]
        b = _extract(state.get(alias_b), array, layer, kper)[0]
        d = np.asarray(a, float) - np.asarray(b, float)
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), dpi=150)
        for ax, dat, t in zip(axes, (a, b, d), (alias_a, alias_b, "A - B")):
            im = ax.imshow(np.asarray(dat, float), cmap="RdBu_r" if t == "A - B" else "viridis")
            ax.set_title(f"{t} ({array})", fontsize=10)
            fig.colorbar(im, ax=ax, shrink=0.85)
        fig.tight_layout()
        p = _fig_path(f"compare-{array}")
        fig.savefig(p); plt.close(fig)
        return {"figure": str(p), "diff_min": float(np.nanmin(d)),
                "diff_max": float(np.nanmax(d)), "diff_mean": float(np.nanmean(d))}


def _extract(model, array: str, layer: int, kper: int):
    import numpy as np
    key = array.lower()
    if key in ("head", "水头"):
        data = mfmodel.read_heads(model, kper=kper)["result"][0]["values"]
        arr = np.asarray(data, float)
        if arr.ndim == 3:
            arr = arr[layer]
        return arr, "Head"
    if key in ("drawdown", "降深"):
        data = mfmodel.drawdown(model, kper=kper)["result"][0]["values"]
        arr = np.asarray(data, float)
        if arr.ndim == 3:
            arr = arr[layer]
        return arr, "Drawdown"
    rec = mfmodel.get_array(model, key, layer=layer if layer >= 0 else None)
    return np.asarray(rec["values"], float), key
