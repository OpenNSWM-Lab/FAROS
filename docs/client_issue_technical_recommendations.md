# FAROS 甲方问题代码审阅与修改清单

## 0. 汇报结论

从甲方要求看，存在若干核心交付差距：

1. 默认模型链路仍未切到 Qwen：FAROS 默认 profile 仍是 `faros_llm`，且绑定 MiniMax。
2. 多智能体目前只是“有 agent 名称和绑定”，还没有 planner/literature/hypothesis/critic/human gate 的协作闭环。
3. 后端 FAROS API 比较完整，但前端没有 FAROS Console，用户不能直接看 DAG、事件、artifact、verification 和人工审批。
4. Paper 输出仍以会议论文为中心，没有甲方要求的“科学假设与研究计划”结构化字段与专用模板。

## 1. 问题一：默认链路不是 Qwen

### 代码证据

- `backend/app/faros/profiles/faros_llm/profile.json` 中 3 个 capability binding 都是：
  - `provider: "minimax"`
  - `model: "MiniMax-M2.5"`

### 具体修改

| 文件 | 修改内容 |
| --- | --- |
| `backend/app/faros/profiles/faros_qwen/profile.json` | 新增 Qwen 专用 profile。建议复制 `faros_llm` 的结构，把 capability bindings 和 agent bindings 的 provider 全部改成 `qwen`，模型用 `qwen-max` 或甲方指定模型。 |
| `backend/app/faros/api/faros_api.py` | 将 `CreateFarosRunRequest.profileId` 默认值从 `faros_llm` 改为 `faros_qwen`。 |
| `backend/app/core/settings.py` | 部署默认值改为 `ACTIVE_PROVIDER_NAME=qwen`，或至少在 `.env.example` 中明确配置。Qwen base URL 不建议写死，应以甲方实际 DashScope/OpenAI-compatible 网关为准。 |
| `backend/app/llm/provider_client.py` | 对 Qwen 做一次真实连通性验证；如果 LiteLLM 的 `openai/qwen-max` 不能直接走当前 base URL，需要给 qwen 增加专门的 model prefix/base URL 处理。 |
| `frontend/src/lib/models/providers.ts` | 保留现有 qwen-max/qwen-plus/qwen-turbo 列表，并在设置页默认选中 Qwen。 |
| `backend/tests/test_faros_smoke.py` | 新增断言：profile 列表包含 `faros_qwen`；默认创建 FAROS run 时 `profile_id == "faros_qwen"`。 |

### 验收点

- `GET /api/faros/profiles` 能看到 `faros_qwen`。
- 不传 profile 创建 run 时，返回的 run 使用 `profile_id = "faros_qwen"`。
- `Settings -> LLM Providers` 中 Qwen 能保存 key、测试连通、设为 active。
- Ideas/Papers/Review/Code 等普通模块不显式传 provider 时也能走 `settings.get_active_provider()` 指向的 Qwen。

## 2. 问题二：目前还不是完整多智能体系统

### 代码证据

- `backend/app/faros/models/agent.py` 已有 `AgentSpec` 和 `AgentBinding`，说明 agent 抽象已经起步。
- `backend/app/faros/registry/agent_registry.py` 内置了 4 个 agent：`researcher / experimenter / writer / reviewer`。
- `backend/app/faros/runtime/agent_executor.py` 会把 `agent + skills + capability` 组合后执行。
- 但是当前没有 `AgentMessage`、`DebateRound`、`CriticFinding`、`HumanApprovalRequest` 这类协作过程模型。
- `backend/app/faros/blueprints/ml_paper/blueprint.json` 仍只有 4 个粗粒度节点：`idea -> experiment -> paper -> review`。
- `backend/app/faros/registry/capability_registry.py` 只注册了 4 个 capability：`idea_refinement / experiment / paper_drafting / reviewer_simulation`。

### 具体修改

| 文件 | 修改内容 |
| --- | --- |
| `backend/app/faros/models/agent.py` | 增加 `AgentMessage`、`DebateRound`、`CriticFinding`、`HumanApprovalRequest`、`HumanApprovalDecision` 等模型，记录 agent 间的消息、争议、审查和人工决定。 |
| `backend/app/faros/capabilities/adapters/` | 新增 `planner.py`、`literature.py`、`hypothesis.py`、`critic.py`、`human_gate.py`。保留现有 4 个 adapter 作为底层能力，不要直接删除。 |
| `backend/app/faros/registry/capability_registry.py` | 注册新增 capability，使 blueprint 能引用 planner/literature/hypothesis/critic/human gate。 |
| `backend/app/faros/blueprints/multi_agent_research/blueprint.json` | 新增多智能体 blueprint，建议节点为 `planner`、`literature_search`、`hypothesis_generation`、`hypothesis_critic`、`human_hypothesis_gate`、`experiment_design`、`experiment_execution`、`result_critic`、`paper_drafting`、`paper_critic`、`human_final_gate`。 |
| `backend/app/faros/runtime/event_log.py` | 增加事件类型：`agent.message`、`agent.decision`、`critic.finding`、`revision.requested`、`human.pending`、`human.approved`、`human.rejected`。 |
| `backend/app/faros/runtime/orchestrator.py` | 执行节点时根据 node strategy 调度：`single`、`parallel`、`debate`、`critic_loop`、`human_gate`。 |

### 验收点

- `/api/faros/agents` 能看到 planner/literature/hypothesis/critic/human 等角色，或至少能从 multi-agent blueprint 中看到这些节点。
- run detail 能看到每个 agent 的消息、输出、审查意见和下一步决定。
- critic 不通过时可以进入 bounded revision，而不是直接失败或硬继续。
- human gate 能让 run 进入等待人工处理状态。

## 3. 问题三：前端没有 FAROS Console，人在回路不可操作

### 代码证据

- `backend/app/faros/api/faros_api.py` 已经有比较完整的后端接口：
  - `GET /api/faros/blueprints`
  - `GET /api/faros/profiles`
  - `GET /api/faros/agents`
  - `GET /api/faros/providers`
  - `POST /api/faros/runs`
  - `GET /api/faros/runs/{run_id}/detail`
  - `GET /api/faros/runs/{run_id}/events`
  - `GET /api/faros/runs/{run_id}/artifacts`
  - `POST /api/faros/runs/{run_id}/resume`
  - `POST /api/faros/runs/{run_id}/steps/{node_id}/skip`
  - `POST /api/faros/runs/{run_id}/steps/{node_id}/retry`
  - `POST /api/faros/runs/{run_id}/steps/{node_id}/replay`
- `frontend/src/App.tsx` 没有 `/faros`、`/faros/runs/:id` 之类路由。
- `frontend/src/components/layout/Sidebar.tsx` 没有 FAROS Console 入口。
- `frontend/src/lib/api/realClient.ts` 只有旧 `/api/v1/runs`，没有 FAROS API client；`subscribeRunEvents()` 明确写着未实现。
- `frontend/src/pages/Research/Workflows.tsx` 使用静态 `allTemplates`，启动 run 时仍走旧 `useCreateRun()`。
- `frontend/src/pages/Runs/RunDetail.tsx` 展示旧 run trace，不展示 FAROS detail 中的 `workflow / dependencySummary / verificationSummary / memory / events / artifacts`。

### 具体修改

| 文件 | 修改内容 |
| --- | --- |
| `frontend/src/lib/api/faros.ts` | 新增 FAROS API client：`listFarosBlueprints()`、`listFarosProfiles()`、`createFarosRun()`、`getFarosRunDetail()`、`getFarosEvents()`、`getFarosArtifacts()`、`resumeFarosRun()`、`skipFarosStep()`、`retryFarosStep()`、`replayFarosStep()`。 |
| `frontend/src/lib/hooks/useFaros.ts` | 用 React Query 封装 FAROS hooks，running 状态下轮询 detail/events/artifacts。 |
| `frontend/src/App.tsx` | 增加 `/faros`、`/faros/blueprints`、`/faros/runs/new`、`/faros/runs/:id` 路由。 |
| `frontend/src/components/layout/Sidebar.tsx` | 增加 FAROS Console 导航入口。 |
| `frontend/src/pages/Faros/FarosDashboard.tsx` | 新增总览页：run 数、失败数、等待人工处理数、最近事件。 |
| `frontend/src/pages/Faros/Blueprints.tsx` | 展示 blueprint 节点、edges、artifact_schema、verification_rules。 |
| `frontend/src/pages/Faros/NewRun.tsx` | 选择 blueprint/profile，填写 topic、constraints、target venue，创建 FAROS run。 |
| `frontend/src/pages/Faros/RunDetail.tsx` | 展示 DAG/timeline、事件流、artifact 列表、verification summary、memory keys、skip/retry/replay/resume 操作。 |
| `backend/app/faros/api/faros_api.py` | 在现有 step 操作基础上补审批接口：`GET /api/faros/approvals?status=pending`、`GET /api/faros/runs/{run_id}/approvals`、`POST /api/faros/approvals/{approval_id}/resolve`。 |

### 验收点

- 前端侧边栏能进入 FAROS Console。
- 用户可以从前端选择 blueprint/profile 创建 FAROS run。
- Run Detail 能看到依赖关系、步骤状态、verification、events、artifacts、memory。
- 对 blocked/failed/skipped 节点可以执行 skip/retry/replay/resume。
- human gate 出现时，前端可以 approve/reject，并推动后续节点执行或终止。

## 4. 问题四：输出字段没有对齐“科学假设与研究计划”

### 代码证据

- `backend/app/modules/paper/papers_api.py` 的 `CreatePaperRequest` 只有 `title / paperType / targetVenue / planLinkId / projectId / experimentIds / figureIds / runIds / providerName / model / notes`。
- `backend/app/modules/paper/papers_api.py` 的 `VENUES` 只有 `icml / neurips / iclr / acl / generic`。
- `backend/app/modules/paper/service.py` 的 `OUTLINE_PROMPT` 和 `SECTION_PROMPT` 面向 conference paper，要求 introduction、related work、method、experiments、references 等。
- `backend/app/storage/paper_storage.py` 只保存 `outlineJson / logs / pdfAvailable` 等，没有甲方要求字段。
- `backend/templates/latex/templates.json` 只列出会议论文模板。
- `frontend/src/pages/Papers/PapersList.tsx` 创建表单只有 type/venue/template/provider/project/runs/experiments，不收 Problem Statement、Rationale、Technical Details 等字段。

### 具体修改

| 文件 | 修改内容 |
| --- | --- |
| `backend/app/modules/paper/contracts.py` | 新增结构化 contract：`ScientificHypothesisPlan`、`DatasetSpec`、`MethodSpec`、`ExperimentSpec`、`ReferenceSpec`、`FieldCoverageReport`。 |
| `backend/app/modules/paper/papers_api.py` | `CreatePaperRequest` 增加 `outputFormat: "conference_paper" | "scientific_hypothesis_plan"` 和 `fieldDrafts`；详情接口返回 `fieldDrafts`、`fieldCoverageReport`。 |
| `backend/app/storage/paper_storage.py` | paper record 增加 `outputFormat`、`fieldDrafts`、`fieldCoverageReport`、`structuredJsonPath` 或 `structuredJson`。 |
| `backend/app/modules/paper/service.py` | 保留现有会议论文 prompt；新增 `SCIENTIFIC_PLAN_PROMPT`，要求 LLM 输出严格 JSON，并新增字段覆盖校验。 |
| `backend/templates/latex/templates.json` | 新增 `scientific_plan` 模板配置。 |
| `backend/templates/latex/scientific_plan/main.tex` | 新增专用 LaTeX 模板，包含 `Problem Statement / Rationale / Technical Details / Datasets / Source / Target / Methods / Experiments / Results / References`。 |
| `frontend/src/pages/Papers/PapersList.tsx` | 创建表单增加 output format；选择 `scientific_hypothesis_plan` 时显示结构化字段编辑器和字段完整性状态。 |
| `backend/app/faros/blueprints/multi_agent_research/blueprint.json` | output contract 增加 `scientific_hypothesis_plan` 和 `field_coverage_report`，把最终 artifact 从纯论文 PDF 扩展为结构化计划。 |

### 建议字段

`ScientificHypothesisPlan` 至少包含：

- `problemStatement`
- `rationale`
- `technicalDetails`
- `datasets`
- `source`
- `target`
- `methods`
- `experiments`
- `results`
- `references`

### 验收点

- 创建 paper 时可以选择 `scientific_hypothesis_plan`。
- 生成结果包含甲方要求的 10 类字段。
- 输出 artifact 至少包括 PDF、LaTeX zip、structured JSON。
- `fieldCoverageReport` 标记所有必填项已覆盖。
- FAROS 最终输出不只是会议论文，还包含可审计的“科学假设与研究计划”。

## 5. 本周代码修改入口：Paper 输出字段 + FAROS Console

这周我的代码任务有两条主线。第一条是主任务：修改 Paper 部分，解决“输出字段没有对齐科学假设与研究计划”。第二条是配套任务：补一个最小 FAROS Console 和人在回路操作入口，解决“前端没有 FAROS Console，人在回路不可操作”。这两件事可以形成闭环：Paper 负责生成结构化结果，FAROS Console 负责让用户看见流程、事件、产物和人工处理入口。

当前 Paper 链路可以按下面这条路径理解：

`frontend/src/pages/Papers/PapersList.tsx`
-> `backend/app/modules/paper/papers_api.py`
-> `backend/app/storage/paper_storage.py`
-> `backend/app/modules/paper/service.py`
-> `backend/templates/latex/*`
-> PDF / LaTeX zip / paper record

现有链路生成的是会议论文；本周要把它扩展出一个新的输出类型：`scientific_hypothesis_plan`。

### 5.1 第一入口：先定义结构化字段 contract

这是 Paper 修改的第一步。先把甲方要求的字段变成代码里的强约束，否则后面 prompt、storage、前端表单都没有统一标准。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 新增 contract 文件 | `backend/app/modules/paper/contracts.py` | 新增 `ScientificHypothesisPlan`、`DatasetSpec`、`MethodSpec`、`ExperimentSpec`、`ReferenceSpec`、`FieldCoverageReport`。 |
| 字段对齐甲方规范 | `backend/app/modules/paper/contracts.py` | 至少包含 `problemStatement / rationale / technicalDetails / datasets / source / target / methods / experiments / results / references`。 |
| 增加字段校验函数 | `backend/app/modules/paper/contracts.py` 或 `backend/app/modules/paper/service.py` | 新增 `validate_scientific_plan_fields()`，检查必填字段非空、datasets/methods/experiments/references 至少有 1 项。 |

建议先交付这一层，因为它最能体现“不是只改文字模板，而是把甲方字段落成数据结构”。

### 5.2 第二入口：扩展 Paper API 和存储字段

当前 `CreatePaperRequest` 只支持会议论文参数，需要新增输出格式和字段草稿。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 扩展创建请求 | `backend/app/modules/paper/papers_api.py` | 给 `CreatePaperRequest` 增加 `outputFormat` 和 `fieldDrafts`。默认 `outputFormat = "conference_paper"`，新值为 `"scientific_hypothesis_plan"`。 |
| 扩展上下文更新 | `backend/app/modules/paper/papers_api.py` | 给 `UpdatePaperContextRequest` 增加 `fieldDrafts`，方便前端保存人工填写或模型生成的结构化字段。 |
| 扩展 paper record | `backend/app/storage/paper_storage.py` | 在 `_normalize_record()` 和 `create_paper()` 中增加 `outputFormat`、`fieldDrafts`、`fieldCoverageReport`、`structuredJsonPath` 或 `structuredJson`。 |
| 保持旧接口兼容 | `backend/app/modules/paper/papers_api.py` | 旧的 conference paper 创建方式继续可用，不影响现有论文生成。 |

这一部分可以汇报为：我先改 Paper API 的输入输出边界，让后端能够区分“会议论文”和“科学假设计划”两种生成模式。

### 5.3 第三入口：改生成服务，新增 scientific plan 分支

当前核心生成函数是 `backend/app/modules/paper/service.py` 里的 `generate_paper(paper_id)`。现有流程是 outline -> section -> LaTeX -> PDF。需要在这里按 `outputFormat` 分流。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 新增 prompt | `backend/app/modules/paper/service.py` | 新增 `SCIENTIFIC_PLAN_PROMPT`，要求 LLM 返回严格 JSON，字段必须与 `ScientificHypothesisPlan` 一致。 |
| 生成分支 | `backend/app/modules/paper/service.py` | 在 `generate_paper()` 开头读取 `paper.get("outputFormat")`。如果是 `scientific_hypothesis_plan`，走新的 `_generate_scientific_plan()`；否则保留原会议论文流程。 |
| 字段覆盖校验 | `backend/app/modules/paper/service.py` | 新增 `_gate_scientific_plan_fields(plan)`，生成 `fieldCoverageReport`，并写回 paper record。 |
| 写结构化 JSON | `backend/app/storage/paper_storage.py` 或 `service.py` | 在 paper 的 latex 目录写入 `structured_plan.json`，同时把路径保存到 paper record。 |
| 生成 LaTeX/PDF | `backend/app/modules/paper/service.py` | 新增 `_build_scientific_plan_tex(plan)`，生成包含 10 类字段的 `main.tex`，再复用现有 PDF 编译/zip 逻辑。 |

建议函数命名：

- `_generate_scientific_plan(paper_id, paper, context, client, model)`
- `_gate_scientific_plan_fields(plan)`
- `_build_scientific_plan_tex(plan)`
- `_write_structured_plan_json(paper_id, plan, coverage)`

### 5.4 第四入口：新增 scientific_plan LaTeX 模板

当前 `backend/templates/latex/templates.json` 只有 `icml / neurips / iclr / acl / generic`，这些都是会议论文结构。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 注册模板 | `backend/templates/latex/templates.json` | 新增 `scientific_plan`，sections 写成甲方要求的 10 类字段。 |
| 新增模板目录 | `backend/templates/latex/scientific_plan/main.tex` | 新增专用 LaTeX 模板，标题建议为 `Scientific Hypothesis and Research Plan`。 |
| 字段占位符 | `backend/templates/latex/scientific_plan/main.tex` | 使用 `%%PROBLEM_STATEMENT%%`、`%%RATIONALE%%`、`%%TECHNICAL_DETAILS%%` 等占位符，或者由 `_build_scientific_plan_tex()` 直接拼接。 |
| 输出说明 | `backend/app/modules/paper/service.py` | 生成 README，说明产物包括 `main.tex`、`main.pdf`、`structured_plan.json`、`refs.bib`。 |

这一部分可以向学长说明：原来模板体系是会议论文模板，我新增的是一个独立模板，不会破坏 ICML/NeurIPS/ICLR/ACL 原有路径。

### 5.5 第五入口：前端 Paper 表单和预览

前端入口在 `frontend/src/pages/Papers/PapersList.tsx`。当前表单围绕 `paperType / targetVenue / template / provider / project / runs / experiments`，没有甲方字段。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 增加输出格式选择 | `frontend/src/pages/Papers/PapersList.tsx` | 新增 `outputFormat` 下拉选项：`Conference Paper` 与 `Scientific Hypothesis Plan`。 |
| 增加结构化字段编辑器 | `frontend/src/pages/Papers/PapersList.tsx` | 选择 scientific plan 时展示 Problem Statement、Rationale、Technical Details、Datasets、Source、Target、Methods、Experiments、Results、References。 |
| 展示字段状态 | `frontend/src/pages/Papers/PapersList.tsx` | 根据 `fieldCoverageReport` 显示 empty/draft/generated/verified。 |
| 预览 JSON | `frontend/src/pages/Papers/PapersList.tsx` 或 `PaperEditor.tsx` | 在 PDF/LaTeX 之外增加 `structured_plan.json` 预览或下载入口。 |

这一部分可以作为本周后半段任务。如果时间紧，第一版前端只需要做到：能选择 `scientific_hypothesis_plan`，能提交字段草稿，生成后能看到字段覆盖状态。

### 5.6 第六入口：补最小 FAROS Console 和人在回路入口

这个任务不需要本周一次性做完整复杂控制台，先做最小可用版本：能进入 FAROS Console、能创建/查看 FAROS run、能看到 events/artifacts/verification，并预留 human approval 操作入口。

| 修改步骤 | 具体文件 | 要做的事 |
| --- | --- | --- |
| 新增 FAROS API client | `frontend/src/lib/api/faros.ts` | 封装 `listFarosBlueprints()`、`listFarosProfiles()`、`createFarosRun()`、`getFarosRunDetail()`、`getFarosEvents()`、`getFarosArtifacts()`、`resumeFarosRun()`、`skipFarosStep()`、`retryFarosStep()`、`replayFarosStep()`。 |
| 新增 FAROS hooks | `frontend/src/lib/hooks/useFaros.ts` | 用 React Query 封装 blueprint/profile/run detail。running 状态下先用 polling，不急着做 SSE。 |
| 加前端路由 | `frontend/src/App.tsx` | 增加 `/faros`、`/faros/runs/new`、`/faros/runs/:id`。 |
| 加侧边栏入口 | `frontend/src/components/layout/Sidebar.tsx` | 增加 `FAROS Console` 或 `FAROS` 导航项。 |
| 新增 Console 页面 | `frontend/src/pages/Faros/FarosDashboard.tsx`、`frontend/src/pages/Faros/NewRun.tsx`、`frontend/src/pages/Faros/RunDetail.tsx` | Dashboard 看 run 概览；NewRun 选择 blueprint/profile 创建 run；RunDetail 展示 workflow、timeline、events、artifacts、verification。 |
| 补人在回路接口 | `backend/app/faros/api/faros_api.py` | 在已有 resume/skip/retry/replay 基础上，补 `GET /api/faros/approvals?status=pending`、`GET /api/faros/runs/{run_id}/approvals`、`POST /api/faros/approvals/{approval_id}/resolve`。 |
| 补 approval 状态 | `backend/app/faros/providers/human_provider.py`、`backend/app/faros/runtime/orchestrator.py` | human gate 不应直接返回 completed，而应能让 run/step 进入 `waiting_for_human` 或 `pending_approval` 状态，审批后再 resume。 |

本周 Console 部分的最低交付标准：

- 侧边栏能进入 `/faros`。
- 能从前端选择 blueprint/profile 创建 FAROS run。
- 能打开 run detail，看到 workflow/timeline/events/artifacts/verification。
- 对 failed/blocked/skipped 节点能展示 skip/retry/replay/resume 操作。
- human approval 可以先做接口和 UI 占位，后续再接完整审批恢复逻辑。

### 5.7 本周交付物

- 后端新增 `ScientificHypothesisPlan` 相关 contract。
- `CreatePaperRequest` 支持 `outputFormat = "scientific_hypothesis_plan"`。
- paper record 能保存 `fieldDrafts`、`fieldCoverageReport`、`structuredJsonPath`。
- `generate_paper()` 能根据 `outputFormat` 走 scientific plan 分支。
- 生成结果包含 `structured_plan.json`、`main.tex`、`main.pdf`、LaTeX zip。
- 前端 Paper 创建表单能选择 scientific plan，并展示甲方要求字段。
- 前端新增最小 FAROS Console 入口，能创建/查看 FAROS run。
- Run Detail 能展示 events、artifacts、verification，并提供 skip/retry/replay/resume 操作入口。
- 后端补 pending approvals 查询和 resolve 接口设计或初版实现。

## 6. 本周实施顺序

1. 先改后端 contract：把甲方字段固化为 `ScientificHypothesisPlan`。
2. 再改 API/storage：让创建、更新、读取 paper 时都能携带结构化字段。
3. 然后改 `generate_paper()`：按 `outputFormat` 分流，新增 scientific plan 生成逻辑。
4. 接着加 `scientific_plan` LaTeX 模板和 `structured_plan.json` 输出。
5. 改前端 Paper 表单：能选择 scientific plan、填写字段、查看覆盖状态。
6. 补最小 FAROS Console：先接 `/api/faros/blueprints`、`/profiles`、`/runs`、`/runs/{id}/detail`。
7. 补人在回路入口：先做 pending approvals 接口和前端占位，再推进 approval resolve 后 resume。

### 手工验收

- 创建一个 `scientific_hypothesis_plan` paper。
- 填写或让模型生成 Problem Statement、Rationale、Technical Details、Datasets、Source、Target、Methods、Experiments、Results、References。
- 点击 generate。
- 检查 paper record 中有 `fieldCoverageReport`。
- 检查生成目录中有 `structured_plan.json`。
- 检查 PDF/LaTeX 中能看到 10 个必需章节。
- 进入 FAROS Console，创建一个 FAROS run。
- 打开 run detail，检查 workflow、events、artifacts、verification 是否可见。
- 对一个失败或阻塞节点尝试 retry/replay，确认前端能调用对应接口。
