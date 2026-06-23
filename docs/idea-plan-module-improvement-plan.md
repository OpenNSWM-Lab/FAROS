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
3. 优化 Idea 质量闭环：在 PlanPackage 生成前，idea 阶段先完成论文池质量门、候选去重、多 reviewer 审核和必要的论文重检索/重筛选。

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

Plan 阶段不生成多个 plan 候选，也不做 plan A/B/C 排名。质量提升走单轨策略：

- 先围绕已选 idea 生成唯一 `PlanPackage`。
- prompt 中注入单一计划质量骨架，要求同一个计划覆盖 evidence/GAP grounding、baseline comparison、method specification、validation metrics、ablation/robustness、downstream handoff artifacts。
- reviewer 或 validator 发现计划缺口时，只修订同一个 `PlanPackage` 的相关字段。
- 中间修订轮不暴露给普通用户，最终只交付一个 package。

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
- deterministic fallback 也必须覆盖 baseline comparison、ablation/robustness 和 downstream artifacts，避免 LLM 不可用时产物退化成泛化流程。

## 5.5 Idea 阶段质量闭环增强

为避免低质量论文池和弱证据 idea 进入 PlanPackage，本次在 idea pipeline 内部增加以下机制：

| 机制 | 位置 | 作用 |
|---|---|---|
| `paperQualityGate` | Step 2 / Step 3 / Step 6 trace outputs | 检查论文数量、主题相关性、外部来源覆盖和 top paper alignment |
| targeted literature repair | Step 2、Step 6 | 当论文池质量不足或 idea evidence reviewer 指出证据问题时，自动追加更精确 query，重新检索并重建图谱 |
| selected paper 补强 | Step 3 novelty check | 如果 graph role selection 选中的论文不够贴合主题，补入 top-aligned raw papers 再 deep-read |
| candidate batch + dedup | Step 5 brainstorm | LLM legacy 路径会多生成候选，再用相似度去重；BFTS 路径也会去重后再入库 |
| idea reviewer reports | Step 6 ranked output trace | 增加 evidence、novelty、feasibility、specificity、impact 五个方向的内部 reviewer 报告 |
| repair routing | Step 6 | 区分 `regenerate_idea` 与 `rerun_literature_search`：证据池问题先回到论文检索/筛选，idea 表达问题才直接重生 candidate |
| review iteration setting | IdeaSessionConfig / 前端 Ideas 页面 | `maxReviewIterations` 控制 idea reviewer 最大内部迭代轮数，默认 2，范围 1-5 |

新的 idea 侧内部链路：

```text
query expansion
 -> literature search
 -> paperQualityGate
 -> targeted repair search if needed
 -> graph clustering / selected paper repair / deep-read
 -> gap + reasoning graph + path seeds
 -> batch idea generation / BFTS
 -> candidate dedup
 -> ranking + prior-work critique
 -> idea reviewer committee
 -> literature repair or candidate regeneration, repeated up to maxReviewIterations
 -> ranked idea output
 -> PlanPackage
```

Reviewer 可见性约束：

- Reviewer 主要用于内部多轮迭代。
- 中间轮 reviewer 发现的问题必须优先用于自动修复，不直接作为最终交付内容展示。
- 最后一轮 `ideaReviewGate` 才写入 trace outputs 供调试查看。
- PlanPackage 阶段同理，`reviewReports/metaReview` 表示最后一轮 plan reviewer 输出；中间轮仅体现在 repair/revision 审计摘要中。

边界：

- targeted literature repair 最多作为内部修复轮使用，不保证外部 API 一定可用。
- 如果外部检索持续失败且本地 corpus 仍不相关，`PlanPackage` 会被 quality gate 阻断，不应进入 approved handoff。

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
- 检查单一计划是否覆盖必要计划角色：证据与 GAP、baseline 对比、方法实现、验证指标、消融/鲁棒性、下游交付产物。
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
  "maxRepairRounds": 2,
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
- plan 模块不生成多个计划候选；低质量计划通过内部 reviewer/repair loop 修订同一个 package。
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

## 12. Plan 质量增强逐步实现规划

本路线继续坚持单轨交付：plan 阶段只生成一个 `PlanPackage`，不生成多个 plan 候选。质量提升通过更强的内部结构、模板、reviewer、revisor 和人类反馈闭环完成。

### 12.1 Phase A：正式化单计划 Blueprint

目标：把当前 prompt 内部的 `singlePlanDesignBrief` 升级为可审计、可测试、可复用的内部结构。

状态：已落地基础版。当前新增内部 `PlanBlueprint`，并在 `PlanPackage.generation.blueprintVersion/templateId/blueprintSummary` 中记录使用情况。

改动范围：

- 新增内部模型 `PlanBlueprint`，不作为下游默认接口。
- `PlanBlueprint` 只描述一个计划的骨架，不代表多个候选。
- 字段建议：

```text
PlanBlueprint
- packageId
- paperType
- topicAnchors[]
- requiredRoles[]
- stageShape[]
- baselineRequirements[]
- metricRequirements[]
- ablationRequirements[]
- artifactRequirements[]
- evidenceConstraints
- downstreamReadinessChecks[]
```

服务链路：

```text
IdeaCandidate + evidence artifacts
 -> PlanBlueprintBuilder
 -> Plan LLM fills PlanPackage fields from blueprint
 -> validator / reviewer / revisor
 -> single PlanPackage
```

验收标准：

- 每个 `PlanPackage.generation` 记录使用的 blueprint version。
- `PlanBlueprint.requiredRoles[]` 能覆盖 evidence/GAP、baseline、method、metric、ablation、handoff。
- LLM prompt 不再临时拼 checklist，而是读取 blueprint。
- 不新增对外 API 依赖；完整 `PlanPackage` 可保留 blueprint 摘要用于 debug。

### 12.2 Phase B：按 `paperType` 建模板库

目标：让不同研究类型生成不同质量标准，避免所有计划都长成同一种泛化实验流程。

状态：已落地基础版。当前新增 `plan_package_templates.py`，并覆盖 `generic/algorithmic_method/system/benchmark/analysis/application/survey`。

新增文件建议：

```text
backend/app/services/plan_package_templates.py
```

模板类型：

| paperType | 计划侧重点 |
|---|---|
| `algorithmic_method` | baseline、核心算法、复杂度、消融、指标对比 |
| `system` | 架构、模块接口、吞吐/延迟/成本、部署约束、端到端评估 |
| `benchmark` | 数据构造、标注协议、任务定义、评价指标、基线覆盖 |
| `analysis` | 变量控制、解释维度、统计检验、失败案例、可视化 |
| `application` | 场景约束、用户任务、业务指标、安全边界、可用性评估 |
| `survey` | 文献分类、比较维度、趋势/GAP、taxonomy，不生成实验执行计划 |

模板输出：

```text
PlanTemplate
- templateId
- paperType
- requiredStageRoles[]
- requiredStepRoles[]
- recommendedOutputs[]
- recommendedMetrics[]
- requiredComparisons[]
- requiredAblations[]
- forbiddenClaims[]
```

验收标准：

- `paperType` 不同，生成的 stage/step 结构明显不同。
- benchmark/survey 类型不会被强行生成算法实验计划。
- validator 能根据 template 检查缺项。
- deterministic fallback 使用同一套模板。

### 12.3 Phase C：独立 PlanRevisor

目标：把 reviewer 和修订器分离，让系统不是“发现问题后再用同一个 planner 重写”，而是由专门 revisor 根据问题生成定向修复。

状态：已落地基础版。当前新增 `plan_package_revisor.py`，将 human feedback、reviewer issues、quality gate 和 readiness findings 路由到 plan-owned 字段，并识别 upstream blockers。

新增服务建议：

```text
backend/app/services/plan_package_revisor.py
```

输入：

```text
PlanPackage
PlanBlueprint
reviewReports/metaReview
qualityGate.errors/warnings
humanFeedback[]
targetSections[]
```

输出：

```text
PlanRevisionPatch
- changedSections[]
- reason
- reviewerIssueIds[]
- fieldPatches[]
- unresolvedIssues[]
```

修复规则：

- `idea/gap/evidence` 根因不在 plan 阶段硬改事实，只标记为 upstream-blocked 或要求回到 idea 侧。
- `researchQuestion/hypothesis/constants/stages/expectedMetrics` 属于 plan-owned，可自动修。
- `background/gap/principle` 只允许在人类反馈明确要求时做文字表达修订，不伪造证据 ID。

验收标准：

- reviewer 输出的问题能追踪到对应 patch。
- patch 应用后重新 validate/review。
- 中间 revision 只进审计摘要，不作为下游契约。
- 修复失败时保留 `status=needs_revision`，不能误标 `agentApproved=true`。

### 12.4 Phase D：严格 JSON Schema Repair

目标：减少 LLM 输出半截 JSON、混杂文本、`null` 污染和字段类型不一致的问题。

状态：已落地基础版。当前新增 `PlanPackageLLMOutput` 中间 schema，LLM 输出会先经过 schema 校验，再进入 PlanPackage 字段写入。

链路：

```text
LLM output
 -> strict json parse
 -> pydantic validation
 -> schema issue list
 -> JSON repair prompt
 -> parse again
 -> semantic validation
```

实现建议：

- 为 LLM 写入字段定义 `PlanPackageLLMOutput` 中间 schema。
- `_extract_json` 只作为兼容入口，不再吞掉严重格式错误。
- repair prompt 必须包含 parse error、schema error、允许字段、禁止字段。
- 达到最大 repair rounds 后，不回退为“看似成功”，而是明确 `fallbackUsed=true` 或 `needs_revision`。

验收标准：

- 非 JSON、半截 JSON、包含 markdown 的输出会触发 repair。
- `null`、空字符串、未知 output type、非法 dependency 会被修复或拒收。
- repair rounds 真实记录到 `generation.repairRounds`。

### 12.5 Phase E：下游 Readiness 模拟器

目标：PlanPackage 不只字段合法，还要能被 code、experiment、paper、review 模块真正消费。

状态：已落地基础版。当前新增 `plan_package_readiness.py`，并将结果写入 `PlanPackage.downstreamReadiness`、`PlanPackageHandoff.downstreamReadiness` 和 `qualityGate.downstreamReady`。

新增检查建议：

```text
PlanDownstreamReadiness
- codeReady
- experimentReady
- paperReady
- reviewReady
- blockingIssues[]
- warnings[]
```

检查维度：

- code：是否有模块、输入、输出、配置、artifact 名称。
- experiment/validation：是否有 baseline、变量、指标、target、ablation、依赖顺序。
- paper：是否有 background、related work map、GAP、principle、contribution、表图计划。
- review：是否有 evidenceTrace、citation refs、novelty claim、risk/limitation。

验收标准：

- `qualityGate.implementationReady=true` 必须同时满足下游 readiness。
- readiness issue 能进入 reviewer / revisor repair loop。
- `/handoff` 只暴露精简 readiness 结果，不暴露内部 debug 细节。

### 12.6 Phase F：字段级人在反馈

目标：让用户不是对整个 package 写泛泛意见，而是能针对某个字段、stage、step、GAP、metric 给反馈。

状态：已落地后端基础版。`PlanHumanFeedback` 已支持 `sectionPath/displayLabel/sourceView/targetSections`，后端会基于 sectionPath 和 comment 自动推断修订范围；前端字段级批注 UI 仍待实现。

前端交互：

- Presentation 页面默认展示人可读内容。
- 每个关键区块支持“给这段提意见”。
- 用户反馈自动带上 `sectionPath`，如：

```text
researchQuestion
gap.selected
principle.mechanism
stages[stage-2].steps[step-2-1]
stages[stage-3].steps[step-3-2].expected
```

后端行为：

- 根据 `sectionPath` 自动推断 target sections。
- 反馈进入 revisor，不直接交给 planner 全量重写。
- 修复后展示“已处理/未处理/需要人工确认”。

验收标准：

- 用户可以只要求改某个 step，不影响已通过的 background/evidence。
- 反馈修订后保留 revision trace。
- 最终只展示最后一轮 reviewer 摘要和未解决问题。

### 12.7 推荐实施顺序

建议按以下顺序推进，避免一次改动过大：

1. Phase A：先落 `PlanBlueprint` 内部模型和 builder。
2. Phase B：接入 `paperType` 模板库，并让 deterministic fallback 使用模板。
3. Phase D：补强 JSON schema repair，提升 LLM 输出稳定性。
4. Phase C：拆出 `PlanRevisor`，把 reviewer issue 转成定向 patch。
5. Phase E：增加 downstream readiness 模拟器，把质量门从“字段完整”升级到“可被下游消费”。
6. Phase F：最后做字段级前端反馈，因为它依赖 revisor 和 sectionPath 稳定。

最小可交付版本：

```text
Phase A + Phase B + Phase D
```

这个版本能明显提升计划结构和输出稳定性，同时不需要大改前端。

完整质量闭环版本：

```text
Phase A + Phase B + Phase C + Phase D + Phase E + Phase F
```

这个版本能形成“用户选择 idea -> 单 PlanPackage -> 内部多轮审查修订 -> 下游 ready handoff”的完整产品链路。

### 12.8 当前已落地状态

本次已实现最小可交付质量增强版本：

```text
Phase A + Phase B + Phase D
```

已落地内容：

- `PlanBlueprint` 内部结构：从 `PlanPackage`、模板、证据和约束生成单计划蓝图。
- `paperType` 模板库：不同论文类型使用不同 required roles、stage shape、recommended metrics、outputs、comparisons、ablations 和 forbidden claims。
- deterministic fallback 模板化：`survey` 不再硬生成算法实验计划，`benchmark` 会生成任务/数据/基线/协议/质量检查结构。
- LLM 输出中间 schema：写入前校验 top-level key、`null`、字段类型、stage/step/output/expected 基本结构。
- schema repair 计数：`generation.schemaRepairRounds` 记录因 JSON/schema 问题触发的修复轮数。
- blueprint 审计元数据：`generation.blueprintVersion/templateId/blueprintSummary` 记录生成依据。

仍待后续实现：

- PlanRevisor LLM patch 模式：当前 revisor 是规则路由，后续可让 LLM 输出结构化 field patch。
- 下游 readiness 深度模拟：当前是轻量规则检查，后续可接 code/experiment/paper/review 模块的 dry-run verifier。
- 字段级反馈前端 UI：后端已支持 sectionPath 定向反馈，前端仍需在展示区块上提供批注入口。
