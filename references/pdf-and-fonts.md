# 出 PDF：为什么走浏览器，以及中文/分页的坑

## 链路

`ipynb --(nbconvert lab 模板 + 注入打印 CSS)--> html --(无头 Chrome/Edge --print-to-pdf)--> A4 PDF`

- **不走 LaTeX**：notebook 里的中文标题、注释、图题过 xelatex 容易丢字形或整段消失，
  而浏览器链路出的是 Skia PDF，字体交给系统解析，中文和图都稳。
- **必须传绝对路径**：无头模式的 `--print-to-pdf` 按浏览器自己的 cwd 解析相对路径，
  Windows 上会报 `0x5 拒绝访问`，其他平台会写到意料之外的目录。
- `--virtual-time-budget=20000` 给 matplotlib 的 `<img>` 和表格渲染留时间，否则偶发空白图。
- 打印 CSS 里两条最关键：`.jp-Cell { page-break-inside: avoid }`（单元格不被从中间切开）、
  `h2, h3 { page-break-after: avoid }`（标题不落页尾变孤儿）。

## 中文与字体

- notebook 里画图前必须设中文字体，否则**图里的**中文是方框（PDF 正文正常，图是栅格化后的）。
- 别照抄别家机器的字体名。用 `python scripts/nbkit.py fonts` 看本机 matplotlib 能解析哪些中文字体，
  `fonts --cell` 直接输出可粘贴的三行 rcParams。
- Linux 容器里通常什么都没有：装 `fonts-noto-cjk`（Debian/Ubuntu）或 `google-noto-sans-cjk-ttc-fonts`
  （Fedora），再重跑 `fonts`。
- 表格右侧列被截、带横向滚动条：这是 `df.head()` 宽表在 A4 上的正常表现，课件原 PDF 也这样，不用修。
  真要全列可见，就把那一格改成 `df.to_string()` 或转置。

## 抽查

出完 PDF 用读文件工具看**首页、含图页、末页**（多数 agent 支持指定页码区间）：
中文有没有方框、图有没有被切一半、结论页数字对不对。`pdf` 命令会顺带报页数和 KB 数，
汇报时直接用（没装 pypdf 就只报 KB）。

## 换引擎

档案里 `tools.print_pdf.path` 指哪个浏览器就用哪个；Chrome 与 Edge 的无头参数一致。
只有 WebKit 系（GNOME Web 等）参数不同，需要改 `to_pdf()` 里的命令行，暂不在支持范围。
