"""环境与引擎定位：GMS / Visual MODFLOW 安装路径、可执行引擎注册表。

所有路径都可在 config/env.json 覆盖；找不到时回退到本机默认值。
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------- 默认配置

# 刻意留空：真实的安装路径请写进 config/env.json（该文件不纳入版本库，
# 见 config/env.example.json）。留空时下面的 _autodetect 会按环境变量
# 与常见安装位置尝试自动定位。
DEFAULT_CONFIG: dict = {
    "gms_home": "",
    "vmod_home": "",
    "workspace": "",                 # 空 = <repo>/workspace
    # 额外搜索 MODFLOW 引擎的目录（可选）
    "extra_engine_dirs": [],
}

_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parent.parent.parent          # <repo>/
CONFIG_PATH = _PKG_ROOT / "config" / "env.json"


def _autodetect(kind: str) -> str:
    """在环境变量与常见安装位置里定位 GMS / Visual MODFLOW 安装目录。

    优先级：环境变量 GMS_HOME / VMOD_HOME -> 常见安装目录（按标志 exe 判定）。
    找不到返回空串，由调用方给出提示。
    """
    var = "GMS_HOME" if kind == "gms_home" else "VMOD_HOME"
    hint = os.environ.get(var)
    if hint and Path(hint).is_dir():
        return str(Path(hint))
    if kind == "gms_home":
        pats = [r"C:\Program Files*\GMS*", r"D:\GMS*", r"E:\GMS*"]
        marker = "GMS10_4.exe"
    else:
        pats = [r"C:\Program Files*\Visual*MODFLOW*", r"D:\Visual*MODFLOW*",
                r"E:\Visual*MODFLOW*"]
        marker = "vmod.exe"
    for pat in pats:
        for d in glob.glob(pat):
            p = Path(d)
            if not p.is_dir():
                continue
            if (p / marker).exists():
                return str(p)
            hits = list(p.glob(marker))          # 标志 exe 在下一层的情况
            if hits:
                return str(hits[0].parent)
    return ""


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except Exception:
            pass
    for key in ("gms_home", "vmod_home", "workspace"):
        if cfg.get(key):
            cfg[key] = os.path.expandvars(str(cfg[key]))
    if not cfg.get("workspace"):
        cfg["workspace"] = str(_PKG_ROOT / "workspace")
    for key in ("gms_home", "vmod_home"):
        if not cfg.get(key):
            cfg[key] = _autodetect(key)
    return cfg


def save_config(cfg: dict) -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    return CONFIG_PATH


CONFIG = load_config()


def workspace() -> Path:
    p = Path(CONFIG["workspace"])
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- 引擎注册表

@dataclass
class Engine:
    key: str            # 逻辑名，如 "mf2005"
    name: str           # 显示名
    path: str           # exe 绝对路径
    kind: str           # "flow" | "transport" | "particle" | "budget" | "calibration"
    vendor: str         # "gms" | "vmod" | "extra"
    version: str = ""
    accepts_namefile: bool = True

    def exists(self) -> bool:
        return Path(self.path).exists()


# GMS 侧：在 models/ 下按文件名特征识别
_GMS_ENGINE_HINTS: list[tuple[str, str, str, str]] = [
    # (glob-ish 文件名包含, key, 显示名, kind)
    ("mf2005",        "mf2005",    "MODFLOW-2005 (GMS)",        "flow"),
    ("mf2k5",         "mf2005",    "MODFLOW-2005 (GMS)",        "flow"),
    ("mf2k",          "mf2000",    "MODFLOW-2000 (GMS)",        "flow"),
    ("MODFLOW-NWT",   "mfnwt",     "MODFLOW-NWT (GMS)",         "flow"),
    ("mfnwt",         "mfnwt",     "MODFLOW-NWT (GMS)",         "flow"),
    ("mf6",           "mf6",       "MODFLOW 6 (GMS)",           "flow"),
    ("mt3dms",        "mt3dms",    "MT3DMS (GMS)",              "transport"),
    ("mt3d-usgs",     "mt3dusgs",  "MT3D-USGS (GMS)",           "transport"),
    ("modpath",       "modpath",   "MODPATH (GMS)",             "particle"),
    ("zonbud",        "zonbud",    "Zone Budget (GMS)",         "budget"),
    ("pht3d",         "pht3d",     "PHT3D (GMS)",               "transport"),
]

# exe 名里出现这些词的一律跳过（GUI / 辅助工具，不是数值引擎）
_SKIP_TOKENS = (
    "dbl_64", "install", "setup", "uninstall", "test", "demo", "example",
    "convert", "check", "wizard", "to2k", "to2", "detect", "gsf2vtk",
)

# 同一逻辑引擎有多个构建时，用打分挑「最标准、最好用」的那个
def _engine_score(path: "Path") -> int:
    """给候选 exe 打分：原版 USGS 构建优先，CFP/HDF5/并行/双精度变种降权。"""
    s = 0
    name = path.name.lower()
    parts = [p.lower() for p in path.parts]
    if "usgs" in parts:                     # 原版 USGS 可执行：最通用
        s += 40
    if "cfp" in name:                       # 管道流过程模型：物理不同，别当默认
        s -= 60
    if "_h5" in name or "h5_" in name:      # GMS 的 HDF5 封装：需要额外配置
        s -= 25
    if "parallel" in name:                  # 并行版：启动依赖更多
        s -= 8
    if "dbl" in name:                       # 双精度版
        s -= 5
    if name.count("_") <= 1 and name.endswith(".exe"):
        s += 6                              # 名字最朴素的多半是标准构建
    return s



def _is_engine_exe(p: Path) -> bool:
    n = p.name.lower()
    if not n.endswith(".exe"):
        return False
    if any(t in n for t in _SKIP_TOKENS):
        return False
    return any(h.lower() in n for h, *_ in _GMS_ENGINE_HINTS)


def list_gms_engines() -> list[Engine]:
    """扫描 GMS models/ 目录，每个逻辑引擎只保留打分最高的那个构建。"""
    home = CONFIG.get("gms_home")
    if not home:
        return []
    models = Path(home) / "models"
    if not models.is_dir():
        return []
    best: dict[str, tuple[int, Engine]] = {}
    for p in sorted(models.rglob("*.exe")):
        if not _is_engine_exe(p):
            continue
        low = p.name.lower()
        match = next((h for h in _GMS_ENGINE_HINTS if h[0].lower() in low), None)
        if not match:
            continue
        _, key, name, kind = match
        score = _engine_score(p)
        if key not in best or score > best[key][0]:
            best[key] = (score, Engine(key=key, name=name, path=str(p), kind=kind,
                                       vendor="gms", version="", accepts_namefile=True))
    return [eng for _, eng in sorted(best.values(), key=lambda kv: -kv[0])]


# Visual MODFLOW 侧：安装目录里登记的引擎（文件名固定）
_VMOD_ENGINE_TABLE: list[tuple[str, str, str, str, str]] = [
    # (exe 文件名, key, 显示名, kind, version)
    ("Mf2k.exe",     "mf2000",  "MODFLOW-2000 (Visual MODFLOW)",   "flow",      "v1.12"),
    ("mflow96.exe",  "mf96",    "MODFLOW-96 (Visual MODFLOW)",     "flow",      "v3.2"),
    ("mt3dms.exe",   "mt3dms",  "MT3DMS (Visual MODFLOW)",         "transport", ""),
    ("mt3d96.exe",   "mt3d96",  "MT3D96 (Visual MODFLOW)",         "transport", ""),
    ("Mpath2k.exe",  "modpath", "MODPATH 3.2 (Visual MODFLOW)",    "particle",  "3.2"),
    ("zbud.exe",     "zonbud",  "Zone Budget (Visual MODFLOW)",    "budget",    ""),
    ("Pestrun.exe",  "pest",    "PEST (Visual MODFLOW)",           "calibration", ""),
]


def list_vmod_engines() -> list[Engine]:
    home = CONFIG.get("vmod_home")
    out: list[Engine] = []
    if not home:
        return out
    for exe, key, name, kind, ver in _VMOD_ENGINE_TABLE:
        p = Path(home) / exe
        if p.exists():
            out.append(Engine(key=key, name=name, path=str(p), kind=kind,
                              vendor="vmod", version=ver, accepts_namefile=True))
    return out


def list_extra_engines() -> list[Engine]:
    out: list[Engine] = []
    for d in CONFIG.get("extra_engine_dirs") or []:
        dd = Path(d)
        if not dd.is_dir():
            continue
        for p in sorted(dd.glob("*.exe")):
            out.append(Engine(key=p.stem.lower(), name=p.stem, path=str(p),
                              kind="flow", vendor="extra"))
    return out


def list_all_engines() -> list[Engine]:
    return list_vmod_engines() + list_gms_engines() + list_extra_engines()


def find_engine(key: str, vendor: str | None = None) -> Engine | None:
    """按逻辑名（可加 vendor 限定）找引擎。"""
    for e in list_all_engines():
        if e.key == key and (vendor is None or e.vendor == vendor):
            return e
    return None


def resolve_engine(prefer: str | None = None, kind: str = "flow",
                   vendor: str | None = None) -> Engine | None:
    """挑一个可用引擎：优先指名 -> 优先 vendor -> 第一个同 kind 的。"""
    engines = [e for e in list_all_engines() if e.kind == kind]
    if prefer:
        hit = find_engine(prefer, vendor)
        if hit and hit.kind == kind:
            return hit
    if vendor:
        vs = [e for e in engines if e.vendor == vendor]
        if vs:
            return vs[0]
    return engines[0] if engines else None


# ---------------------------------------------------------------- 软件探测

def gms_info() -> dict:
    home = Path(CONFIG.get("gms_home") or "")
    exe = home / "GMS10_4.exe"
    return {
        "home": str(home),
        "installed": home.is_dir(),
        "exe": str(exe) if exe.exists() else None,
        "exe_present": exe.exists(),
        "models_dir": str(home / "models"),
        "engines": [asdict(e) for e in list_gms_engines()],
        "has_xms_api": (home / "Python35" / "Lib" / "site-packages" / "xms_api.pyd").exists(),
        "note": "xms_api 必须在 GMS 进程运行时调用，无法脱离 GUI 独立跑。",
    }


def vmod_info() -> dict:
    home = Path(CONFIG.get("vmod_home") or "")
    exe = home / "vmod.exe"
    usr = home / "vmod.usr"
    edition = ""
    if usr.exists():
        try:
            lines = [x.strip() for x in usr.read_text("latin-1").splitlines() if x.strip()]
            edition = lines[-1] if lines else ""
        except Exception:
            pass
    return {
        "home": str(home),
        "installed": home.is_dir(),
        "exe": str(exe) if exe.exists() else None,
        "exe_present": exe.exists(),
        "build": "4.0.0.126 (2004-03)",
        "registration": edition,
        "tutorial_dir": str(home / "Tutorial"),
        "engines": [asdict(e) for e in list_vmod_engines()],
        "project_files": [".vmp (工程)", ".vmf (水流, XML)", ".vmt (运移)",
                          ".vmz (分区均衡)", ".mdb (Access 库)"],
        "note": "无对外 API；工程可经 .vmf(XML) + 标准 MODFLOW 文件程序化读写。",
    }


def find_grapher() -> tuple[Path | None, Path | None]:
    """定位 Golden Software Grapher 主程序与 Scripter 脚本执行引擎。

    查找顺序：config/env.json 的 ``grapher_home`` → 环境变量 ``GRAPHER_HOME``
    → 常见安装目录。返回 ``(Grapher.exe, Scripter.exe)``，未找到时为 ``(None, None)``。
    """
    candidates: list[Path] = []
    cfg_home = CONFIG.get("grapher_home")
    if cfg_home:
        candidates.append(Path(cfg_home))
    env_home = os.environ.get("GRAPHER_HOME")
    if env_home:
        candidates.append(Path(env_home))
    candidates.extend([
        Path(r"C:\Program Files\Golden Software\Grapher 16"),
        Path(r"C:\Program Files\Golden Software\Grapher"),
    ])
    for c in candidates:
        g = c / "Grapher.exe"
        s = c / "Scripter.exe"
        if g.exists() and s.exists():
            return g, s
    return None, None


def grapher_info() -> dict:
    """Golden Software Grapher 安装与脚本引擎探测结果。

    Grapher 不参与建模与求解，只作为后处理绘图后端：由 ``Scripter.exe``
    执行 ``.bas`` 脚本，产出 ``.grf`` 工程与 ``.png`` 图件。
    """
    grapher_exe, scripter_exe = find_grapher()
    home = grapher_exe.parent if grapher_exe else Path(CONFIG.get("grapher_home") or "")
    return {
        "home": str(home) if str(home) else None,
        "installed": bool(grapher_exe),
        "exe": str(grapher_exe) if grapher_exe else None,
        "scripter": str(scripter_exe) if scripter_exe else None,
        "role": "后处理绘图（不做数值计算）",
        "script_dir": str(workspace() / "grapher_scripts"),
        "note": "通过 Scripter.exe 驱动 .bas 脚本；调用前需启动主进程承载 COM 接口，用后回收进程。",
    }


def _scan_errors(stdout: str) -> list[str]:
    """从引擎输出里挑出真正的报错行（排除「no error」这类良性措辞）。"""
    bad_tokens = ("error", "failed", "cannot", "can't find", "abnormal",
                  "no convergence", "stop:")
    benign = ("no error", "no errors", "0 error", "error count: 0",
              "can't find name file")   # 「找不到 NAME FILE」由重试逻辑处理
    hits = []
    for ln in stdout.splitlines():
        s = ln.strip()
        low = s.lower()
        if not any(t in low for t in bad_tokens):
            continue
        if any(b in low for b in benign):
            continue
        hits.append(s)
    return hits[-8:]


def run_engine(engine: Engine, namefile: str | Path, cwd: str | Path | None = None,
               timeout: int | None = None) -> dict:
    """用引擎跑一个 NAME FILE，返回 stdout/stderr/退出码。

    老式 USGS 引擎（MODFLOW-96/2000/2005）**从标准输入读 NAME FILE 名**，
    不接受命令行参数——VMod 的 Mf2k.exe 甚至会把 argv 当成「工程名前缀」。
    因此这里先按 stdin 方式跑，失败再退回命令行参数方式。
    MF6 则相反：无参数在当前目录找 mfsim.nam，或直接用参数指定。
    """
    namefile = Path(namefile)
    if not namefile.exists():
        return {"ok": False, "error": f"NAME FILE 不存在: {namefile}"}
    wd = Path(cwd) if cwd else namefile.parent

    def _run(argv: list[str], stdin: bytes) -> subprocess.CompletedProcess | dict:
        try:
            return subprocess.run(
                [str(engine.path), *argv], cwd=str(wd),
                capture_output=True, timeout=timeout, input=stdin,
            )
        except subprocess.TimeoutExpired as exc:
            return {"_timeout": True,
                    "stdout": (exc.stdout or b"").decode("utf-8", "replace")[-4000:]}
        except OSError as exc:
            return {"_error": f"无法启动引擎: {exc}"}

    if engine.key in ("mf6", "mfusg"):
        attempts = [([namefile.name], b""), ([], b"")]
    else:
        attempts = [([], (namefile.name + "\n\n").encode()),
                    ([namefile.name], b"\n")]

    last = None
    for argv, stdin in attempts:
        proc = _run(argv, stdin)
        if isinstance(proc, dict):
            return {"ok": False, "error": proc.get("_error") or f"运行超时（>{timeout}s）",
                    "stdout_tail": proc.get("stdout", "")}
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        low = out.lower()
        errors = _scan_errors(out)
        last = {
            "ok": True,
            "engine": engine.name,
            "engine_path": engine.path,
            "vendor": engine.vendor,
            "mode": "stdin" if not argv else "argv",
            "returncode": proc.returncode,
            "stdout_tail": out[-6000:],
            "stderr_tail": err[-2000:],
            "converged": not any(k in low for k in
                                 ("did not converge", "failed to converge",
                                  "no convergence", "failed to meet solver")),
            "normal_termination": "normal termination" in low,
            "errors": errors[-8:],
        }
        # 文件没找到 -> 换另一种调用方式再试
        if "can't find name file" in low or "cannot find name file" in low:
            continue
        break
    return last or {"ok": False, "error": "引擎未产生输出"}


def which_python() -> str:
    return shutil.which("python") or ""
