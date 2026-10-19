#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nbkit —— 把课程实验课件复刻成本人 notebook 并出中文 PDF 的固定动作。

设计约束：本文件不含任何个人信息与机器路径。"谁交付、课件在哪、成果放哪、用哪个解释器、
用什么打印 PDF" 全部来自环境档案 profile.json，查找顺序见 profile_path()。
档案缺字段时函数抛 ProfileError 并附标准题面——照题面问用户，不要猜路径。

两种用法，任选：

  # A) 命令行（推荐，任何 agent / shell 都能驱动，不需要 import）
  python nbkit.py doctor
  python nbkit.py init --student-id 20240001 --student-name Zhang San \\
      --course ml-lab --materials-dir ~/courseware --output-dir ~/work/ml \\
      --python "$(which python3)"
  python nbkit.py check ml-lab
  python nbkit.py build cells.json draft.ipynb --course ml-lab
  python nbkit.py run draft.ipynb --course ml-lab
  python nbkit.py audit draft.ipynb
  python nbkit.py deliver draft.ipynb "实验四-逻辑回归" --course ml-lab
  python nbkit.py pdf "20240001-Zhang San-实验四-逻辑回归.ipynb" --course ml-lab

  # B) Python API（在构建脚本里）
  import nbkit
  nbkit.preflight("ml-lab")
  cells = [nbkit.MD("## 一、导包"), nbkit.CODE("import pandas as pd")]
  out = nbkit.deliver(nbkit.build(cells, "draft.ipynb", course="ml-lab"), "实验四", course="ml-lab")
  nbkit.to_pdf(out, course="ml-lab")

cells.json 格式：[{"type": "md"|"code", "source": "..."}]，或 {"cells": [...]} 同结构。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_PROFILE = os.path.join(os.path.expanduser("~"), ".nbkit", "profile.json")
LEGACY_PROFILE = os.path.join(SKILL_DIR, "profile.json")
EXAMPLE_PROFILE = os.path.join(SKILL_DIR, "profile.example.json")
DEFAULT_PATTERN = "{id}-{name}-{exp}"

# 标准提问清单：顺序即询问顺序，一次问全，不要挤牙膏式追问。
# 只列"必须人给"的字段；有默认值或能自动探测的（文件名模板、kernelspec、浏览器）不进清单。
GLOBAL_ASK = [
    ("student.id", "署名第一段（学号 / 工号 / 昵称，交付文件名用）"),
    ("student.name", "署名第二段（姓名）"),
]
COURSE_ASK = [
    ("materials_dirs", "课件/讲义所在目录（绝对路径，可多个）"),
    ("data_search_dirs", "去哪些目录找课件点名的数据集（绝对路径，可多个）"),
    ("output_dir", "成果输出目录（绝对路径，ipynb / pdf / data 都落这里）"),
    ("python", "执行 notebook 的 Python 解释器（绝对路径）"),
]
# 真正跑不动才拦；课件目录缺了只是提示，因为读课件是人的活
HARD_MISSING = ("student.id", "student.name", "output_dir")


class ProfileError(RuntimeError):
    """档案缺字段。异常文本就是给用户的提问清单。"""


# --------------------------------------------------------------------------- 档案

def profile_path() -> str:
    """档案查找顺序：$NBKIT_PROFILE > ~/.nbkit/profile.json > <技能目录>/profile.local.json
    > <技能目录>/profile.json。都不存在时返回用户级路径（新档案写在那里，不会污染仓库）。"""
    env = os.environ.get("NBKIT_PROFILE")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for cand in (USER_PROFILE, os.path.join(SKILL_DIR, "profile.local.json"), LEGACY_PROFILE):
        if os.path.exists(cand):
            return cand
    return USER_PROFILE


def load_profile(path: str | None = None) -> dict:
    p = path or profile_path()
    if not os.path.exists(p):
        return {}
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def save_profile(updates: dict, path: str | None = None) -> str:
    """把问到的值深合并写回档案。同一个字段下次只复述确认，不再重复问。"""
    p = path or profile_path()
    prof = load_profile(p)
    _merge(prof, updates)
    prof["updated_at"] = __import__("datetime").date.today().isoformat()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"profile updated -> {p}")
    return p


def _dig(prof: dict, dotted: str):
    cur = prof
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def course_key(prof: dict | None = None, course: str | None = None) -> tuple[str, dict]:
    prof = prof if prof is not None else load_profile()
    courses = prof.get("courses", {}) or {}
    key = course or prof.get("default_course") or (list(courses)[0] if len(courses) == 1 else None)
    if not key:
        raise ProfileError("档案里没有 default_course，也没传 course：先问用户这是哪门课/哪个项目。")
    data = courses.get(key)
    if data is None:
        have = ", ".join(courses) or "（还没有任何条目）"
        raise ProfileError(
            f"档案里没有 '{key}'，已有：{have}。\n"
            f"问用户这门课的四条路径后落盘：\n"
            f"  python nbkit.py init --course {key} --materials-dir ... --data-dir ... "
            f"--output-dir ... --python ...\n"
            f"  或 nbkit.save_profile({{'courses': {{'{key}': {{...}}}}}})"
        )
    return key, data


def resolve(course: str | None = None, prof: dict | None = None) -> dict:
    """把档案摊平成一次交付要用的参数；HARD_MISSING 缺了就抛，附标准题面。"""
    prof = prof if prof is not None else load_profile()
    key, c = course_key(prof, course)
    vals = {
        "course": key,
        "course_label": c.get("label", key),
        "student_id": _dig(prof, "student.id"),
        "student_name": _dig(prof, "student.name"),
        "pattern": _dig(prof, "deliver_pattern") or DEFAULT_PATTERN,
        "materials_dirs": _as_list(c.get("materials_dirs")),
        "data_search_dirs": _as_list(c.get("data_search_dirs")),
        "output_dir": c.get("output_dir"),
        "python": c.get("python") or sys.executable,
        "kernel": c.get("kernel", "python3"),
        "browser": c.get("browser") or _dig(prof, "tools.print_pdf.path"),
    }
    keys = {"student.id": "student_id", "student.name": "student_name", "output_dir": "output_dir"}
    missing = [f for f in HARD_MISSING if not vals.get(keys[f])]
    if missing:
        raise ProfileError(ask_text(course, prof, missing))
    return vals


def _as_list(v) -> list:
    if not v:
        return []
    return [os.path.expanduser(str(x)) for x in (v if isinstance(v, list) else [v])]


def missing_fields(course: str | None = None, prof: dict | None = None) -> list[tuple[str, str]]:
    prof = prof if prof is not None else load_profile()
    out = [(d, label) for d, label in GLOBAL_ASK if not _dig(prof, d)]
    key = course or prof.get("default_course")
    courses = prof.get("courses", {}) or {}
    if not key and len(courses) == 1:
        key = list(courses)[0]
    if not key:
        return [("default_course", "本次是哪门课/哪个项目（新条目还要问它的课件目录、数据目录、输出目录、解释器）")] + out
    c = courses.get(key) or {}   # 条目还没登记时，四条路径逐条问，不要只回问条目名
    for field, label in COURSE_ASK:
        if not c.get(field):
            out.append((f"courses.{key}.{field}", label))
    return out


def ask_text(course: str | None = None, prof: dict | None = None, only: list[str] | None = None) -> str:
    """现成的提问文案：直接拿去向用户提问，不要自己编字段名，更不要猜路径。"""
    miss = [(d, t) for d, t in missing_fields(course, prof) if only is None or d in only]
    if not miss:
        return "档案完整，无需提问。"
    lines = [f"档案 {profile_path()} 缺失以下字段，逐项向用户确认（已有同类路径作为候选项，不要替他决定）："]
    for i, (dotted, label) in enumerate(miss, 1):
        lines.append(f"  {i}. {label}  [字段 {dotted}]")
    lines.append("问到后落盘：python nbkit.py set 字段=值；未确认的目录不要拿同名/相邻目录兜底。")
    return "\n".join(lines)


def preflight(course: str | None = None, prof: dict | None = None) -> str:
    """执行前摊开本次要用的每一项，并检查目录是否真的存在。这一步不能跳——路径写错往往在成果落盘后才发现。"""
    prof = prof if prof is not None else load_profile()
    miss = missing_fields(course, prof)
    if miss:
        raise ProfileError(ask_text(course, prof))
    v = resolve(course, prof)
    dead = [p for p in v["materials_dirs"] + v["data_search_dirs"] if not os.path.isdir(p)]
    report = [
        f"档案     {profile_path()}",
        f"署名     {v['student_id']}-{v['student_name']}  (文件名模板 {v['pattern']})",
        f"条目     {v['course']} / {v['course_label']}",
        f"输出目录 {v['output_dir']}  [{'存在' if os.path.isdir(v['output_dir']) else '待创建'}]",
        f"课件目录 {'; '.join(v['materials_dirs']) or '（未登记）'}",
        f"找数据   {'; '.join(v['data_search_dirs']) or '（未登记）'}",
        f"解释器   {v['python']}  [{'存在' if os.path.exists(v['python']) else '不存在'}]",
        f"PDF 引擎 {v['browser'] or '（档案未记录，打印时自动探测 Chrome/Edge）'}",
    ]
    print("\n".join(report))
    if dead:
        print("注意：档案里这些目录当前不存在（外接盘没插 / 路径过期 / 改名），开跑前先跟用户核一句：\n  " + "\n  ".join(dead))
    return "\n".join(report)


# --------------------------------------------------------------------------- 环境与体检

def find_browser() -> str | None:
    """PATH 上的 chrome/msedge/chromium，再看各平台标准安装位。"""
    for exe in ("chrome", "msedge", "chromium", "chromium-browser", "google-chrome"):
        hit = shutil.which(exe)
        if hit:
            return hit
    candidates = [
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/microsoft-edge",
        "/usr/bin/chromium",
    ]
    return next((c for c in candidates if os.path.exists(c)), None)


def cjk_fonts() -> list[str]:
    """列出本机 matplotlib 能用的中文字体。没有中文字体时 PDF 里的图会全是方框。"""
    try:
        from matplotlib import font_manager
    except Exception:
        return []
    wanted = ("SimHei", "Microsoft YaHei", "微软雅黑", "Noto Sans CJK", "Source Han Sans",
              "WenQuanYi", "PingFang SC", "Heiti SC", "Songti SC", "SimSun", "FangSong", "KaiTi")
    found = []
    for f in font_manager.fontManager.ttflist:
        name = f.name
        if any(w.lower() in name.lower() for w in wanted) and name not in found:
            found.append(name)
    return found


def font_cell(family: str | None = None) -> str:
    """返回一段可直接放进 notebook 的代码：设置中文字体。不传 family 就自动挑。"""
    fam = family or (cjk_fonts() or ["SimHei"])[0]
    return (f"import matplotlib.pyplot as plt\n"
            f"plt.rcParams['font.sans-serif'] = ['{fam}']\n"
            f"plt.rcParams['axes.unicode_minus'] = False")


def ensure_kernel(python: str, base_name: str = "python3") -> tuple[str, str]:
    """为目标解释器造一个临时 kernelspec，返回 (kernel_name, JUPYTER_PATH)。

    为什么需要：nbconvert --execute 是按 kernelspec 名找内核的，只换跑 nbconvert 的 python
    并不会换掉 notebook 真正用的解释器——多环境机器上这会静默跑在另一个环境里。
    JUPYTER_PATH 必须是 kernels 目录的父目录，少一层就报 NoSuchKernel。
    """
    chk = subprocess.run([python, "-c", "import ipykernel"], capture_output=True, text=True)
    if chk.returncode != 0:
        raise RuntimeError(
            f"{python} 里没有 ipykernel，无法用它执行 notebook。\n"
            f"先问用户是否允许安装：{python} -m pip install ipykernel\n"
            f"或显式指定已有内核：execute(..., kernel='python3')"
        )
    import hashlib

    abspath = os.path.abspath(python)
    tag = re.sub(r"\W+", "", os.path.basename(abspath)) + hashlib.md5(abspath.encode()).hexdigest()[:8]
    name = f"nbkit-{tag}"
    jupyter_path = os.path.join(tempfile.gettempdir(), "nbkit-kernels")
    spec = os.path.join(jupyter_path, "kernels", name)
    os.makedirs(spec, exist_ok=True)
    with io.open(os.path.join(spec, "kernel.json"), "w", encoding="utf-8") as f:
        json.dump({
            "argv": [abspath, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": f"nbkit ({os.path.basename(abspath)})",
            "language": "python",
        }, f, indent=2)
    return name, jupyter_path


def doctor() -> str:
    """一次性报出环境是否够跑：依赖、中文字体、浏览器、档案状态。"""
    lines = [f"python     {sys.executable}  ({sys.version.split()[0]})", f"platform   {sys.platform}"]
    for mod in ("nbformat", "nbconvert", "jupyter_client", "matplotlib"):
        try:
            __import__(mod)
            lines.append(f"依赖       OK  {mod}")
        except Exception as e:
            lines.append(f"依赖       缺  {mod}  ({type(e).__name__}: {e})")
    fonts = cjk_fonts()
    lines.append(f"中文字体   {', '.join(fonts) if fonts else '没找到 —— 图里的中文会变方框，先装一个或指定 family'}")
    br = find_browser()
    lines.append(f"PDF 引擎   {br or '没找到 Chrome/Edge —— 出 PDF 前问用户装哪个'}")
    p = profile_path()
    lines.append(f"档案       {p}  [{'已存在' if os.path.exists(p) else '不存在，首次 check 会逐项询问'}]")
    if os.path.exists(p):
        try:
            prof = load_profile(p)
            miss = missing_fields(prof=prof)
            lines.append(f"档案字段   {'完整' if not miss else '待补：' + ', '.join(d for d, _ in miss)}")
        except Exception as e:
            lines.append(f"档案       解析失败：{e}")
    print("\n".join(lines))
    return "\n".join(lines)


# --------------------------------------------------------------------------- 建 / 跑 / 查

def MD(text: str):
    return ("md", text.strip())


def CODE(text: str):
    return ("code", text.strip())


def build(cells, out_path: str, kernel: str | None = None, display_name: str | None = None,
          course: str | None = None) -> str:
    """cells: [(kind, source)] 或 cells.json 读出来的 list[dict]。内核名默认取档案该条目配置。"""
    import nbformat as nbf

    if kernel is None:
        prof = load_profile()
        if course or prof.get("default_course") or len(prof.get("courses", {}) or {}) == 1:
            key = course_key(prof, course)[0]
            kernel = _dig(prof, f"courses.{key}.kernel") or "python3"
            display_name = display_name or _dig(prof, f"courses.{key}.display_name") or kernel
        else:
            kernel, display_name = "python3", "Python 3"
    nb = nbf.v4.new_notebook()
    nb.metadata.update({
        "kernelspec": {"display_name": display_name or kernel, "language": "python", "name": kernel},
        "language_info": {"name": "python"},
    })
    norm = [(c["type"], c["source"]) if isinstance(c, dict) else c for c in cells]
    nb.cells = [
        nbf.v4.new_markdown_cell(s) if k in ("md", "markdown") else nbf.v4.new_code_cell(s)
        for k, s in norm
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    nbf.write(nb, out_path)
    md = sum(1 for k, _ in norm if k in ("md", "markdown"))
    print(f"built {out_path}: {len(norm)} cells ({md} md / {len(norm) - md} code)")
    return out_path


def execute(path: str, timeout: int = 600, python: str | None = None, course: str | None = None,
            kernel: str | None = None) -> str:
    """就地执行，把输出写回 ipynb。没跑过的 notebook 不算交付。

    解释器优先级：显式 python > 档案该条目的 python > 当前进程。
    除非显式传 kernel，否则为该解释器临时注册内核，保证"跑的就是档案里那个环境"。
    """
    py = python or (resolve(course)["python"] if course else sys.executable)
    args = [py, "-m", "nbconvert", "--to", "notebook", "--execute", "--inplace",
            path, f"--ExecutePreprocessor.timeout={timeout}"]
    env = dict(os.environ)
    if kernel:
        args.append(f"--ExecutePreprocessor.kernel_name={kernel}")
    else:
        kn, jp = ensure_kernel(py)
        args.append(f"--ExecutePreprocessor.kernel_name={kn}")
        env["JUPYTER_PATH"] = jp + os.pathsep + env.get("JUPYTER_PATH", "")
    print(f"execute {path}  python={py}  kernel={kernel or 'nbkit 临时内核'}")

    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout + 120, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "NbConvertApp" in ln or "Error" in ln]
    print("\n".join(tail[-4:]) or f"nbconvert rc={r.returncode}")
    if r.returncode != 0:
        raise RuntimeError(f"执行失败：{path}\n{(r.stderr or '')[-1500:]}")
    return path


def audit(path: str, wide: int = 200, strict: bool = True) -> dict:
    """打印每个代码单元格的结论性输出供人和课件对数；有 error 直接抛，疑似空跑给警告。

    对数看什么：样本数、缺失值数、指标值、分类报告——逐项和课件的 Out[n] 比。
    """
    import nbformat as nbf

    nb = nbf.read(path, as_version=4)
    imgs, errs, empty, md = 0, [], [], 0
    for i, c in enumerate(nb.cells):
        if c.cell_type != "code":
            md += 1
            continue
        src = (c.source or "").strip()
        outs = c.get("outputs", [])
        for o in outs:
            t = o.get("output_type")
            if t == "stream":
                print(f"[{i}] OUT:", o["text"].strip()[:wide].replace("\n", " | "))
            elif t == "execute_result":
                print(f"[{i}] RES:", str(o["data"].get("text/plain", "")).strip()[:wide].replace("\n", " | "))
            elif t == "display_data" and "image/png" in o.get("data", {}):
                imgs += 1
                print(f"[{i}] <图 {imgs}>")
            elif t == "error":
                errs.append(f"[{i}] {o['ename']}: {o['evalue']}")
        if not outs and _expects_output(src):
            empty.append(i)
    print(f"cells: {len(nb.cells)} ({md} md / {len(nb.cells) - md} code)  图: {imgs}  error: {len(errs)}")
    if empty:
        print(f"疑似空跑（表达式单元格没有任何输出，多半是没真执行）：单元格 {empty}")
    if errs:
        raise RuntimeError("notebook 里有报错单元格：\n" + "\n".join(errs))
    if empty and strict:
        raise RuntimeError("有单元格没产出输出，先确认 execute 是否真的跑过（空输出的 notebook 不能交）")
    print("audit ok: 0 error")
    return {"errors": errs, "images": imgs, "empty_output_cells": empty, "cells": len(nb.cells)}


def _expects_output(src: str) -> bool:
    """最后一行是裸表达式 / head / plot / describe 这类，执行后应该有输出。"""
    if not src:
        return False
    last = [ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")][-1]
    if re.match(r"^\s*(import |from |%|!|@)", last):
        return False
    if re.search(r"=\s*[^=]", last) and not last.lstrip().startswith(("df[", "plt.")):
        return False
    if re.search(r"\.(head|tail|info|describe|shape|value_counts|plot|show|display|hist|bar|corr)\b", last):
        return True
    if re.match(r"^\s*(print|display)\s*\(", last):
        return True
    return bool(re.match(r"^[A-Za-z_][\w.\[\]'\"]*$", last.strip()))


def deliver(path: str, exp_name: str, course: str | None = None, dest_dir: str | None = None,
            student_id: str | None = None, student_name: str | None = None, pattern: str | None = None,
            force: bool = False) -> str:
    """按档案 deliver_pattern 重命名并落到该条目的 output_dir。

    实验名取课件文件名，不要自己发挥。显式参数只用于本次覆盖，长期值请写回档案。
    """
    v = resolve(course)
    sid = student_id or v["student_id"]
    name = student_name or v["student_name"]
    dest = os.path.expanduser(dest_dir or v["output_dir"])
    stem = _safe((pattern or v["pattern"]).format(id=sid, name=name, exp=exp_name))
    os.makedirs(dest, exist_ok=True)
    new = os.path.join(dest, stem + ".ipynb")
    if os.path.abspath(new) == os.path.abspath(path):
        return new
    if os.path.exists(new) and not force:
        raise RuntimeError(f"目标已存在，不覆盖：{new}\n确认要替换就加 --force，或换实验名。")
    os.replace(path, new)
    print(f"renamed -> {new}")
    return new


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "-", name).strip()


PRINT_CSS = """
<style>
@page { size: A4; margin: 14mm 12mm; }
html, body { background: #fff; }
.jp-Notebook { padding: 0; max-width: 100%; }
h1 { font-size: 22px; margin: 6px 0 12px; }
h2 { font-size: 18px; margin: 22px 0 8px; border-bottom: 2px solid #e0e0e0; padding-bottom: 4px; }
h3 { font-size: 15px; margin: 16px 0 6px; }
p, li { font-size: 13px; line-height: 1.75; }
.jp-RenderedHTMLCommon p { margin: 6px 0; }
.jp-InputArea-editor, .jp-OutputArea pre, .jp-RenderedText { font-size: 11.5px !important; line-height: 1.5; }
pre { white-space: pre-wrap !important; word-break: break-all; }
.jp-Cell { page-break-inside: avoid; break-inside: avoid; margin-bottom: 10px; }
h2, h3 { page-break-after: avoid; }
img { max-width: 100%; }
.jp-RenderedHTMLCommon table, .jp-RenderedHTMLCommon tbody tr td { font-size: 11.5px; }
</style>
"""


def to_pdf(ipynb_path: str, course: str | None = None, browser: str | None = None,
           keep_html: bool = False) -> str:
    """ipynb -> lab 模板 HTML（注入打印 CSS）-> 无头浏览器打印 A4。

    不走 LaTeX：notebook 里的中文标题/注释过 LaTeX 容易丢字形，浏览器链路出的是 Skia/PDF，中文和图都稳。
    浏览器优先级：显式 browser > 档案 > 自动探测（Chrome/Edge 任一）。
    """
    from nbconvert import HTMLExporter

    prof = load_profile()
    br = browser or (resolve(course)["browser"] if course else None) or _dig(prof, "tools.print_pdf.path") or find_browser()
    if not br or not os.path.exists(br):
        raise ProfileError(f"没找到可用的无头浏览器（试了：{br or '档案未记录且自动探测失败'}）。\n"
                           "问用户本机 Chrome/Edge 装在哪，再 python nbkit.py set tools.print_pdf.path=...")
    if not _dig(prof, "tools.print_pdf.path"):
        print(f"提示：档案未记录浏览器，本次用 {br}；落盘 python nbkit.py set tools.print_pdf.path={br}")

    # 必须绝对路径：无头模式的 --print-to-pdf 按浏览器自己的 cwd 解析相对路径，Windows 上会报 0x5 拒绝访问
    base = os.path.abspath(ipynb_path).rsplit(".", 1)[0]
    html, pdf = base + ".html", base + ".pdf"
    body, _ = HTMLExporter(template_name="lab").from_filename(ipynb_path)
    assert "</head>" in body, "HTML 模板结构变了，检查 nbconvert 版本"
    io.open(html, "w", encoding="utf-8").write(body.replace("</head>", PRINT_CSS + "</head>", 1))

    r = subprocess.run(
        [br, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--virtual-time-budget=20000", "--run-all-compositor-stages-before-draw",
         f"--print-to-pdf={pdf}", "file:///" + os.path.abspath(html).replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if r.returncode != 0 or not os.path.exists(pdf):
        raise RuntimeError(f"{br} 打印失败 rc={r.returncode}\n{(r.stderr or '')[-500:]}")
    if not keep_html:
        os.remove(html)
    pages = _page_count(pdf)
    print(f"{pdf}: {os.path.getsize(pdf) // 1024} KB" + (f", {pages} 页" if pages else ""))
    return pdf


def _page_count(pdf: str):
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod)
            return len(m.PdfReader(pdf).pages)
        except Exception:
            continue
    return None


def cleanup(*paths: str) -> None:
    """临时件收干净：构建脚本、中间 HTML 删掉，交付目录只留 .ipynb + .pdf + data/。"""
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)
            print("removed", p)


# --------------------------------------------------------------------------- 命令行

def _kv_to_dict(pairs: list[str]) -> dict:
    updates: dict = {}
    for kv in pairs:
        dotted, _, val = kv.partition("=")
        cur = updates
        keys = dotted.split(".")
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = val
    return updates


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nbkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path", help="打印档案路径")
    sub.add_parser("profile", help="打印档案内容")
    sub.add_parser("doctor", help="体检：依赖/中文字体/浏览器/档案")

    p = sub.add_parser("init", help="一次性写入署名与某个课程/项目条目")
    p.add_argument("--student-id")
    p.add_argument("--student-name")
    p.add_argument("--pattern", help="文件名模板，默认 {id}-{name}-{exp}")
    p.add_argument("--course", help="课程/项目 key")
    p.add_argument("--label")
    p.add_argument("--materials-dir", action="append", default=[], help="可重复")
    p.add_argument("--data-dir", action="append", default=[], help="可重复")
    p.add_argument("--output-dir")
    p.add_argument("--python", dest="py")
    p.add_argument("--browser")
    p.add_argument("--set-default", action="store_true", help="把该条目设为 default_course")

    p = sub.add_parser("set", help="set 字段=值，深合并落盘")
    p.add_argument("pairs", nargs="+")

    p = sub.add_parser("ask", help="打印缺失字段的标准题面")
    p.add_argument("course", nargs="?")

    p = sub.add_parser("check", help="preflight：摊开并校验本次要用的路径")
    p.add_argument("course", nargs="?")

    p = sub.add_parser("build", help="cells.json -> ipynb")
    p.add_argument("cells_json")
    p.add_argument("out")
    p.add_argument("--course")
    p.add_argument("--kernel")

    p = sub.add_parser("run", help="就地执行 notebook")
    p.add_argument("notebook")
    p.add_argument("--course")
    p.add_argument("--python", dest="py")
    p.add_argument("--kernel")
    p.add_argument("--timeout", type=int, default=600)

    p = sub.add_parser("audit", help="打印单元格输出摘要，报错/空跑即失败")
    p.add_argument("notebook")
    p.add_argument("--wide", type=int, default=200)
    p.add_argument("--lenient", action="store_true", help="空输出只警告不报错")

    p = sub.add_parser("deliver", help="按档案模板重命名并搬进输出目录")
    p.add_argument("notebook")
    p.add_argument("exp_name")
    p.add_argument("--course")
    p.add_argument("--dest")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("pdf", help="ipynb -> A4 PDF")
    p.add_argument("notebook")
    p.add_argument("--course")
    p.add_argument("--browser")
    p.add_argument("--keep-html", action="store_true")

    p = sub.add_parser("fonts", help="列出可用中文字体")
    p.add_argument("--cell", action="store_true", help="直接输出可粘贴的设置代码")
    p.add_argument("--family")

    p = sub.add_parser("clean", help="删除临时件")
    p.add_argument("paths", nargs="+")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "path":
            print(profile_path())
        elif a.cmd == "profile":
            print(f"# {profile_path()}")
            print(json.dumps(load_profile(), ensure_ascii=False, indent=2))
        elif a.cmd == "doctor":
            doctor()
        elif a.cmd == "ask":
            print(ask_text(a.course))
        elif a.cmd == "check":
            preflight(a.course)
        elif a.cmd == "set":
            save_profile(_kv_to_dict(a.pairs))
        elif a.cmd == "init":
            updates: dict = {}
            if a.student_id:
                updates.setdefault("student", {})["id"] = a.student_id
            if a.student_name:
                updates.setdefault("student", {})["name"] = a.student_name
            if a.pattern:
                updates["deliver_pattern"] = a.pattern
            if a.browser:
                updates["tools"] = {"print_pdf": {"path": a.browser}}
            if a.course:
                entry: dict = {}
                if a.label:
                    entry["label"] = a.label
                if a.materials_dir:
                    entry["materials_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in a.materials_dir]
                if a.data_dir:
                    entry["data_search_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in a.data_dir]
                if a.output_dir:
                    entry["output_dir"] = os.path.abspath(os.path.expanduser(a.output_dir))
                if a.py:
                    entry["python"] = os.path.abspath(os.path.expanduser(a.py))
                updates["courses"] = {a.course: entry}
                if a.set_default:
                    updates["default_course"] = a.course
            if not updates:
                print("没给任何参数，什么都没写。看 nbkit.py init -h")
            save_profile(updates)
        elif a.cmd == "build":
            with io.open(a.cells_json, encoding="utf-8") as f:
                data = json.load(f)
            build(data["cells"] if isinstance(data, dict) else data, a.out, course=a.course, kernel=a.kernel)
        elif a.cmd == "run":
            execute(a.notebook, timeout=a.timeout, python=a.py, course=a.course, kernel=a.kernel)
        elif a.cmd == "audit":
            audit(a.notebook, wide=a.wide, strict=not a.lenient)
        elif a.cmd == "deliver":
            deliver(a.notebook, a.exp_name, course=a.course, dest_dir=a.dest, force=a.force)
        elif a.cmd == "pdf":
            to_pdf(a.notebook, course=a.course, browser=a.browser, keep_html=a.keep_html)
        elif a.cmd == "fonts":
            if a.cell:
                print(font_cell(a.family))
            else:
                print("\n".join(cjk_fonts()) or "没找到中文字体")
        elif a.cmd == "clean":
            cleanup(*a.paths)
    except ProfileError as e:
        print(e, file=sys.stderr)
        return 1
    except (RuntimeError, AssertionError) as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
