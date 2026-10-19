# jupyter-lab-report

用于处理繁琐的实验报告的一个工作流 — 把课程实验课件（PDF / 截图 / md 讲义 + 数据集）复刻成
**自己真跑过的 `.ipynb`**，并导出**中文不乱码的 A4 PDF**，交付文件名按档案里的规则自动署名。

Turn course lab handouts (PDF / screenshots / markdown, plus datasets) into **your own executed
Jupyter notebook** and a **Chinese-safe A4 PDF**, with a fixed naming convention for submission.

The workflow is machine-independent. Everything that *is* machine-specific — who is submitting,
where the courseware lives, where deliverables go, which Python interpreter runs the notebook,
which browser prints the PDF — lives in a `profile.json`. When a field is missing the tooling
refuses to guess: it prints a standard question list so the agent asks you, then records the answer.

## Why this exists

Reproducing a lab handout is mostly bookkeeping, and the failure modes are boring but expensive:

- **The notebook was never really run.** `nbconvert --execute` output is the only proof; empty
  output cells get flagged as failures by `audit`.
- **Silent wrong-environment execution.** `nbconvert --execute` resolves a *kernelspec name*, not an
  interpreter. Swapping the Python that launches nbconvert does not swap the kernel. `run` registers
  a temporary kernelspec for the interpreter in your profile, so the notebook really runs there.
- **Chinese text turns into boxes in the PDF.** LaTeX routes drop CJK glyphs; this prints through
  headless Chrome/Edge (Skia PDF) with a print stylesheet instead.
- **Hard-coded paths rot.** Nothing in `SKILL.md` or `nbkit.py` contains an absolute path or a
  personal identifier.

## Install

```bash
git clone <your-fork-url> && cd jupyter-lab-report
pip install -r requirements.txt
python scripts/nbkit.py doctor
```

`doctor` reports: interpreter, `nbformat` / `nbconvert` / `jupyter_client` / `matplotlib`,
available CJK fonts, a usable headless browser, and profile status.

Then drop the folder into your agent's skills directory (see [Agent integration](#agent-integration))
and fill the profile once:

```bash
python scripts/nbkit.py init \
  --student-id 20240001 --student-name "Zhang San" \
  --course ml-lab --label "Machine Learning Lab" \
  --materials-dir ~/courseware --data-dir ~/datasets --output-dir ~/work/ml \
  --python "$(which python3)" --set-default
```

## Quickstart

```bash
python scripts/nbkit.py check ml-lab                     # print every path this run will use
python scripts/nbkit.py build cells.json draft.ipynb --course ml-lab
python scripts/nbkit.py run   draft.ipynb --course ml-lab --timeout 900
python scripts/nbkit.py audit draft.ipynb                # per-cell outputs; fails on errors/empty runs
python scripts/nbkit.py deliver draft.ipynb "Lab-4-Logistic-Regression" --course ml-lab
python scripts/nbkit.py pdf  "20240001-Zhang San-Lab-4-Logistic-Regression.ipynb" --course ml-lab
python scripts/nbkit.py clean cells.json
```

`cells.json` is a flat cell list, which keeps the model out of ipynb JSON entirely:

```json
{"cells": [
  {"type": "md",   "source": "## 1. Imports"},
  {"type": "code", "source": "import pandas as pd"}
]}
```

## The profile

Lookup order (first hit wins): `$NBKIT_PROFILE` → `~/.nbkit/profile.json` →
`<skill>/profile.local.json` → `<skill>/profile.json`. **User-level is recommended** — it survives
skill updates and never ends up in a git repo. Every command prints the resolved path.

| Field | Meaning | Asked if missing |
| --- | --- | --- |
| `student.id`, `student.name` | signature used in deliverable filenames | yes |
| `deliver_pattern` | filename template, default `{id}-{name}-{exp}` | no (default) |
| `default_course` | which entry to use when not specified | yes |
| `courses.<k>.materials_dirs` | where the courseware lives | yes |
| `courses.<k>.data_search_dirs` | where to look for named datasets | yes |
| `courses.<k>.output_dir` | where ipynb / pdf / `data/` are written | yes |
| `courses.<k>.python` | interpreter that must run the notebook | yes |
| `courses.<k>.kernel`, `display_name` | kernelspec written into the notebook | no (default) |
| `tools.print_pdf.path` | Chrome/Edge binary for headless printing | no (auto-detected) |

Copy `profile.example.json` to `~/.nbkit/profile.json` and edit, or use `init` / `set`
(`set` deep-merges, so single fields are one-liners). `deliver` refuses to overwrite an existing
deliverable unless you pass `--force`.

## Agent integration

`SKILL.md` follows the widely used skill convention (frontmatter `name` + `description`, body of
instructions). The CLI is plain `python scripts/nbkit.py ...`, and the Python API is plain imports —
no agent-specific tool is required, so the same folder works for:

| Agent | Where to put it |
| --- | --- |
| Claude Code | `~/.claude/skills/jupyter-lab-report/` |
| Codex CLI | `~/.codex/skills/jupyter-lab-report/` |
| Qoder | `~/.qoder-cn/skills/jupyter-lab-report/` |
| Cursor / anything else | keep the folder anywhere and point a rules file at `SKILL.md` |

Naming: the repo may carry a `-skill` suffix for discoverability (`jupyter-lab-report-skill`), but
the directory an agent actually loads must be named exactly `jupyter-lab-report` — it has to match
the frontmatter `name`, otherwise skill discovery silently skips it.

Prompting in another language works: the `description` carries both Chinese and English trigger
phrases. The instruction body is Chinese because the workflow (courseware reproduction, student-ID
filename signatures, oral defence) is a Chinese-classroom one; contributions that translate it are
welcome.

## Layout

```
SKILL.md                    the workflow (Chinese)
README.md                   this file
profile.example.json        template; copy to ~/.nbkit/profile.json
scripts/nbkit.py            CLI + Python API, no personal data, no absolute paths
references/
  ask-user-checklist.md     exact questions, in order, plus what not to ask
  pdf-and-fonts.md          why browser printing, CJK fonts, page-break CSS
  course-branch-example.md  how to document a course that differs from the norm
evals/evals.json            behaviour checks, including the ask-first contract
```

## Contributing

Two rules keep it portable: (1) no absolute path or personal identifier in any tracked file except
`profile.example.json` placeholders; (2) new environment knowledge goes into the profile or
`references/`, never into `SKILL.md`'s flow.

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
