# PlanPackage 输出对齐说明

本文档定义 Plan 阶段的最终输出契约。对应模块改动见：

```text
docs/idea-plan-module-improvement-plan.md
```

PDF v5 设计文档保留为：

```text
docs/FAROS-Idea-Module-Plan-v5.pdf
```

## 1. 输出目标

Plan 阶段输出一个完整 `PlanPackage`。它只描述 idea 与 idea 实施计划，不执行实验，不生成真实实验结果。

`PlanPackage` 必须同时满足两类交付要求：

1. 实施计划硬字段：截图要求的计划字段。
2. 科研上下文硬字段：背景、GAP、方案原理、论文总结、证据链和质量校验。

后续模块统一优先消费 `PlanPackage`。

## 2. 顶层结构

```json
{
  "schemaVersion": "plan-package/v2",
  "packageId": "ppkg_xxx",
  "createdAt": "2026-06-10T00:00:00Z",
  "source": {},
  "idea": {},
  "background": {},
  "literatureSurvey": {},
  "gap": {},
  "principle": {},
  "researchQuestion": "",
  "hypothesis": "",
  "constants": {},
  "stages": [],
  "evidenceTrace": {},
  "downstreamContract": {},
  "qualityGate": {},
  "generation": {},
  "sourceFields": {},
  "rawIdeaOutputs": {}
}
```

约束：

- 顶层必须包含截图字段：`researchQuestion`、`hypothesis`、`constants`、`stages`。
- 顶层必须包含科研交付字段：`idea`、`background`、`literatureSurvey`、`gap`、`principle`、`evidenceTrace`、`qualityGate`。
- `steps[]` 只嵌套在 `stages[].steps[]`，不设置根级 `steps`。
- `background/gap/principle/literatureSurvey/evidenceTrace` 是 Idea v5 adapter 字段，Plan LLM 不允许重写。
- `researchQuestion/hypothesis/constants/stages` 是 Plan 实施计划字段，可以由 hybrid LLM planner 生成。

## 3. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schemaVersion` | string | 是 | 固定为 `plan-package/v2` |
| `packageId` | string | 是 | `ppkg_` 前缀 ID |
| `createdAt` | string | 是 | ISO 时间 |
| `source` | object | 是 | 上游 artifact 来源 |
| `idea` | object | 是 | final candidate 摘要 |
| `background` | object | 是 | 研究背景和动机 |
| `literatureSurvey` | object | 是 | 所有调研论文总结 |
| `gap` | object | 是 | GAP 列表和选中 GAP |
| `principle` | object | 是 | 方案原理 |
| `researchQuestion` | string | 是 | 核心研究问题 |
| `hypothesis` | string | 否 | 核心假设，缺省为 `""` |
| `constants` | object | 否 | 固定因素，缺省为 `{}` |
| `stages` | array | 是 | 分阶段实施计划 |
| `evidenceTrace` | object | 是 | v5 证据链 |
| `downstreamContract` | object | 是 | 后续模块消费约定 |
| `qualityGate` | object | 是 | schema/evidence/readiness 校验 |
| `generation` | object | 是 | 生成模式、LLM provider/model、fallback 状态 |
| `sourceFields` | object | 是 | PlanPackage 字段到 Idea v5 旧字段的映射 |
| `rawIdeaOutputs` | object | 是 | 旧 idea 输出 compact adapter，便于下游迁移 |

## 4. `source`

```json
{
  "ideaSessionId": "idea_xxx",
  "planSessionId": "psess_xxx",
  "ideaCandidateId": "cand_xxx",
  "rankedOutputId": "rio_xxx",
  "searchTreeId": "ist_xxx",
  "searchNodeId": "node_xxx",
  "pathSeedId": "rps_xxx",
  "reasoningKgId": "rkg_xxx",
  "literatureMapId": "lm_xxx",
  "bftsHandoffId": "bh_xxx",
  "selectedResearchPlanId": "rp_xxx"
}
```

约束：

- 从 idea session 创建时，`ideaSessionId` 必填。
- 有明确 candidate 时，`ideaCandidateId` 必填。
- `selectedResearchPlanId` 只用于兼容旧 `ResearchPlan`。

## 5. `idea`

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

来源：

- `IdeaCandidate`
- `RankedIdeaOutput.rankedCandidates[]`
- `IdeaSearchTree.nodes[]`

## 5.1 `generation`

```json
{
  "mode": "hybrid",
  "providerName": "deepseek",
  "model": "deepseek-chat",
  "promptVersion": "plan-package-implementation-planner-v1",
  "llmUsedSections": ["implementationPlan"],
  "repairRounds": 0,
  "fallbackUsed": false,
  "warnings": []
}
```

约束：

- `mode` 允许 `hybrid` 或 `deterministic`。
- `hybrid` 模式只允许 LLM 写 `implementationPlan` 字段，即 `researchQuestion/hypothesis/constants/stages`。
- `background/gap/principle/literatureSurvey/evidenceTrace/sourceFields/rawIdeaOutputs` 不允许由 Plan LLM 生成。
- LLM 失败时 `fallbackUsed=true`，系统保留 deterministic stages。

## 5.2 `sourceFields`

```json
{
  "idea": ["IdeaCandidate.title", "IdeaCandidate.proposedMethod"],
  "background": ["StructuredPaper.summary", "LiteratureMap.clusters"],
  "literatureSurvey": ["StructuredPaper[]", "LiteratureProbeResult[].papers"],
  "gap": ["LiteratureMap.gaps", "IdeaCritique.weaknesses"],
  "principle": ["IdeaCandidate.proposedMethod", "ReasoningPathSeed[]"],
  "evidenceTrace": ["IdeaCandidate.searchNodeId", "CandidateGraphEvidence"],
  "implementationPlan": ["LLM implementation planner", "PlanPackage.gap"]
}
```

用途：

- 给 code/paper/review/validation 模块提供稳定字段来源说明。
- 保证背景、GAP、原理直接对齐 Idea v5 已生成内容。

## 5.3 `rawIdeaOutputs`

`rawIdeaOutputs` 保存旧 idea 输出的 compact adapter，不替代正式字段，只用于迁移和调试：

```json
{
  "ideaCandidate": {},
  "candidateGraphEvidence": {},
  "rankedOutput": {},
  "literatureMap": {},
  "reasoningKg": {},
  "reasoningPathSeeds": [],
  "structuredPaperIds": [],
  "probeResultIds": [],
  "graphPatchIds": []
}
```

## 6. `background`

```json
{
  "summary": "",
  "motivation": "",
  "currentLimitations": [],
  "domainContext": [],
  "evidenceRefs": []
}
```

来源：

- `LiteratureMap.gaps`
- `StructuredPaper.limitations`
- `IdeaCandidate.critique`
- `ReasoningKG`

## 7. `literatureSurvey`

```json
{
  "summary": "",
  "coverage": {
    "rawPaperCount": 0,
    "selectedPaperCount": 0,
    "structuredPaperCount": 0,
    "probePaperCount": 0,
    "clusterCount": 0
  },
  "clusters": [],
  "papers": []
}
```

硬约束：

- `papers[]` 必须覆盖 Step 3 deep-read / selected 的 `StructuredPaper[]`，并标记 `source=structured`。
- `papers[]` 必须覆盖 Step 5 literature probe 发现的论文，并标记 `source=probe`。
- probe paper 不并入 selected paper，只在 `literatureSurvey` 和 `evidenceTrace` 中独立标记。
- 每篇论文至少输出 `paperId`、`title`、`source`、`summary`、`methods`、`findings`、`limitations`、`claims`。

### 7.1 `literatureSurvey.papers[]`

```json
{
  "paperId": "raw_xxx",
  "structuredPaperId": "sp_xxx",
  "source": "structured",
  "title": "",
  "authors": [],
  "year": 2026,
  "venue": "",
  "url": "",
  "role": "supporting_evidence",
  "summary": "",
  "methods": [],
  "findings": [],
  "limitations": [],
  "claims": [],
  "usedByStageIds": [],
  "usedByStepIds": [],
  "evidenceRefs": []
}
```

`source` 允许值：

```text
structured | probe
```

`role` 常用值：

```text
supporting_evidence | prior_work | baseline | contradiction | dataset | metric | background
```

## 8. `gap`

```json
{
  "summary": "",
  "items": [
    {
      "id": "gap-1",
      "statement": "",
      "severity": "medium",
      "whyUnsolved": "",
      "supportedByPaperIds": [],
      "supportedByClaimIds": [],
      "linkedGraphSignalIds": []
    }
  ],
  "selectedGapId": "gap-1"
}
```

约束：

- 至少 1 条 `items[]`。
- `selectedGapId` 必须存在于 `items[].id`。
- 每条 gap 应至少能回溯到 paper、claim 或 graph signal。

## 9. `principle`

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

来源：

- `IdeaCandidate.proposedMethod`
- `IdeaCandidate.keyInsight`
- `CandidateGraphEvidence.reasoningTrace`
- `ReasoningPathSeed[]`
- `ReasoningKG`
- `LiteratureProbeResult[]`
- `GraphPatch[]`

## 10. 实施计划字段

截图要求的字段直接落在顶层和 `stages[].steps[]` 内。

### 10.1 顶层计划字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `researchQuestion` | string | 是 | 核心研究问题 |
| `hypothesis` | string | 否 | 核心假设 |
| `constants` | object | 否 | 固定不变因素，例如数据集、模型、硬件等 |
| `stages` | array | 是 | 阶段列表 |

### 10.2 `stages[]`

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

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 建议 `stage-N` |
| `order` | number | 是 | 阶段序号 |
| `title` | string | 是 | 阶段名称 |
| `goal` | string | 是 | 阶段目标 |
| `method` | string | 是 | 阶段总体方法 |
| `dependsOn` | string[] | 否 | 依赖阶段 ID，缺省 `[]` |
| `steps` | array | 是 | 阶段内步骤 |

### 10.3 `stages[].steps[]`

```json
{
  "id": "step-1",
  "order": 1,
  "title": "",
  "desc": "",
  "method": "",
  "inputFrom": [],
  "outputs": [],
  "expected": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 全局唯一 step ID |
| `order` | number | 是 | 阶段内序号 |
| `title` | string | 是 | 步骤名称 |
| `desc` | string | 是 | 步骤详细描述 |
| `method` | string | 是 | 具体方法 |
| `inputFrom` | string[] | 否 | 依赖 step ID，缺省 `[]` |
| `outputs` | array | 是 | 产出物 |
| `expected` | array | 是 | 预期指标 |

### 10.4 `outputs[]`

```json
{
  "type": "report",
  "name": "",
  "desc": ""
}
```

合法 `type`：

```text
metrics | chart | table | checkpoint | code | report | log
```

### 10.5 `expected[]`

```json
{
  "metric": "",
  "target": "",
  "desc": ""
}
```

说明：

- `metric` 是预期指标名称。
- `target` 是预期目标值，使用字符串表示，允许自然语言。
- `desc` 是指标说明，缺省可为 `""`。

## 11. `evidenceTrace`

```json
{
  "candidateId": "cand_xxx",
  "searchNodeId": "node_xxx",
  "pathSeedId": "rps_xxx",
  "reasoningPathId": "rp_xxx",
  "reasoningKgId": "rkg_xxx",
  "literatureMapId": "lm_xxx",
  "searchTreeId": "ist_xxx",
  "structuredPaperIds": [],
  "rawPaperIds": [],
  "probeResultIds": [],
  "probePaperIds": [],
  "graphPatchIds": [],
  "reasoningTrace": [],
  "stageEvidence": {},
  "stepEvidence": {}
}
```

约束：

- candidate/search node/path seed/KG/map/tree 有则必须保留 ID。
- `probeResultIds` 和 `probePaperIds` 必须独立保留。
- `stageEvidence`、`stepEvidence` 用于把阶段/步骤回溯到 paper、claim、graph signal。

## 12. `qualityGate`

```json
{
  "schemaValid": true,
  "evidenceValid": true,
  "implementationReady": true,
  "errors": [],
  "warnings": []
}
```

语义：

- `schemaValid=false`：字段结构不满足硬 schema。
- `evidenceValid=false`：存在无法回溯的 gap/principle/step evidence。
- `implementationReady=false`：计划字段存在，但不足以后续实现模块直接消费。
- 即使存在 warning 或 evidence invalid，API 仍返回 package，前端可展示并人工修正。

## 13. 下游消费约定

| 下游模块 | 优先读取字段 |
|---|---|
| code | `researchQuestion`、`constants`、`stages[].steps[]`、`outputs[]`、`expected[]`、`downstreamContract.code` |
| paper | `idea`、`background`、`literatureSurvey`、`gap`、`principle`、`evidenceTrace` |
| review | `qualityGate`、`critique`、`gap`、`closestPriorWork`、`evidenceTrace` |
| validation | `expected[]`、`outputs[]`、`qualityGate`、`evidenceTrace` |

旧 `ResearchPlan` 只作为兼容对象，不作为 Plan 阶段的主输出。
