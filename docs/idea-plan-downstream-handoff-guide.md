# Idea+Plan 下游模块接入说明

本文档说明 code、experiment、paper、review、validation 等后续模块如何使用 idea+plan 阶段的产出，以及主要字段的含义。

相关设计文档：

```text
docs/FAROS-Idea-Module-Plan-v5.pdf
docs/idea-plan-module-improvement-plan.md
docs/idea-plan-output-template-design.md
```

## 1. 接入结论

后续模块默认消费：

```http
GET /api/v1/plans/packages/{package_id}/handoff
```

返回对象：

```text
PlanPackageHandoff
```

不要让后续模块默认读取完整 `PlanPackage`。完整包包含 reviewer 报告、rawIdeaOutputs、sourceFields、humanFeedback、revision history 等审计/调试信息，字段多且会让下游耦合过重。

三层视图分工如下：

| 对象 | 面向对象 | 主要用途 |
|---|---|---|
| `PlanPackage` | 内部审计、调试、追踪 | 完整 idea+plan 事实来源 |
| `PlanPackagePresentation` | 前端和用户 | 人可读展示，隐藏大量内部 ID |
| `PlanPackageHandoff` | 后续模块 | 精简机器接口，默认接入对象 |

## 2. 交付状态

`PlanPackageHandoff.idea` 应来自 idea Step 6 的 ranked output。该阶段会先应用 idea review gate：`PriorWorkComparison`、`IdeaCritique` 和 graph evidence 会影响候选排序；如果 top candidate 存在 critique、suggested improvements 或 evidence warning，系统会尝试生成反馈优化候选再参与排序。因此后续模块不应把 Plan reviewer 当成 idea 初审入口。

下游模块在正式执行前应检查：

```json
{
  "status": "approved",
  "qualityGate": {
    "schemaValid": true,
    "evidenceValid": true,
    "topicRelevant": true,
    "citationFaithful": true,
    "planSpecific": true,
    "agentApproved": true,
    "humanApproved": true,
    "implementationReady": true
  }
}
```

推荐规则：

| 场景 | 可否继续 | 说明 |
|---|---|---|
| `status=approved` 且 `implementationReady=true` | 可以正式交给后续模块 | 标准交付状态 |
| `status=needs_human_review` 且 `agentApproved=true` | 可用于预览/草稿联调 | 还缺人工批准 |
| `status=needs_revision` | 不建议继续 | 需要用户反馈或自动修订 |
| `qualityGate.errors` 非空 | 不建议继续 | schema/evidence/计划字段存在硬问题 |
| `qualityGate.warnings` 非空 | 可以继续但要展示风险 | 通常是证据弱、相关性不足或计划不够具体 |

## 3. API 使用方式

### 3.1 已知 packageId

后续模块拿到 `packageId` 后直接取 handoff：

```http
GET /api/v1/plans/packages/{package_id}/handoff
```

### 3.2 只有 ideaSessionId

先按 idea session 找 package，再取 handoff：

```http
GET /api/v1/ideas/sessions/{idea_session_id}/plan-package
GET /api/v1/plans/packages/{package_id}/handoff
```

### 3.3 调试和审计

```http
GET /api/v1/plans/packages/{package_id}
```

仅用于 debug/audit，不建议 code、paper、review 等模块直接依赖完整包。

### 3.4 前端展示

```http
GET /api/v1/plans/packages/{package_id}/presentation
```

仅用于 UI 展示，不建议后端模块消费。该视图为了可读性会压缩、改名和隐藏部分机器字段。

## 4. PlanPackageHandoff 顶层结构

```json
{
  "schemaVersion": "plan-package-handoff/v1",
  "packageId": "ppkg_xxx",
  "status": "approved",
  "idea": {},
  "researchQuestion": "",
  "hypothesis": "",
  "constants": {},
  "backgroundSummary": "",
  "selectedGap": {},
  "principle": {},
  "contributionStatement": [],
  "keyPapers": [],
  "stages": [],
  "qualityGate": {},
  "evidenceTrace": {},
  "downstreamContract": {}
}
```

## 5. 顶层字段含义

| 字段 | 类型 | 含义 | 下游使用建议 |
|---|---|---|---|
| `schemaVersion` | string | handoff schema 版本 | 用于兼容判断，当前为 `plan-package-handoff/v1` |
| `packageId` | string | PlanPackage 唯一 ID | 下游所有 artifact 建议记录该 ID |
| `status` | string | package 生命周期状态 | 正式运行优先要求 `approved` |
| `idea` | object | 最终选择的 idea 摘要 | paper/review/code prompt 的核心题目来源 |
| `researchQuestion` | string | 研究问题、对象、场景和边界 | 所有后续模块的任务主线 |
| `hypothesis` | string | 核心假设和预期提升 | experiment/validation/review 判断计划是否可检验 |
| `constants` | object | 固定条件、约束和上下文 | code/experiment 生成配置时优先读取 |
| `backgroundSummary` | string | 研究背景摘要 | paper 引言、review 背景一致性检查 |
| `selectedGap` | object | 被选中的核心研究缺口 | paper/review/validation 的主要论证对象 |
| `principle` | object | 方案原理、机制、创新点 | code 设计、paper 方法、review novelty 检查 |
| `contributionStatement` | array | 贡献声明及其验证映射 | paper contribution、review checklist |
| `keyPapers` | array | 关键论文摘要 | paper related work、review citation checks |
| `stages` | array | 分阶段实施计划 | code/experiment/validation 的主输入 |
| `qualityGate` | object | schema/evidence/计划质量门结果 | 下游是否继续执行的 gate |
| `evidenceTrace` | object | idea v5 证据链关键 ID | 调用上游证据详情、审计追踪 |
| `downstreamContract` | object | 后续模块消费约定 | 告诉各模块应读哪些字段、应产出什么 |

## 6. 核心子字段

### 6.1 `idea`

```json
{
  "id": "cand_xxx",
  "title": "",
  "problem": "",
  "hypothesisStatement": "",
  "keyInsight": "",
  "proposedMethod": "",
  "expectedOutcome": "",
  "scores": {},
  "critiqueSummary": "",
  "closestPriorWork": []
}
```

| 字段 | 含义 |
|---|---|
| `id` | idea candidate ID |
| `title` | idea 标题 |
| `problem` | 研究问题的原始描述 |
| `hypothesisStatement` | idea 阶段生成的假设表述 |
| `keyInsight` | 核心洞察 |
| `proposedMethod` | 原始方案方法 |
| `expectedOutcome` | 预期结果，不是真实实验结果 |
| `scores` | idea 评分，如 novelty、feasibility、impact 等 |
| `critiqueSummary` | idea 阶段 critique 摘要 |
| `closestPriorWork` | 最接近已有工作的摘要或引用信息 |

### 6.2 `selectedGap`

```json
{
  "id": "gap-1",
  "kind": "selected",
  "statement": "",
  "severity": "medium",
  "existingCoverage": "",
  "unresolvedIssue": "",
  "proposedEntry": "",
  "boundary": "",
  "validationNeeds": [],
  "whyUnsolved": "",
  "supportedByPaperIds": [],
  "supportedByClaimIds": [],
  "linkedGraphSignalIds": []
}
```

| 字段 | 含义 |
|---|---|
| `statement` | GAP 的一句话定义 |
| `existingCoverage` | 现有工作已经覆盖的部分 |
| `unresolvedIssue` | 仍未解决的问题 |
| `proposedEntry` | 本 idea 准备切入的位置 |
| `boundary` | 不打算解决的边界 |
| `validationNeeds` | 需要通过后续实验/分析验证的点 |
| `whyUnsolved` | 为什么现有方法尚未解决 |
| `supportedByPaperIds` | 支撑该 GAP 的论文 ID |
| `supportedByClaimIds` | 支撑该 GAP 的 claim ID |
| `linkedGraphSignalIds` | KG 或 search graph 中的证据信号 |

### 6.3 `principle`

```json
{
  "summary": "",
  "mechanism": "",
  "noveltyClaim": "",
  "assumptions": [],
  "risks": [],
  "reasoningPath": [],
  "graphGrounding": {},
  "probeGrounding": {}
}
```

| 字段 | 含义 |
|---|---|
| `summary` | 方案原理摘要 |
| `mechanism` | 输入、处理过程、输出和核心机制 |
| `noveltyClaim` | 与已有方法相比的创新性声明 |
| `assumptions` | 方案成立依赖的前提 |
| `risks` | 可能失败或不稳定的风险 |
| `reasoningPath` | idea 生成时的结构化推理路径 |
| `graphGrounding.entityIds` | KG 实体证据 |
| `graphGrounding.relationIds` | KG 关系证据 |
| `graphGrounding.pathSeedIds` | reasoning path seed 来源 |
| `probeGrounding.probeResultIds` | Step 5 probe 结果 |
| `probeGrounding.graphPatchIds` | probe 产生的 graph patch |
| `probeGrounding.probePaperIds` | probe 发现的论文 |

### 6.4 `stages[]`

`stages` 是下游 code/experiment/validation 最重要的字段。它描述计划，不表示已执行结果。

```json
{
  "id": "stage-1",
  "order": 1,
  "title": "",
  "goal": "",
  "method": "",
  "dependsOn": [],
  "steps": []
}
```

| 字段 | 含义 |
|---|---|
| `id` | stage 稳定 ID |
| `order` | 执行顺序 |
| `title` | 阶段标题 |
| `goal` | 阶段目标 |
| `method` | 阶段级方法 |
| `dependsOn` | 依赖的 stage ID |
| `steps` | 阶段内具体步骤 |

### 6.5 `stages[].steps[]`

```json
{
  "id": "step-1-1",
  "order": 1,
  "title": "",
  "desc": "",
  "method": "",
  "inputFrom": [],
  "outputs": [],
  "expected": [],
  "evidenceRefs": [],
  "codeHints": {}
}
```

| 字段 | 含义 |
|---|---|
| `id` | step 稳定 ID，后续 artifact 应记录该 ID |
| `order` | step 在 stage 内的顺序 |
| `title` | 步骤标题 |
| `desc` | 具体计划动作 |
| `method` | 执行方法或分析方法 |
| `inputFrom` | 依赖的 step ID |
| `outputs` | 计划产物 |
| `expected` | 预期指标或验收标准 |
| `evidenceRefs` | 该步骤依赖的 GAP、paper、principle 等证据 |
| `codeHints` | 给 code 模块的可选实现提示 |

### 6.6 `outputs[]`

```json
{
  "type": "metrics",
  "name": "baseline_metrics.json",
  "desc": "",
  "requiredFor": []
}
```

合法 `type`：

```text
metrics | chart | table | checkpoint | code | report | log
```

使用建议：

| type | 下游含义 |
|---|---|
| `metrics` | 指标 JSON、评估结果文件 |
| `chart` | 图表，如曲线图、柱状图、Pareto 图 |
| `table` | CSV/表格 |
| `checkpoint` | 模型权重或中间状态 |
| `code` | 脚本、模块或配置 |
| `report` | 分析报告或 markdown |
| `log` | 运行日志 |

### 6.7 `expected[]`

```json
{
  "metric": "citation faithfulness",
  "target": "improves over baseline",
  "desc": "checks whether generated claims remain grounded in retrieved evidence"
}
```

注意：

- `expected` 是计划目标，不是真实实验结果。
- 后续 experiment/validation 模块应把真实测量值另存为自己的 run/result artifact。
- paper 模块不能把 `expected.target` 写成已完成实验结论。

### 6.8 `keyPapers[]`

```json
{
  "paperId": "paper_xxx",
  "title": "",
  "source": "structured",
  "relevanceScore": 0.82,
  "summary": "",
  "methods": [],
  "findings": [],
  "limitations": [],
  "supports": []
}
```

| 字段 | 含义 |
|---|---|
| `source=structured` | Step 3 deep-read / selected paper |
| `source=probe` | Step 5 literature probe 发现的论文 |
| `relevanceScore` | 与当前主题的相关性评分，0-1 |
| `methods` | 论文方法总结 |
| `findings` | 论文主要发现 |
| `limitations` | 论文局限 |
| `supports` | 该论文在当前 package 中支持什么 |

使用建议：

- paper 模块可用 `keyPapers` 写 related work 和 evidence-aware background。
- review 模块应结合 `selectedGap.supportedByPaperIds` 和 `keyPapers[].paperId` 检查 GAP 是否有证据支撑。
- 如果 `relevanceScore < 0.45`，不要把该论文作为强证据。

### 6.9 `qualityGate`

```json
{
  "schemaValid": true,
  "evidenceValid": true,
  "topicRelevant": true,
  "citationFaithful": true,
  "planSpecific": true,
  "agentApproved": true,
  "humanApproved": true,
  "implementationReady": true,
  "overallScore": 0.86,
  "reviewDecision": "approved",
  "warnings": [],
  "errors": []
}
```

| 字段 | 含义 |
|---|---|
| `schemaValid` | 字段结构完整、类型合法 |
| `evidenceValid` | evidence refs 能回溯到真实上游证据 |
| `topicRelevant` | 论文、GAP、计划没有明显偏离 seed query |
| `citationFaithful` | citation/paper/claim/KG 引用没有明显伪造 |
| `planSpecific` | stages/steps/outputs/expected 足够具体 |
| `agentApproved` | reviewer committee 通过 |
| `humanApproved` | 人类确认可交付 |
| `implementationReady` | 可以进入 code/experiment 阶段 |
| `overallScore` | 0-1 综合质量评分 |
| `reviewDecision` | `draft/revise/reject/approve/approved` |
| `warnings` | 可继续但需要展示的风险 |
| `errors` | 不建议继续的硬问题 |

### 6.10 `evidenceTrace`

```json
{
  "ideaCandidateId": "cand_xxx",
  "searchNodeId": "node_xxx",
  "pathSeedId": "rps_xxx",
  "reasoningKgId": "rkg_xxx",
  "literatureMapId": "lm_xxx",
  "selectedPaperIds": [],
  "structuredPaperIds": [],
  "probePaperIds": []
}
```

用途：

- 让下游 artifact 能回溯到 idea candidate、search node、reasoning path、KG、literature map。
- paper/review 模块需要更完整证据时，可用这些 ID 再访问完整 `PlanPackage` 或 idea 模块对应 API。
- `probePaperIds` 与 `selectedPaperIds/structuredPaperIds` 保持独立，不应混为 selected paper。

### 6.11 `downstreamContract`

`downstreamContract` 是给后续模块的消费建议，不是强制 schema。

默认语义：

```json
{
  "implementation": {
    "consume": ["researchQuestion", "hypothesis", "constants", "stages"],
    "requiredOutputs": ["metrics", "table", "chart", "log"]
  },
  "code": {
    "consume": ["stages.steps", "steps.outputs", "constants", "principle"],
    "requiredOutputs": ["code", "checkpoint", "log", "metrics"]
  },
  "paper": {
    "consume": ["background", "literatureSurvey", "gap", "principle", "contributionStatement", "stages", "evidenceTrace"],
    "requiredOutputs": ["table", "chart", "report"]
  },
  "review": {
    "consume": ["idea", "gap", "principle", "contributionStatement", "qualityGate", "evidenceTrace"],
    "requiredOutputs": ["report"]
  }
}
```

## 7. 各模块使用建议

### 7.1 code 模块

主要读取：

```text
researchQuestion
hypothesis
constants
principle
stages[].steps[]
stages[].steps[].outputs[]
stages[].steps[].expected[]
stages[].steps[].codeHints
```

建议行为：

- 以 `stages[].steps[]` 生成任务列表。
- 每个生成的代码 artifact 记录 `packageId/stageId/stepId`。
- 按 `outputs[].type=code/checkpoint/log/metrics` 决定要生成脚本、配置、日志还是指标收集逻辑。
- 不要把 `expected[]` 当成真实结果，只能作为验收目标。

### 7.2 experiment / validation 模块

主要读取：

```text
constants
stages
steps.expected
qualityGate
evidenceTrace
```

建议行为：

- 将 `expected[]` 转换为可执行的验证项。
- 将真实运行结果另存为 run/result artifact，并记录 `packageId/stageId/stepId`。
- 如果 `qualityGate.implementationReady=false`，默认阻止正式运行，除非用户显式选择强制执行。
- 真实结果产生后不要回写为 `PlanPackage.expected`，而应生成新的 experiment/run 输出。

### 7.3 paper 模块

主要读取：

```text
idea
researchQuestion
hypothesis
backgroundSummary
selectedGap
principle
contributionStatement
keyPapers
stages
evidenceTrace
```

建议行为：

- `backgroundSummary` 用于 Introduction 背景草稿。
- `selectedGap` 用于 Related Work 之后的 gap paragraph。
- `principle` 用于 Method section。
- `contributionStatement` 用于 Contribution list。
- `keyPapers` 用于 Related Work，但需要保留 paperId 以便 citation grounding。
- `stages/expected` 只能写成实验计划或待验证假设，不能写成实验结果。

### 7.4 review 模块

主要读取：

```text
idea
selectedGap
principle
contributionStatement
keyPapers
qualityGate
evidenceTrace
```

建议行为：

- 检查 `selectedGap.statement` 是否真的由 `supportedByPaperIds` 和 `keyPapers` 支撑。
- 检查 `principle.noveltyClaim` 是否和 `closestPriorWork/keyPapers` 有清晰差异。
- 检查 `contributionStatement[].validationStageIds` 和 `validationStepIds` 是否能对应到真实 stage/step。
- 将 review 输出与 `packageId` 绑定，方便人类反馈再回流到 plan 修订。

### 7.5 frontend / product UI

主要读取：

```http
GET /api/v1/plans/packages/{package_id}/presentation
```

建议行为：

- 默认展示 `PlanPackagePresentation`。
- 不要把完整 `PlanPackage` 的 raw graph、sourceFields、reviewReports 全量暴露给普通用户。
- 用户反馈只需要自然语言，系统自动推断修订字段。
- `Validate/Review/Revise` 是内部质量门，不应作为普通用户主按钮展示。

## 8. 下游 artifact 绑定规范

后续模块产生任何 artifact 时，建议至少记录：

```json
{
  "packageId": "ppkg_xxx",
  "ideaCandidateId": "cand_xxx",
  "stageId": "stage-1",
  "stepId": "step-1-1",
  "sourceEvidenceRefs": []
}
```

如果 artifact 对应论文、图表、指标或代码，建议再记录：

```json
{
  "outputType": "metrics",
  "outputName": "baseline_metrics.json",
  "requiredFor": ["paper", "review"]
}
```

这样后续 paper/review 可以从实验结果反查到原始 idea、GAP、证据和计划步骤。

## 9. 不要做的事

- 不要把 `expected[]` 当成实验结果。
- 不要把 `PlanPackagePresentation` 作为后端模块的稳定机器接口。
- 不要默认读取完整 `PlanPackage` 中的 `rawIdeaOutputs`，除非做迁移或调试。
- 不要改写 `evidenceTrace`、paper ID、KG ID、probe ID、graph patch ID。
- 不要在后续模块里重新解释 `selectedGap` 的 ID 语义；需要补充时应生成自己的 review/experiment artifact。
- 不要在 `qualityGate.errors` 非空时静默继续正式执行。

## 10. 最小接入伪代码

```python
package = get_json(f"/api/v1/plans/packages/{package_id}/handoff")

gate = package["qualityGate"]
if package["status"] != "approved" or not gate["implementationReady"]:
    raise RuntimeError("PlanPackage is not ready for downstream execution")

for stage in package["stages"]:
    for step in stage["steps"]:
        create_task(
            package_id=package["packageId"],
            stage_id=stage["id"],
            step_id=step["id"],
            title=step["title"],
            method=step["method"],
            expected=step["expected"],
            outputs=step["outputs"],
        )
```

## 11. 当前边界

- idea+plan 阶段只交付 idea 和实施计划，不执行实验。
- PlanPackage 中所有指标目标都是计划目标，不是已观测结果。
- `keyPapers` 是精简论文视图；如果 paper 模块需要完整 claims/methods/findings，应通过完整 `PlanPackage.literatureSurvey.papers[]` 或 idea 模块论文 API 获取。
- `qualityGate.agentApproved=true` 不等于用户批准；正式交付建议要求 `humanApproved=true`。
- 后续模块可以先按 `PlanPackageHandoff` 打通主链路，再逐步接入完整证据详情。
