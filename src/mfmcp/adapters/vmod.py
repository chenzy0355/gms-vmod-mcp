"""Visual MODFLOW 适配器：读写 .vmf（XML 工程描述）与标准 MODFLOW 文件。

Visual MODFLOW 4.0 工程由三部分组成：
  * ``<name>.vmp``  —— 工程索引
  * ``<name>.vmf``  —— 水流模型（XML），含网格、包、引擎、层参数
  * ``<name>.mdb``  —— Access 数据库（图形/几何数据）
真正能被程序安全读写的是 ``.vmf``；几何细节在 ``.mdb`` 里，不做写入。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

VMOD_SUFFIXES = {".vmf": "flow", ".vmt": "transport", ".vmz": "zonebudget",
                 ".vmp": "project", ".vmw": "?"}

# 这几类参数允许被 AI 改写（其余只读，避免破坏工程）
_WRITABLE_TYPES = {"Int", "Float", "Bool", "String", "List"}


class VModError(RuntimeError):
    pass


# ---------------------------------------------------------------- 加载

def load_vmf(path: str | Path) -> ET.ElementTree:
    p = Path(path)
    if not p.exists():
        raise VModError(f".vmf 不存在: {p}")
    try:
        return ET.parse(str(p))
    except ET.ParseError as exc:
        raise VModError(f".vmf XML 解析失败: {exc}") from exc


def save_vmf(tree: ET.ElementTree, path: str | Path, backup: bool = True) -> Path:
    p = Path(path)
    if backup and p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
    tree.write(str(p), encoding="utf-8", xml_declaration=False)
    return p


def parse_vmod_header(path: str | Path) -> str:
    """取出 ``<?VMod 4.0.0.126?>`` 里的版本串。"""
    try:
        head = Path(path).read_text("utf-8", errors="ignore")[:64]
    except Exception:
        return ""
    m = re.search(r"<\?VMod\s+([\d.]+)\?>", head)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 发现工程

def find_projects(root: str | Path, max_depth: int = 4) -> list[dict]:
    """在目录树里找 Visual MODFLOW 工程（以 .vmf 为准）。"""
    root = Path(root)
    out: list[dict] = []
    if not root.exists():
        return out
    for vmf in root.rglob("*.vmf"):
        if len(vmf.relative_to(root).parts) > max_depth:
            continue
        stem = vmf.with_suffix("")
        siblings = {s: (stem.with_suffix(s).exists() or (stem.parent / (stem.name + s)).exists())
                    for s in (".vmp", ".vmt", ".vmz", ".mdb")}
        out.append({
            "kind": "visual-modflow",
            "name": vmf.stem,
            "dir": str(vmf.parent),
            "vmf": str(vmf),
            "files": {k: v for k, v in siblings.items() if v},
            "size_kb": round(vmf.stat().st_size / 1024, 1),
        })
    return out


# ---------------------------------------------------------------- 工程概览

def _attr(el: ET.Element, *names, default=None):
    for n in names:
        if n in el.attrib:
            return el.attrib[n]
    return default


def project_info(vmf_path: str | Path) -> dict:
    tree = load_vmf(vmf_path)
    root = tree.getroot()

    grid = root.find("Grid")
    gridinfo = dict(grid.attrib) if grid is not None else {}
    try:
        nlay = int(gridinfo.get("Layers", 0))
        nrow = int(gridinfo.get("Rows", 0))
        ncol = int(gridinfo.get("Columns", 0))
        ncells = nlay * nrow * ncol
    except ValueError:
        nlay = nrow = ncol = ncells = 0

    cur = root.find("Current")
    engines: dict[str, str] = {}
    for tag in ("Engine",):
        for el in root.iter(tag):
            nm = _attr(el, "Name", default="")
            if nm and nm not in engines:
                engines[nm] = _attr(el, "EXE_Name", default="")

    variants: list[dict] = []
    for flow in root.findall(".//Flow/Variant"):
        eng = flow.find("Engine")
        pk = flow.find("Packages")
        variants.append({
            "name": _attr(flow, "Name", default=""),
            "engine": _attr(eng, "Name", default="") if eng is not None else "",
            "exe": _attr(eng, "EXE_Name", default="") if eng is not None else "",
            "packages": [i.attrib.get("Name", "") for i in (pk if pk is not None else [])
                         if i.attrib.get("Name")],
        })

    geo = root.find("Georeference")
    maps = [m.attrib.get("Source_Name", "") for m in root.findall(".//Maps/Map")]

    pkgs_el = root.find(".//Packages")

    return {
        "kind": "visual-modflow",
        "vmf": str(vmf_path),
        "vmod_version": parse_vmod_header(vmf_path),
        "project_name": _attr(root, "Name", default=""),
        "model_run": _attr(root, "Model_Run", default=""),
        "grid": {"layers": nlay, "rows": nrow, "cols": ncol, "cells": ncells,
                 "raw": gridinfo},
        "georeference": dict(geo.attrib) if geo is not None else {},
        "current": dict(cur.attrib) if cur is not None else {},
        "engines": engines,
        "flow_variants": variants,
        "maps": [m for m in maps if m],
        "layer_types": pkgs_el.attrib.get("Layer_Types", "") if pkgs_el is not None else "",
    }


# ---------------------------------------------------------------- 参数读写

def _walk(el: ET.Element, path: str = "") -> Iterator[tuple[ET.Element, str]]:
    """深度优先遍历，同时携带每个节点的标签路径（标准库 ElementTree 无 getparent）。"""
    nm = el.attrib.get("Name") or el.attrib.get("Name_Short")
    seg = f"{el.tag}[@Name='{nm}']" if nm else el.tag
    here = f"{path}/{seg}"
    yield el, here
    for child in el:
        yield from _walk(child, here)


def iter_params(vmf_path: str | Path) -> Iterator[dict]:
    """遍历 .vmf 中所有带 Value 的参数节点，产出扁平记录。"""
    tree = load_vmf(vmf_path)
    for el, xpath in _walk(tree.getroot()):
        name = el.attrib.get("Name")
        if not name or "Value" not in el.attrib:
            continue
        units = ""
        u = el.find(".//Units")
        if u is not None:
            units = u.attrib.get("Short_Name") or u.attrib.get("Default_Name") or ""
        yield {
            "name": name,
            "key": el.attrib.get("Key", ""),
            "value": el.attrib.get("Value", ""),
            "type": el.attrib.get("Type", ""),
            "units": units,
            "description": el.attrib.get("Description", ""),
            "short_name": el.attrib.get("Short_Name", ""),
            "options": el.attrib.get("Option", ""),
            "writable": el.attrib.get("Type", "") in _WRITABLE_TYPES,
            "path": xpath,
        }


def search_params(vmf_path: str | Path, query: str, limit: int = 50) -> list[dict]:
    """按 Name / Description / Short_Name 子串模糊查参数（不区分大小写）。"""
    q = query.lower()
    hits = []
    for rec in iter_params(vmf_path):
        hay = " ".join(str(rec.get(k, "")) for k in
                       ("name", "short_name", "description", "key")).lower()
        if q in hay:
            hits.append(rec)
        if len(hits) >= limit:
            break
    return hits


def _find_node(tree: ET.ElementTree, name: str, key: str | None = None) -> ET.Element | None:
    best = None
    for el in tree.getroot().iter():
        if el.attrib.get("Name") != name:
            continue
        if key and el.attrib.get("Key") != key:
            continue
        if "Value" in el.attrib:
            return el
        best = best or el
    return best


def set_params(vmf_path: str | Path, edits: list[dict], out_path: str | Path | None = None,
               dry_run: bool = False) -> dict:
    """批量改参数。

    edits: ``[{"name": "MXITER", "key": "0001.0002.0001", "value": "500"}, ...]``
    ``path``（iter_params 返回的 path）优先于 name/key 定位，最精确。
    """
    tree = load_vmf(vmf_path)
    by_path = {p: el for el, p in _walk(tree.getroot())}
    applied, failed = [], []
    for ed in edits:
        name = str(ed.get("name", "")).strip()
        if not name:
            failed.append({"edit": ed, "reason": "缺少 name"})
            continue
        if ed.get("path"):
            node = by_path.get(str(ed["path"]))
        else:
            node = _find_node(tree, name, ed.get("key") or None)
        if node is None:
            failed.append({"edit": ed, "reason": "未找到该参数"})
            continue
        old = node.attrib.get("Value", "")
        ptype = node.attrib.get("Type", "")
        if ptype not in _WRITABLE_TYPES:
            failed.append({"edit": ed, "reason": f"类型 {ptype} 不允许写入"})
            continue
        new = str(ed.get("value"))
        if ptype == "Int":
            try:
                new = str(int(float(new)))
            except ValueError:
                failed.append({"edit": ed, "reason": "需要整数"})
                continue
        elif ptype == "Float":
            try:
                new = repr(float(new))
            except ValueError:
                failed.append({"edit": ed, "reason": "需要数值"})
                continue
        node.attrib["Value"] = new
        applied.append({"name": name, "key": node.attrib.get("Key", ""),
                        "old": old, "new": new, "type": ptype})

    result = {"applied": applied, "failed": failed, "changed": bool(applied)}
    if dry_run or not applied:
        result["saved"] = None
        return result
    target = Path(out_path) if out_path else Path(vmf_path)
    result["saved"] = str(save_vmf(tree, target))
    return result


# ---------------------------------------------------------------- 变体/引擎切换

def set_engine(vmf_path: str | Path, engine_name: str,
               out_path: str | Path | None = None) -> dict:
    """切换水流引擎（写 <Current Flow_Engine> 与 <Flow/Variant>/<Engine>）。"""
    tree = load_vmf(vmf_path)
    root = tree.getroot()
    node = None
    for el in root.iter("Engine"):
        if _attr(el, "Name") == engine_name or _attr(el, "Short_Name") == engine_name:
            node = el
            break
    if node is None:
        avail = sorted({_attr(e, "Name", default="") for e in root.iter("Engine")})
        return {"ok": False, "error": f"未找到引擎 {engine_name}", "available": avail}
    cur = root.find("Current")
    if cur is not None:
        cur.set("Flow_Engine", engine_name)
    target = Path(out_path) if out_path else Path(vmf_path)
    save_vmf(tree, target)
    return {"ok": True, "engine": engine_name,
            "exe": _attr(node, "EXE_Name", default=""), "saved": str(target)}


# ---------------------------------------------------------------- 标准 MODFLOW 文件

def find_modflow_files(root: str | Path, limit: int = 40) -> list[dict]:
    """找目录下像是 MODFLOW 模型的 NAME FILE（.nam / VMod 的 .mfi）及配套文件。"""
    root = Path(root)
    out = []
    if not root.exists():
        return out
    seen: set[Path] = set()
    for pat in ("*.nam", "*.mfi"):
        for nam in list(root.rglob(pat))[:limit]:
            if nam in seen:
                continue
            seen.add(nam)
            stem = nam.with_suffix("")
            fam = {}
            for suf in (".dis", ".bas6", ".bas", ".bcf6", ".bcf", ".lpf", ".upw",
                        ".wel", ".riv", ".rch", ".ghb", ".chd", ".drn", ".evt",
                        ".hds", ".bud", ".lst", ".oc", ".mfi"):
                for cand in (stem.with_suffix(suf), stem.parent / (stem.name + suf),
                             stem.parent / (stem.name + suf.upper())):
                    if cand.exists():
                        fam[suf] = cand.name
                        break
            out.append({"kind": "modflow", "name": nam.stem,
                        "namefile_kind": "vmod-mfi" if nam.suffix == ".mfi" else "standard-nam",
                        "dir": str(nam.parent), "nam": str(nam), "files": fam})
    # 没有 NAME FILE 时退而求其次找 .dis
    if not out:
        for dis in list(root.rglob("*.dis"))[:limit]:
            out.append({"kind": "modflow-partial", "name": dis.stem,
                        "dir": str(dis.parent), "nam": None, "dis": str(dis)})
    return out


# ---------------------------------------------------------------- VMod NAME FILE

def read_namefile(path: str | Path) -> list[dict]:
    """解析 NAME FILE（标准 .nam 或 VMod 的 .mfi），返回各条记录。

    每条：``{"type": "LPF", "unit": 33, "path": "...", "name": "model.LPF",
    "exists": True, "external": True}``（external=路径指向本机以外的原始安装位置）。
    """
    p = Path(path)
    recs: list[dict] = []
    for line in p.read_text("latin-1", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # 形如:  LPF  33  C:\path\to\X.LPF
        m = re.match(r"^(\S+)\s+(\d+)\s+(.+?)\s*$", s)
        if not m:
            # 也可能是:  DIS  34  X.DIS
            m2 = re.match(r"^(\S+)\s+(\S+)\s+(.+?)\s*$", s)
            if not m2:
                recs.append({"raw": s, "type": None})
                continue
            ftype, unit, fpath = m2.group(1), m2.group(2), m2.group(3)
        else:
            ftype, unit, fpath = m.group(1), int(m.group(2)), m.group(3)
        fname = Path(fpath.replace("\\", "/")).name
        local = (p.parent / fname)
        recs.append({
            "type": ftype, "unit": unit, "path": fpath.strip(),
            "name": fname, "exists": local.exists(),
            "external": not Path(fpath.replace("\\", "/")).is_absolute()
                        or str(Path(fpath.replace("\\", "/")).parent).lower()
                           != str(p.parent).lower(),
        })
    return recs


def portable_namefile(namefile: str | Path, out_dir: str | Path | None = None,
                      out_name: str | None = None, dry_run: bool = False) -> dict:
    """把 NAME FILE 里的绝对路径改成「就地（同名目录）」，使其可在本机直接运行。

    VMod 的 ``.mfi`` 里写的是安装时的绝对路径（如 ``C:\\VMODNT\\Tutorial\\X.LPF``），
    换机后就跑不动。本函数把它改写成同目录下的文件名（写到 out_dir 时复制一份）。
    """
    src = Path(namefile)
    recs = read_namefile(src)
    d = Path(out_dir) if out_dir else src.parent
    target = (d / (out_name or src.name))
    missing = [r["name"] for r in recs if r.get("name") and not r["exists"]
               and (src.parent / r["name"]).exists() is False]
    lines: list[str] = []
    for r in recs:
        if not r.get("type"):
            if r.get("raw"):
                lines.append(r["raw"])
            continue
        unit = f"{r['unit']:>6}" if isinstance(r["unit"], int) else f"{str(r['unit']):>6}"
        lines.append(f"{r['type']:<8}{unit} {r['name']}")
    body = "\n".join(lines) + "\n"
    result = {
        "source": str(src), "target": str(target),
        "records": len([r for r in recs if r.get("type")]),
        "localized": sum(1 for r in recs if r.get("external")),
        "missing_local": [r["name"] for r in recs
                          if r.get("name") and not (src.parent / r["name"]).exists()],
        "preview": body,
    }
    if dry_run:
        return result
    d.mkdir(parents=True, exist_ok=True)
    target.write_text(body, "latin-1")
    result["written"] = str(target)
    return result

