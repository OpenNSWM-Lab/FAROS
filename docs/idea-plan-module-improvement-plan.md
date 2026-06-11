# Plan 模块具体改动说明

本文档记录本次在 PDF v5 Idea 模块已对齐后的 Plan 模块改动。对应 v5 设计文档保留为：

```text
docs/FAROS-Idea-Module-Plan-v5.pdf
```

输出字段契约见：

```text
docs/idea-plan-output-template-design.md
```

## 1. 改动目标

本次改动聚焦两件事：

1. 优化 Plan 环节：从旧的自由文本 `CandidatePlan` / `ResearchPlan` 生成，升级为消费 Idea v5 产物的 `PlanPackage` 生成。
2. 对齐输出产物：Plan 阶段必须同时输出实施计划字段和科研上下文字段，供后续 code、paper、review、validation 模块消费。

边界：

- idea+plan 阶段只输出 idea 与 idea 实施计划。
- 不执行实验。
- 不生成真实实验结果、benchmark 数值或运行日志。
- 不替代后续 code / experiment / paper / review 模块。

## 2. 新增核心对象

新增主交付对象：

```text
PlanPackage
```

位置：

```text
backend/app/models/plan_package.py
```

`PlanPackage` 是 plan 阶段新的稳定输出。它包含两组必交付内容：

- 实施计划字段：`researchQuestion`、`hypothesis`、`constants`、`stages[].steps[].outputs[]`、`stages[].steps[].expected[]`
- 科研上下文字段：`idea`、`background`、`literatureSurvey`、`gap`、`principle`、`evidenceTrace`、`qualityGate`

旧对象保留：

- `PlanSession`
- `CandidatePlan`
- `ResearchPlan`

这些旧对象用于兼容历史 API 和后续旧链路，不再作为 idea+plan 阶段的主交付。

## 3. 新增模型

新增文件：

```text
backend/app/models/plan_package.py
```

主要模型：

| 模型 | 用途 |
|---|---|
| `PlanPackage` | plan 阶段主交付 |
| `PlanSource` | 上游 idea session、candidate、search tree、KG、map 来源 |
| `PlanIdeaSummary` | final idea 摘要 |
| `PlanBackground` | 背景、动机、当前限制 |
| `PlanLiteratureSurvey` | 所有调研论文总结 |
| `PlanLiteraturePaperSummary` | 单篇论文总结 |
| `PlanGap` / `PlanGapItem` | GAP 列表和选中 GAP |
| `PlanPrinciple` | 方案原理、机制、novelty、assumptions、risks |
| `PlanStage` | 阶段计划 |
| `PlanStep` | 阶段内步骤 |
| `PlanOutput` | 步骤产出 |
| `PlanExpectedMetric` | 预期指标 |
| `PlanEvidenceTrace` | Idea v5 证据链 |
| `PlanDownstreamContract` | 后续模块消费约定 |
| `PlanQualityGate` | schema / evidence / readiness 校验结果 |
| `PlanGenerationMetadata` | 记录 deterministic / hybrid 生成模式、provider、model、fallback |
| `PlanSourceFieldMap` | 记录新字段对齐到哪些 Idea v5 旧字段 |

默认兼容规则：

- `hypothesis` 默认 `""`
- `constants` 默认 `{}`
- `dependsOn` 默认 `[]`
- `inputFrom` 默认 `[]`
- `desc` 默认 `""`

## 4. 新增 Storage

新增文件：

```text
backend/app/storage/plan_package_storage.py
```

职责：

- 创建 `PlanPackage`
- 更新 `PlanPackage`
- 按 `packageId` 查询
- 按 `ideaSessionId` 查询
- 按 `planSessionId` 查询
- 按 `ideaCandidateId` 查询

存储路径统一为：

```text
backend/data/plan_packages
```

注意：本次同时修正了 idea/plan 相关 storage 的路径解析，避免从不同 cwd 启动时写入错误位置。目标路径统一为：

```text
E:\FAROS\backend\data
```

涉及：

- `backend/app/storage/idea_storage.py`
- `backend/app/storage/research_plan_storage.py`
- `backend/app/storage/plan_package_storage.py`
- `backend/app/modules/platform/storage.py` 中的 plan link 路径

## 5. 新增 Service

新增文件：

```text
backend/app/services/plan_package_service.py
backend/app/services/plan_package_builder.py
backend/app/services/plan_package_validator.py
```

### 5.1 `PlanPackageService`

职责：

1. 从 `ideaSessionId` 读取 Idea v5 产物。
2. 选择 candidate：
   - 显式 `candidateId`
   - session 中已选 candidate
   - `RankedIdeaOutput` top candidate
   - 当前最高分 candidate
3. 调用 builder 组装 `PlanPackage`。
4. 按 `generationMode` 生成实施计划：
   - `deterministic`：使用规则 fallback stages。
   - `hybrid`：调用 LLM 生成 `researchQuestion/hypothesis/constants/stages`。
5. 调用 validator 生成 `qualityGate`。
6. 持久化 package。
7. 可选将 `PlanPackage` 转成旧 `ResearchPlan`。

### 5.2 `PlanPackageBuilder`

职责：

- 从 `IdeaCandidate` 组装 `idea`
- 从 `StructuredPaper[]`、`LiteratureMap`、critique 组装 `background`
- 从 Step 3 structured papers 和 Step 5 probe papers 组装 `literatureSurvey`
- 从 `LiteratureMap.gaps`、paper limitations、critique 组装 `gap`
- 从 candidate `proposedMethod`、reasoning trace、path seeds 组装 `principle`
- 从 candidate/search node/path seed/KG/literature map/probe/graph patch 组装 `evidenceTrace`
- 生成默认 `stages[].steps[]` 实施计划
- 写入 `sourceFields`，明确每个 PlanPackage 字段来自哪些 Idea v5 旧字段。
- 写入 `rawIdeaOutputs`，保留旧输出字段的 compact adapter，方便下游模块迁移。

关键约束：

- `background/gap/principle` 不由 Plan LLM 重新生成，只从 Idea v5 产物 adapter 映射。
- Plan LLM 只允许生成或优化 `researchQuestion/hypothesis/constants/stages`。
- LLM 不能伪造 paper / claim / KG / probe / graph patch ID。
- LLM 失败时自动回退到 deterministic stages，并在 `generation.fallbackUsed` 和 `qualityGate.warnings` 中记录。

### 5.3 `PlanPackageValidator`

职责：

- 校验截图要求的实施计划硬字段是否完整。
- 校验科研上下文字段是否完整。
- 校验 `stages[].steps[]` 中的依赖引用是否存在。
- 校验 `outputs[].type` 是否为合法枚举。
- 校验 `literatureSurvey.papers[]` 是否覆盖 structured papers。
- 校验 probe papers 是否独立标记为 `source=probe`。
- 校验 `gap`、`principle`、`evidenceTrace` 是否至少能回溯到 candidate、paper 或 graph evidence。
- 校验 `stages[].steps[].evidenceRefs[]` 只能引用真实 evidence ID。
- 校验 hybrid 模式下 LLM 只写 `implementationPlan` 字段。
- 检查实施计划是否绑定已选 GAP 和 idea/principle。

校验结果写入：

```text
PlanPackage.qualityGate
```

## 6. 新增 API

新增文件：

```text
backend/app/modules/platform/plan_packages_api.py
```

新增接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/plans/packages/from-idea-session/{idea_session_id}` | 从 idea session 创建 PlanPackage |
| `GET` | `/api/v1/plans/packages/{package_id}` | 获取 PlanPackage |
| `GET` | `/api/v1/plans/sessions/{plan_session_id}/package` | 按 plan session 获取 PlanPackage |
| `GET` | `/api/v1/ideas/sessions/{idea_session_id}/plan-package` | 按 idea session 获取 PlanPackage |
| `POST` | `/api/v1/plans/packages/{package_id}/validate` | 重新校验 PlanPackage |
| `POST` | `/api/v1/plans/packages/{package_id}/to-research-plan` | 转成旧 ResearchPlan |

创建请求支持：

```json
{
  "candidateId": "cand_xxx",
  "generationMode": "hybrid",
  "maxStages": 4,
  "maxStepsPerStage": 5,
  "maxRepairRounds": 0,
  "userNotes": ""
}
```

兼容字段：

```text
useLLM=true  -> generationMode=hybrid
useLLM=false -> generationMode=deterministic
```

默认值：

```text
generationMode=hybrid
```

API 挂载位置：

```text
backend/app/modules/platform/router.py
```

## 7. 输入依赖

`PlanPackage` 优先读取以下 Idea v5 产物：

| 产物 | 用途 |
|---|---|
| `IdeaCandidate` | idea、hypothesis、method、scores、embedded evidence |
| `RankedIdeaOutput` | ranking、prior work、critique |
| `StructuredPaper[]` | Step 3 深读论文总结 |
| `LiteratureMap` | gaps、clusters、selected papers |
| `ReasoningKG` | 原理实体、关系、paper grounding |
| `ReasoningPathSeed[]` | reasoning path / mechanism seed |
| `IdeaSearchTree` | search node、operator、BFTS 演化 |
| `LiteratureProbeResult[]` | Step 5 probe 发现的额外论文 |
| `GraphPatch[]` | Step 5 图谱补丁 |
| `BFTSHandoff` | Step 4 到 Step 5 的交接上下文 |

## 8. 输出行为

创建 package 后，返回：

```json
{
  "packageId": "ppkg_xxx",
  "schemaVersion": "plan-package/v2",
  "qualityGate": {},
  "package": {}
}
```

`package` 中必须同时包含：

- `researchQuestion`
- `hypothesis`
- `constants`
- `stages`
- `idea`
- `background`
- `literatureSurvey`
- `gap`
- `principle`
- `evidenceTrace`
- `qualityGate`
- `generation`
- `sourceFields`
- `rawIdeaOutputs`

`steps[]` 只允许嵌套在：

```text
stages[].steps[]
```

不新增根级 `steps`。

## 9. 兼容策略

旧 plan 链路继续保留：

- 旧 `PlanSession` API 不删除。
- 旧 `CandidatePlan` API 不删除。
- 旧 `ResearchPlan` API 不删除。
- 新增 adapter：`PlanPackage -> ResearchPlan`。

后续模块应优先消费 `PlanPackage`。如果旧模块暂时只能读取 `ResearchPlan`，可以通过 adapter 生成兼容对象。

## 10. 验证方式

建议的黑盒测试顺序：

1. 接通 LLM provider。
2. 创建 idea session。
3. 启动 idea pipeline。
4. 获取 `/ideas/sessions/{id}/candidates`，确认 v5 candidate 字段存在。
5. 创建 `/plans/packages/from-idea-session/{id}`。
6. 检查 package 中实施计划字段和科研上下文字段是否同时存在。
7. 调用 `/plans/packages/{package_id}/validate`。
8. 检查 `qualityGate.errors` 和 `qualityGate.warnings`。

## 11. 当前约束

- `generationMode=hybrid` 是默认路径，会调用 LLM 生成实施计划字段。
- `generationMode=deterministic` 关闭 Plan LLM，只使用规则 fallback stages。
- Plan LLM 只允许写 `researchQuestion/hypothesis/constants/stages`。
- `background/gap/principle/literatureSurvey/evidenceTrace` 来自 Idea v5 adapter，不由 Plan LLM 重写。
- Step 5 probe papers 不并入 selected papers，只在 `literatureSurvey` 和 `evidenceTrace` 中独立标记。
- `qualityGate.evidenceValid=false` 时仍返回 package，供前端展示和人工修正。
