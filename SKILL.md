---
name: jupyter-lab-report
description: 把课程实验课件（PDF / 截图 / md 讲义 + 数据集）复刻成本人可提交的 .ipynb 并出中文 A4 PDF 的一条龙：读课件、找数据、markdown 章节与代码块交替建 notebook、nbconvert 真跑自检、按档案命名规则交付、无头浏览器打印 PDF。署名与各类目录不写死在流程里，统一由 profile.json 提供，缺字段必须先询问用户。Use when the user hands over course lab courseware/PDF and asks to 复刻该 pdf 的流程 / 创建一个 NNNN.ipynb / 调用合适的 csv / 重命名为学号-姓名-实验名称 / 把 ipynb 生成为 pdf / 按这份课件做实验报告, or in English: reproduce a Jupyter notebook from lab handouts and export a Chinese PDF.
---

# jupyter-lab-report — 课件 → ipynb → PDF

流程与机器无关；「谁交付、课件在哪、成果放哪、用哪个解释器、用什么打印 PDF」全部来自环境档案
`profile.json`。交付物固定两件：**真跑过的** `.ipynb` + 同名 A4 `.pdf`，外加 notebook 依赖的 `data/`。
中间 HTML、构建脚本是临时件，出完 PDF 就删。

**该不该开这个技能**：输入是"课件/讲义"、输出是"我自己的 notebook 报告"，就是它。
纯数据分析、随手画图（不交报告）不算；要交 md + 终端截图的走终端截图类技能。

所有命令都在本技能目录下执行（`<skill>` 指本 SKILL.md 所在目录），脚本是纯 CLI，任何 agent 都能驱动：

```bash
python <skill>/scripts/nbkit.py doctor      # 第一次用先体检：依赖 / 中文字体 / 浏览器 / 档案
python <skill>/scripts/nbkit.py check       # 摊开本次要用的路径
```

## 0. 先解析档案，缺什么问什么（规范性全在这一步）

档案查找顺序：`$NBKIT_PROFILE` > `~/.nbkit/profile.json` > `<skill>/profile.local.json` > `<skill>/profile.json`。
推荐写在用户级 `~/.nbkit/profile.json`：升级或重新 clone 技能都不会丢，也不会把个人信息带进仓库。

- **缺字段就抛错，题面是现成的**：`nbkit.py ask <course>`。用你所在 agent 的提问方式
  （Claude Code 用 AskUserQuestion，Codex / Cursor / 命令行就直接在回复里列问题）**一次问全**，
  固定顺序：署名编号 → 姓名 → 课件目录 → 找数据目录 → 输出目录 → Python 解释器。
- 问到的**当场落盘**，下次只复述确认、不再重复问：

  ```bash
  python <skill>/scripts/nbkit.py init --student-id 20240001 --student-name "Zhang San" \
      --course ml-lab --label "机器学习实验" \
      --materials-dir ~/courseware --data-dir ~/datasets --output-dir ~/work/ml \
      --python "$(which python3)" --set-default
  python <skill>/scripts/nbkit.py set courses.ml-lab.output_dir=~/work/ml   # 单字段改这个
  ```

- 新增一门课 / 一个项目 = 档案里加一个 key。**不要往 SKILL.md 里写绝对路径**——那是数据，
  写进文档换个机器就得改流程。
- 三条红线：不拿同名/相邻目录当兜底；不自己造数据；`check` 报"目录不存在"时先跟用户核一句再用
  （外接盘没插、路径过期、同名目录有两个，都属这类）。
- 有默认值或能自动探测的（文件名模板 `deliver_pattern`、`kernel`、浏览器路径）**不要拿去烦用户**。

| 档案字段 | 用途 | 缺失时 |
| --- | --- | --- |
| `student.id` / `student.name` | 交付文件署名 | 必问 |
| `courses.<k>.materials_dirs` | 去哪读课件 | 必问 |
| `courses.<k>.data_search_dirs` | 去哪找课件点名的数据集 | 必问 |
| `courses.<k>.output_dir` | ipynb / pdf / data 落哪 | 必问 |
| `courses.<k>.python` | 执行 notebook 的解释器 | 必问（确认当前进程就够时可省） |
| `courses.<k>.kernel`、`deliver_pattern`、`tools.print_pdf.path` | 内核名 / 文件名模板 / 打印引擎 | 有默认值或自动探测，不问 |

## 1. 三要素齐了才动手

| 要素 | 从档案哪个字段拿 | 说明 |
| --- | --- | --- |
| 课件 | `materials_dirs` | 决定 notebook 的单元格序列，是唯一的"标准答案" |
| 数据 | `data_search_dirs` | 按课件代码里写的相对路径（如 `./data/xxx.csv`）去找；找到后**拷进 `<output_dir>/data/`**，让相对路径原样可用 |
| 课程归属 | `default_course` / 用户话里的课程名 | 用户只说"复刻课件流程"时先确认是哪门课 |

数据按给到的文件名找不到，**问一句实际路径**，不要造数据、不要拿别的数据集凑。
课件里的内网地址或他人机器路径本机不可达时，先问；同意后才就地取材派生，并在报告里注明换了数据源。

## 2. 读课件：把单元格序列抄准

课件 PDF 大多是**渲染出来的 notebook 导出**，`In [n]:` / `Out[n]:` 就是单元格边界。
逐页看图（多数 agent 的读文件工具支持指定页码区间，单次 ≤20 页）。两类坑：

- **讲义型 PDF 常是纯图片、没有文字层**，`pypdf.extract_text()` 返回空串，只能看渲染图。
- **代码行会被页宽截断**（如 `random_sta` 断在行尾）。被截的部分靠上下文补全，补不出来就问，别猜一个值塞进报告。

课件里 `import` 了但正文没用到的库（如 `GridSearchCV`），**保持不用**。复刻流程不等于补全流程，
多加的步骤会被当成没做过课件内容。

## 3. 建 notebook：markdown 章节 + 代码块交替

不要把所有代码堆进几个大单元格——notebook 是要转 PDF 交上去的，读起来得像一份报告。
把单元格写成 `cells.json`，用 `build` 生成，别手搓 ipynb JSON，也别用编辑器的单元格插入功能（会覆盖已有单元格）：

```json
{"cells": [
  {"type": "md",   "source": "# 实验四 逻辑回归——客户流失预测\n\n学号：…　姓名：…　日期：…"},
  {"type": "md",   "source": "## 一、导包"},
  {"type": "code", "source": "import pandas as pd"},
  {"type": "code", "source": "df = pd.read_csv('./data/churn.csv')\ndf"}
]}
```

```bash
python <skill>/scripts/nbkit.py build cells.json draft.ipynb --course ml-lab
python <skill>/scripts/nbkit.py fonts --cell          # 打印本机可用的中文字体设置代码，粘进导包那一格
```

排版约定（这几条直接决定 PDF 好不好看）：

- 章节用中文数字编号（`## 一、`、`### 3.1`），标题文字沿用课件的小标题。
- **课件里每段代码上方的中文注释原样保留**在代码里；markdown 只补课件没说的"为什么这么做"，
  一到三句、大白话。
- 课件用 `print(...)` 出结果的地方保留 `print`；课件是裸表达式（`df.head()`）的地方**别加 print**，
  保持"最后一行表达式直接出结果"的写法。
- 画图前必设中文字体，否则 PDF 里图题全是方框：用 `nbkit.py fonts` 看本机有什么，
  `nbkit.py fonts --cell` 直接出可粘贴的三行 rcParams。别照抄别家机器的字体名。
- 结尾自己加一节 `## 结果分析`：3 条左右，讲清指标怎么看、和业务/相关性分析怎么对上。

## 4. 执行 + 对数（这一步不能跳）

```bash
python <skill>/scripts/nbkit.py run draft.ipynb --course ml-lab --timeout 900
python <skill>/scripts/nbkit.py audit draft.ipynb
```

`run` 会为档案里那个解释器临时注册内核再执行——只换跑 nbconvert 的 python 并不会换 notebook 用的内核，
多环境机器上这会静默跑错环境。`audit` 打印每个代码单元格的结论性输出、图片数、报错，
并对"表达式单元格没有任何输出"直接判失败：**空输出的 notebook 不算交付**。

拿 `audit` 的输出和课件的 `Out[n]` 逐项比：样本数、缺失值数、准确率、分类报告。
**总数对得上、个别指标差在小数点后两位**，通常是切分随机种子下的行差异，不是流程做错了——
核对总样本和正类样本数是否完全一致就能确认，别为此去反推凑数的 `random_state`。
结论段的数字一律按**实测值**写，抄课件数字会在答辩/评审时对不上。

## 5. 重命名 + 出 PDF

```bash
python <skill>/scripts/nbkit.py deliver draft.ipynb "实验四-逻辑回归" --course ml-lab
python <skill>/scripts/nbkit.py pdf "20240001-Zhang San-实验四-逻辑回归.ipynb" --course ml-lab
python <skill>/scripts/nbkit.py clean cells.json draft.html          # 临时件收干净
```

- 实验名取课件文件名，别自己发挥；`deliver` 用档案的 `deliver_pattern` 拼名（默认 `{id}-{name}-{exp}`），
  换署名规则只改档案。目标已存在时**不覆盖**，要替换显式加 `--force`。
- PDF 走无头浏览器而不是 LaTeX：notebook 里的中文过 LaTeX 容易丢字形；浏览器链路出的是 Skia/PDF，
  中文和图都稳。Chrome 优先，档案没记录时自动探测 Chrome/Edge，探测结果按提示落盘。
- 出完抽查 PDF 的**首页、图表页、末页**，确认中文没变方框、图没被切一半。
  `df.head()` 这类宽表在打印时带横向滚动条、右侧列被截，这个表现和课件原 PDF 一致，不用修。
- 交付目录最后只留：`.ipynb` + `.pdf` + `data/`。

## 6. 汇报模板

1. 一句结论 + 成果清单（`.ipynb` / `.pdf` / `data\`，给绝对路径，附页数和大小）。
2. 复刻到什么程度：单元格数、markdown/代码配比、执行 0 报错、图几张。
3. 与课件的差异**只说一处**：数字为什么不同、或哪一步课件被页宽截断了。
4. 结尾给选项：要不要换目录、要不要改实验名称、要不要加调参那一节。
5. 本次问过路径的话补一行"档案已记住 `courses.<k>.xxx = …`"，让用户知道下次不会再问。

别写成事故报告，也别把同一个数据差异解释两遍。

## 附：移植与排错

- **换机器**：`python <skill>/scripts/nbkit.py doctor` 一次看全依赖/中文字体/浏览器/档案缺项。
- **换 agent**：脚本是纯 CLI + 纯 Python API，不依赖任何 agent 专有工具；`SKILL.md` 用的是
  Claude Code / Qoder 的 skill 约定（frontmatter `name` + `description`），Codex / Cursor 直接把
  本目录放进它们的 skills 目录，或在规则文件里指向本 SKILL.md。
- **课件形态不是 notebook 导出**（终端截图 / 纯文字步骤）时见 `references/course-branch-example.md`；
  询问话术见 `references/ask-user-checklist.md`；打印与字体细节见 `references/pdf-and-fonts.md`。
- **依赖**：`pip install -r requirements.txt`（nbformat / nbconvert / jupyter-client / ipykernel；
  matplotlib 与 pypdf 可选，分别用于画图与报页数）。
