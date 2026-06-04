# Paper Skill Pipeline 说明文档

本文档说明 `backend/app/modules/paper` 目前的论文生成流程：`service.py` 如何调度 skill、每个 skill 负责哪一步、产生哪些中间变量，以及中间产物落在哪里。

## 1. 总入口

Paper 生成入口：

```text
backend/app/modules/paper/service.py
```

`service.py` 现在主要做调度：

```text
读取 paper meta
确定 provider/model/paperType/venue
创建 PaperSkillContext
调用 PaperSkillLeader 依次运行 skills
从 ctx.data 汇总最终结果并更新 meta.json
```

真正的步骤顺序在：

```text
backend/app/modules/paper/skills/leader.py
```

当前默认 pipeline：

```text
01 collect_context
02 paper_brief
03 outline
04 outline_gate
05 section_write
06 evidence_gate
07 figure_generate
08 assemble_latex
09 compile_pdf
10 qa_audit
```

## 2. 中间变量传递机制

核心结构在：

```text
backend/app/modules/paper/skills/base.py
```

每个 skill 返回 `PaperSkillResult`：

```python
PaperSkillResult(
    name="skill_name",
    summary="short summary",
    artifacts=["artifacts/xx.json", "artifacts/xx.md"],
    data={"key": value},
)
```

`leader.py` 会把每个 skill 的 `data` 合并进 `ctx.data`：

```python
ctx.update(k, v)
```

所以后面的 skill 可以读取前面产生的变量：

```python
ctx.get("context")
ctx.get("paper_brief")
ctx.get("outline")
ctx.get("sections_content")
ctx.get("figure_entries")
```

## 3. 中间产物落盘位置

每一步都会写 `.json` 和 `.md`：

```text
backend/data/papers/<paper_id>/latex/artifacts/
```

当前 artifact：

```text
01_collect_context.json / .md
02_paper_brief.json / .md
03_outline.json / .md
04_outline_gate.json / .md
05_section_write.json / .md
06_evidence_gate.json / .md
07_figure_generate.json / .md
08_assemble_latex.json / .md
09_compile_pdf.json / .md
10_qa_audit.json / .md
```

## 4. Skill 总表

| 顺序 | Skill | 文件 | 作用 | 写入 `ctx.data` |
| --- | --- | --- | --- | --- |
| 01 | `collect_context` | `skills/collect_context.py` | 收集 plan/project/experiment/run/figure/notes 上下文 | `context` |
| 02 | `paper_brief` | `skills/paper_brief.py` | 生成论文写作 brief，用户可补充 | `paper_brief` |
| 03 | `outline` | `skills/outline.py` | 根据 context + brief 生成大纲 | `outline` |
| 04 | `outline_gate` | `skills/outline_gate.py` | 检查大纲结构质量 | `outline_gate_issues` |
| 05 | `section_write` | `skills/section_write.py` | 逐节生成 LaTeX 正文 | `sections`, `sections_content` |
| 06 | `evidence_gate` | `skills/evidence_gate.py` | 统计算法、公式、表格、图片、引用 | `evidence_gates` |
| 07 | `figure_generate` | `skills/figure_generate.py` | 接入实验图并生成默认图 | `figure_entries` |
| 08 | `assemble_latex` | `skills/assemble_latex.py` | 拼装 LaTeX 工程并修正图片路径 | 无新增 |
| 09 | `compile_pdf` | `skills/compile_pdf.py` | 编译 PDF，失败则 fallback | `pdf_available` |
| 10 | `qa_audit` | `skills/qa_audit.py` | 汇总 brief、outline gate、evidence gate | `qa_summary` |

## 5. 每个 Skill 说明

### 01 collect_context

输入：

```text
paper.planLinkId
paper.projectId
paper.experimentIds
paper.figureIds
paper.runIds
paper.notes
```

输出中间变量：

```python
context = {
    "plan_context": "...",
    "project_summary": "...",
    "metrics_summary": "...",
    "runs_summary": "...",
    "figures_summary": "...",
    "user_notes": "...",
}
```

用途：

```text
给 paper_brief、outline、section_write 提供原始材料。
```

重点看：

```text
01_collect_context.json
```

如果 `metrics_summary`、`figures_summary` 是 `N/A`，后面的论文容易空泛。

### 02 paper_brief

文件：

```text
backend/app/modules/paper/skills/paper_brief.py
```

作用：

```text
在正式写大纲前，先生成一份写作任务书。
用户可以在前端补充 briefUserEdits。
如果用户不补充，也能自动生成。
如果 LLM 失败，会 fallback 到保守 brief，保证论文生成不中断。
```

输出中间变量：

```python
paper_brief = {
    "research_question": "...",
    "core_claim": "...",
    "paper_angle": "system",
    "target_audience": "...",
    "contributions": [...],
    "must_use_evidence": [...],
    "must_use_figures": [...],
    "section_priorities": {...},
    "avoid_claims": [...],
}
```

同时写入 paper meta：

```text
briefJson
briefUserEdits
briefStatus
```

相关 API：

```text
GET   /api/v1/papers/{paper_id}/brief
PATCH /api/v1/papers/{paper_id}/brief
POST  /api/v1/papers/{paper_id}/brief/generate
```

重点看：

```text
02_paper_brief.json
02_paper_brief.md
```

### 03 outline

作用：

```text
根据 context + paper_brief 生成论文大纲。
```

输出中间变量：

```python
outline = {
    "title": "...",
    "authors": [...],
    "abstract": "...",
    "sections": [...],
    "references": [...],
    "algorithms": [...],
    "contributions": [...],
}
```

`sections` 常见字段：

```text
id
title
keyPoints
minWords
hasAlgorithm
hasEquations
hasTables
hasFigures
figureDescriptions
```

重点看：

```text
03_outline.json
```

### 04 outline_gate

作用：

```text
检查 outline 是否满足最低要求。
```

输出中间变量：

```python
outline_gate_issues = [
    "Only 4 sections (need >=5)",
    "Only 6 references (need >=25)",
]
```

当前限制：

```text
只记录问题，不会自动重写 outline。
```

### 05 section_write

作用：

```text
按 outline 逐节生成 LaTeX 正文。
现在 prompt 会带入 paper_brief，所以正文会更贴近用户确认过的写作意图。
```

输出中间变量：

```python
sections = [...]

sections_content = {
    "intro": "\\section{Introduction}...",
    "method": "\\section{Method}...",
    "experiments": "\\section{Experiments}...",
}
```

同时写文件：

```text
backend/data/papers/<paper_id>/latex/sections/*.tex
```

重点看：

```text
sections/experiments.tex
sections/analysis.tex
```

### 06 evidence_gate

作用：

```text
统计正文里的证据结构数量。
```

输出中间变量：

```python
evidence_gates = {
    "algorithms": {"count": 1, "required": 2, "pass": false},
    "equations": {"count": 4, "required": 4, "pass": true},
    "tables": {"count": 3, "required": 3, "pass": true},
    "figures": {"count": 4, "required": 4, "pass": true},
    "citations": {"count": 10, "required": 10, "pass": true},
    "all_pass": false,
}
```

当前限制：

```text
它主要数 LaTeX 标记，还不会验证数据是否真实来自 metrics。
```

### 07 figure_generate

作用：

```text
先接入 linked experiment figures，再生成默认 figures，最后合并成 figure_entries。
```

输出中间变量：

```python
figure_entries = [
    {
        "figureId": "fig_xxx",
        "filename": "fig_xxx_bar_result",
        "ext": "pdf",
        "path": "figures/fig_xxx_bar_result.pdf",
        "caption": "...",
        "label": "fig:fig_xxx",
        "experimentId": "exp_xxx",
        "source": "selected",
    }
]
```

重点看：

```text
07_figure_generate.md
07_figure_generate.json
latex/figures/
```

### 08 assemble_latex

作用：

```text
生成 main.tex、refs.bib、README.md。
同时检查 section 里的 \includegraphics 路径，如果路径不存在，会尽量重写到真实 figure 文件。
```

重点看：

```text
08_assemble_latex.json
figure_rewrites
```

### 09 compile_pdf

作用：

```text
优先用 latexmk 编译。
如果失败，使用 fallback PDF renderer。
```

输出中间变量：

```python
pdf_available = True
```

重点看：

```text
09_compile_pdf.json
status: latexmk / fallback / failed
errors
```

### 10 qa_audit

作用：

```text
汇总 paper_brief、outline_gate_issues、evidence_gates。
```

输出中间变量：

```python
qa_summary = {
    "paper_brief": {...},
    "outline_issues": [...],
    "evidence_gates": {...},
}
```

重点看：

```text
10_qa_audit.json
```

## 6. 最终写入 paper meta 的字段

生成完成后，`service.py` 会更新：

```text
status
templateId
evidenceGates
figureCount
sectionCount
referenceCount
pdfAvailable
```

新增 brief 相关字段：

```text
briefJson
briefUserEdits
briefStatus
```

meta 文件位置：

```text
backend/data/papers/<paper_id>/meta.json
```

## 7. 下一步可优化方向

当前 `paper_brief` 解决的是“生成前先确认写作意图”。后续还可以继续加：

```text
evidence_pack    把 metrics/runs 整理成可验证证据包
figure_plan      明确每张图放在哪一节
table_generate   从真实 metrics 生成 LaTeX 表格
section_revision gate 发现问题后自动重写对应 section
citation_check   检查 cite key 和 refs.bib 是否一致
```
