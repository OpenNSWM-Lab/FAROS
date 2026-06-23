# Plan 模块具体改动说明

本文档记录本次在 PDF v5 Idea 模块已对齐后的 Plan 模块改动。对应 v5 设计文档保留为：

```text
docs/FAROS-Idea-Module-Plan-v5.pdf
```

输出字段契约见：

```text
docs/idea-plan-output-template-design.md
```

后续模块接入指南见：

```text
docs/idea-plan-downstream-handoff-guide.md
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

`PlanPackage` 是 idea+plan 阶段唯一交付对象。旧 `ResearchPlan` API、模型、storage 和转换 adapter 已删除。

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
| `PlanContributionStatement` | 贡献声明及其 stage/step/evidence 映射 |
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
- `backend/app/storage/plan_package_storage.py`
- `backend/app/modules/platform/storage.py` 中的 plan link 路径

## 5. 新增 Service

新增文件：

```text
backend/app/services/plan_package_service.py
backend/app/services/plan_package_builder.py
backend/app/services/plan_package_validator.py
backend/app/services/plan_package_reviewers.py
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
5. 调用 validator 生成基础 `qualityGate`。
6. 调用 reviewer committee 生成 `reviewReports/metaReview`。
7. 如果 reviewer 发现可由 plan 字段修复的问题，自动进入 repair loop 重新生成 `researchQuestion/hypothesis/constants/stages/expectedMetrics`。
8. 根据 repair 后 reviewer 结果设置 `status`：通过 agent 审核则进入 `needs_human_review`，否则进入 `needs_revision`。
8. 持久化 package。

### 5.2 `PlanPackageBuilder`

职责：

- 从 `IdeaCandidate` 组装 `idea`
- 从 `StructuredPaper[]`、`LiteratureMap`、critique 组装 `background`
- 从 Step 3 structured papers 和 Step 5 probe papers 组装 `literatureSurvey`
- 为每篇 `literatureSurvey.papers[]` 计算 `relevanceScore/relevanceSignals/relevanceReason`
- 从 `LiteratureMap.gaps`、paper limitations、critique 组装 `gap`
- 从 candidate `proposedMethod`、reasoning trace、path seeds 组装 `principle`
- 从 candidate/search node/path seed/KG/literature map/probe/graph patch 组装 `evidenceTrace`
- 生成默认 `stages[].steps[]` 实施计划
- 写入 `sourceFields`，明确每个 PlanPackage 字段来自哪些 Idea v5 旧字段。
- 写入 `rawIdeaOutputs`，保留旧输出字段的 compact adapter，方便下游模块迁移。

关键约束：

- `background/gap/principle` 默认从 Idea v5 产物 adapter 映射。
- Plan LLM 默认只生成或优化 `researchQuestion/hypothesis/constants/stages`；在人类反馈明确指向时，可受控修订 `background/gap/principle` 的文字表达。
- LLM 不能伪造 paper / claim / KG / probe / graph patch ID。
- LLM 失败时自动回退到 deterministic stages，并在 `generation.fallbackUsed` 和 `qualityGate.warnings` 中记录。

### 5.3 `PlanPackageValidator`

职责：

- 校验截图要求的实施计划硬字段是否完整。
- 校验科研上下文字段是否完整。
- 校验 `stages[].steps[]` 中的依赖引用是否存在。
- 校验 `outputs[].type` 是否为合法枚举。
- 校验 `literatureSurvey.papers[]` 是否覆盖 structured papers。
- 校验 literature relevance metadata，并提示低相关论文造成的证据池污染风险。
- 如果 selected GAP 只由低相关论文支撑，则 `evidenceValid=false`。
- 校验 probe papers 是否独立标记为 `source=probe`。
- 校验 `gap`、`principle`、`evidenceTrace` 是否至少能回溯到 candidate、paper 或 graph evidence。
- 校验 `stages[].steps[].evidenceRefs[]` 只能引用真实 evidence ID。
- 校验 hybrid 模式下 LLM 只写 `implementationPlan` 字段。
- 检查实施计划是否绑定已选 GAP 和 idea/principle。
- 生成 `topicRelevant/citationFaithful/planSpecific/agentApproved/humanApproved/overallScore/reviewDecision` 等语义门控字段。

校验结果写入：

```text
PlanPackage.qualityGate
```

### 5.4 `PlanPackageReviewerCommittee`

职责：

- `RelevanceReviewer`：检查论文和计划是否与 seed query / selected idea 对齐。
- `EvidenceReviewer`：检查 GAP、background、principle、step evidence refs 是否可信。
- `FeasibilityReviewer`：检查 stages/steps 是否足够具体，是否能交给 code/experiment 模块。
- `MetricReviewer`：检查 expected metrics 是否具体可验证。
- `NoveltyReviewer`：检查 selected GAP、novelty claim、contribution statement 是否清晰。
- `reviewerMode=hybrid` 时，上述 5 个方向各自追加一次同方向 LLM semantic review，并与规则分数/问题合并为同一个 reviewer report。
- `MetaReviewer`：汇总 reviewer 分数、blocking issues、repair suggestions，并给出 `approve/revise/reject` 决策。
- reviewer blocking issues 会进入 LLM repair prompt，修订不再只依赖 JSON 解析成功。
- plan-owned reviewer findings 会自动触发 repair loop；用户不需要手动点 Review/Revise。
- idea/GAP/证据/novelty 这类上游质量问题前置到 idea Step 6 的 review gate。该 gate 不只是筛掉差候选，还会把 critique、prior-work comparison、evidence warning 转成 feedback optimization，生成更强候选后重新评分和排序。

reviewer 模式：

```text
hybrid = 每个方向 reviewer 都执行 rule check + focused LLM semantic review，默认审核路径，适合正式交付前质量审核
deterministic = 只运行规则 reviewer，稳定、低成本，适合离线测试或节省 token
```

reviewer 输出写入：

```text
PlanPackage.reviewReports
PlanPackage.metaReview
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
| `GET` | `/api/v1/plans/packages/{package_id}` | 获取完整 PlanPackage，仅用于 Debug / Audit |
| `GET` | `/api/v1/plans/packages/{package_id}/presentation` | 获取用户可读展示视图 |
| `GET` | `/api/v1/plans/packages/{package_id}/handoff` | 获取后续模块精简 handoff 视图 |
| `GET` | `/api/v1/ideas/sessions/{idea_session_id}/plan-package` | 按 idea session 获取完整 PlanPackage |
| `GET` | `/api/v1/ideas/sessions/{idea_session_id}/plan-package/presentation` | 按 idea session 获取用户可读展示视图 |
| `POST` | `/api/v1/plans/packages/{package_id}/validate` | 重新校验 PlanPackage |
| `POST` | `/api/v1/plans/packages/{package_id}/review` | 运行 reviewer committee |
| `POST` | `/api/v1/plans/packages/{package_id}/feedback` | 写入人类反馈 |
| `POST` | `/api/v1/plans/packages/{package_id}/revise` | 基于反馈和 reviewer 发现修订 PlanPackage |
| `POST` | `/api/v1/plans/packages/{package_id}/approve` | 人工批准交付给后续模块 |

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
  "schemaVersion": "plan-package/v4",
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
- `reviewReports`
- `metaReview`
- `humanFeedback`
- `revisions`
- `generation`
- `sourceFields`
- `rawIdeaOutputs`

`steps[]` 只允许嵌套在：

```text
stages[].steps[]
```

不新增根级 `steps`。

## 9. 单轨交付策略

- idea session 选择最终 candidate。
- plan 模块从 idea session 生成唯一的 `PlanPackage`。
- 完整 `PlanPackage` 作为内部事实来源保留。
- 前端默认展示 `PlanPackagePresentation`，避免把 raw ID、sourceFields、review object 和 raw graph payload 暴露给用户。
- 前端主流程不暴露 `Validate/Review/Revise` 内部质检按钮；这些质量门在生成、反馈修订和批准前自动执行，API 端点保留给调试、自动化测试和后续服务编排。
- reviewer 发现 plan-owned 问题时会自动修复并重新审核；idea-owned 问题应由 idea Step 6 review gate 影响 candidate 排名，并在有 critique/suggestion/evidence warning 时触发候选反馈优化。
- code、paper、review、run 等后续模块默认消费 `PlanPackageHandoff`。
- 不再创建、转换或持久化 `ResearchPlan`。

## 10. 验证方式

建议的黑盒测试顺序：

1. 接通 LLM provider。
2. 创建 idea session。
3. 启动 idea pipeline。
4. 获取 `/ideas/sessions/{id}/candidates`，确认 v5 candidate 字段存在。
5. 创建 `/plans/packages/from-idea-session/{id}`。
6. 打开 `/plans/packages/{package_id}/presentation`，检查用户可读展示内容。
7. 打开 `/plans/packages/{package_id}/handoff`，检查下游模块精简交付字段。
8. 调用 `/plans/packages/{package_id}/validate`。
9. 调用 `/plans/packages/{package_id}/review`，检查 `reviewReports/metaReview`。
10. 如存在问题，调用 `/plans/packages/{package_id}/feedback` 写入用户自然语言反馈。
11. 调用 `/plans/packages/{package_id}/revise` 基于反馈修订；不传 `targetSections` 时由后端根据未解决反馈自动推断修订范围。
12. 通过后调用 `/plans/packages/{package_id}/approve`，让 `status=approved`。

`revise` 请求可传：

```json
{
  "generationMode": "hybrid",
  "reviewerMode": "hybrid",
  "targetSections": ["stages", "expectedMetrics"],
  "maxRepairRounds": 2
}
```

`targetSections` 允许值：

```text
researchQuestion | hypothesis | constants | stages | expectedMetrics | background | gap | principle
```

前端默认不暴露 `targetSections`，用户只需要写自然语言意见并触发修订。服务端会根据反馈内容自动推断：

- 研究问题、场景、边界相关反馈 -> `researchQuestion`
- 假设、预期提升相关反馈 -> `hypothesis`
- 数据集、模型、硬件、约束相关反馈 -> `constants`
- 实施计划、实验步骤、对比/消融相关反馈 -> `stages`
- 指标、输出、评估相关反馈 -> `expectedMetrics`
- 背景、GAP、原理相关反馈 -> 对应修订 `background/gap/principle` 的表达

## 11. 当前约束

- `generationMode=hybrid` 是默认路径，会调用 LLM 生成实施计划字段。
- `generationMode=deterministic` 关闭 Plan LLM，只使用规则 fallback stages。
- Plan LLM 默认只写 `researchQuestion/hypothesis/constants/stages`。
- 人类反馈明确要求时，Plan LLM 可受控修订 `background/gap/principle` 的文字表达，但不能新增、删除或伪造 paper / claim / KG / probe / graph patch ID。
- `literatureSurvey/evidenceTrace/sourceFields/rawIdeaOutputs` 来自 Idea v5 adapter，始终不由 Plan LLM 重写。
- Step 5 probe papers 不并入 selected papers，只在 `literatureSurvey` 和 `evidenceTrace` 中独立标记。
- `qualityGate.evidenceValid=false` 时仍返回 package，供前端展示和人工修正。
- `agentApproved=true` 只表示 reviewer committee 通过；进入后续模块前仍建议 `humanApproved=true`。
