# 询问规范：题面、顺序、以及什么不该问

`python scripts/nbkit.py ask <course>` 会按当前档案打印下面的清单（只打印真正缺的项）。
把它的输出直接转成提问，不要自己另编字段名。

## 一次问全，顺序固定

| # | 字段 | 中文题面 | English |
| --- | --- | --- | --- |
| 1 | `default_course` | 这次是哪门课 / 哪个项目？（档案里已有的：…） | Which course/project is this for? |
| 2 | `student.id` | 交付文件名的署名编号用什么（学号/工号）？ | What ID goes in the deliverable filename? |
| 3 | `student.name` | 署名姓名用什么？ | What name goes in the filename? |
| 4 | `courses.<k>.materials_dirs` | 课件/讲义放在哪个目录？给绝对路径，可以多个 | Absolute path(s) to the courseware |
| 5 | `courses.<k>.data_search_dirs` | 课件点名的数据集该去哪几个目录找？ | Where should I look for the datasets? |
| 6 | `courses.<k>.output_dir` | 成果（ipynb / pdf / data）交付到哪个目录？ | Where should deliverables be written? |
| 7 | `courses.<k>.python` | 用哪个 Python 解释器跑这个 notebook？ | Which interpreter must run this notebook? |

提问方式按所在 agent 走：Claude Code 用 AskUserQuestion（把档案里已有的同类路径作为候选项，
标成"档案记录，推荐"）；命令行 / Codex / Cursor 就直接在回复里列这七条，让用户一次答完。

## 落盘，别只记在对话里

```bash
python scripts/nbkit.py init --course ml-lab --materials-dir ... --output-dir ... --python ...
python scripts/nbkit.py set courses.ml-lab.output_dir=~/work/ml
```

下次同一门课只复述一行"档案：输出目录 = …，解释器 = …，对吗？"，对了就继续，不要重新逐条索要。

## 不该问的

- `deliver_pattern`、`kernel`、`display_name`、`tools.print_pdf.path`：都有默认值或能自动探测，
  探测到了按提示落盘即可。
- 课件里已经写明的东西（实验名、字段含义、步骤顺序）：那是"标准答案"，读课件，不是问用户。
- 数据缺失之外的替代方案：数据集找不到要问，但"要不要顺手加一节调参"这种不要问——课件没有就不做。

## 三条红线

1. **不拿同名/相邻目录兜底。** 找不到就是找不到，问；不要退到"看起来像"的目录，那会静默交付错版本。
2. **不造数据。** 用户明确同意后才用现有数据集派生，并在 notebook 里注明换了数据源。
3. **档案里目录不存在时先核一句。** 外接盘没插、目录改过名、同盘有两个名字几乎一样的目录，
   都属于"路径过期"，用 `os.listdir` 看 repr 而不是改字猜。
