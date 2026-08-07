# FAROS TODO 与 Developer Guide 中文概要

来源文件：

- `FAROS/docs/FAROS_TODO.md`
- `FAROS/docs/DEVELOPER_GUIDE.md`

本文是两份英文文档的大意整理，方便快速理解 FAROS 当前状态、后续路线、模块边界和开发规则。

## 1. 总体理解

FAROS 当前不是一个完全成熟的 AutoResearch 平台，而是处在从“可运行的 LLM 科研工作流应用”向“可复用 AutoResearch Runtime”过渡的阶段。

当前已经具备：

- 独立的 FAROS runtime 包：`backend/app/faros`
- FAROS API
- blueprint/profile 加载机制
- capability registry 和 provider registry
- 基于文件的 run、event、artifact、memory 存储
- 一个可运行 baseline profile：`faros_llm`
- 一个可运行 baseline blueprint：`ml_paper`
- 一条端到端基线工作流：`idea -> experiment -> paper -> review`

当前尚未具备：

- 真正的 DAG 调度器，目前执行仍偏线性
- 成熟的跨领域 AutoResearch 能力
- 真正的自动代码合成与实验执行，`experiment` 阶段目前主要是 scaffold
- FAROS-native 前端控制台，当前前端仍更偏模块原生页面
- 足够强的 verification、evidence grounding、human-in-the-loop 和 provider 策略

一句话概括：FAROS 已经从旧的 LLM-Scientist 整理版中拆出了 runtime 轮廓，但核心工作还在于让 runtime 更稳、更深、更可扩展。

## 2. FAROS_TODO.md 大意

`FAROS_TODO.md` 是后续阶段的结构化 backlog。它按 P0、P1、P2 拆分路线。

### 2.1 Roadmap Overview

当前 release baseline 已完成基础 FAROS runtime 和一个最小 LLM workflow。后续阶段分为：

- `P0`：让当前 LLM workflow 更深、更安全。
- `P1`：加强 runtime 质量、verification、memory、storage、frontend 可见性。
- `P2`：把 FAROS 泛化成更广泛的 AutoResearch 平台。

### 2.2 P0 Runtime Hardening

目标是让当前 runtime 足够安全，支持更多 capability 和多人贡献，而不是马上重写。

重点任务：

- 把线性执行改为真正的图语义。
- 使用 blueprint edges 做依赖规划。
- 支持拓扑排序、循环检测、非法图校验。
- 增加 run 生命周期控制：retry、resume、cancel、失败状态区分。
- 增加 step-level 安全控制：超时、错误分类、provider failure 与 capability failure 区分、预算/token guardrail。
- 清理 event model，把自由格式 event 改成有类别和版本的结构化事件。

验收重点：

- blueprint 出现 cycle 时能清晰失败。
- runtime 能按 edges 执行，而不是按数组顺序隐式执行。
- 失败、取消、重试等状态可追踪、可验证。

### 2.3 P0 LLM Workflow Depth

目标是让当前 `idea -> experiment -> paper -> review` 不只是结构完整，而是真的有科研价值。

Idea 阶段：

- 将 `idea_refinement` 拆成更细的能力或内部步骤，例如 literature understanding、gap analysis、idea ranking。
- 提升文献证据映射、候选选择可解释性、结构化下游输出。

Experiment 阶段：

- 当前只是创建 project 和 experiment record 加一个最小 scaffold。
- 需要升级为真实的 LLM-domain code generation、执行脚本、配置、评估入口。
- 实验输出要能直接供 paper 阶段消费。

Run / Metric / Figure 集成：

- 实验输出要连接 run、metric、figure。
- paper 阶段要通过明确字段消费实验指标与图表。

Paper 阶段：

- 改善 prompt 中对 run/experiment 的 grounding。
- 加强 section 一致性、citation verification、evidence binding。
- 让 methods 和 experiments section 真正反映上游实验状态。

Review 阶段：

- review 输出要结构化。
- action items 要能连接回 FAROS artifacts 或 follow-up requests。
- reviewer prompt 要理解实验上下文，不只是通用论文批评。

### 2.4 P0 Provider Layer

目标是让 provider 处理更少硬编码，更符合 runtime 机制。

主要方向：

- 移除 profile 中固定 provider 假设。
- 允许 profile 按策略继承当前 active provider/model。
- 定义 provider 解析优先级：
  1. request input override
  2. profile binding override
  3. runtime active provider/model
  4. provider config default
- 支持每个 capability 的 fallback model list。
- 区分 provider unavailable、rejected、quota exceeded。
- 增加非 LLM provider contract，例如 `tool` provider 和 `human` provider。

核心意思：FAROS 不能长期被某一个具体 LLM provider 绑定住，provider 应该是 runtime 的可替换执行资源。

### 2.5 P1 Verification

目标是从“字段存在”升级为“结果可信到足够继续执行”。

主要任务：

- 结构验证：required key 检查升级为 typed schema 检查，验证 artifact 是否存在，验证阶段间依赖 contract。
- 证据验证：验证 paper claims 是否有实验/引用支撑，review findings 是否引用真实 section 或输出。
- 一致性验证：检查 idea -> experiment -> paper 的命名、结论、venue constraints 是否一致。
- Blueprint-level verification：允许 blueprint 自带质量门和 verification assets。

核心意思：verification 不应该只是检查有没有 `paperId`，而要检查这个 `paperId` 对应的结果是否真的可信、可用、可继续传给下游。

### 2.6 P1 Research Memory

目标是把 FAROS memory 从一个扁平合并字典升级为真正的 runtime 组件。

主要方向：

- 区分 run-scoped memory 和 node-scoped memory。
- 区分 artifact-backed memory 和 transient context memory。
- 避免 capability 之间随意写 key 导致冲突。
- 支持按类型查询历史输出，例如 selected idea、current experiment evidence、current paper summary、current review actions。
- 支持跨 run 复用，为未来迭代式和分支式 workflow 做准备。

### 2.7 P1 Frontend

目标是让 FAROS 作为一等 runtime 暴露给用户，而不只是隐藏在模块页面后面。

需要新增：

- blueprint list page
- profile list page
- run creation page
- run detail page
- event timeline view
- artifact list view
- verification panel

同时要保留现有模块原生页面，方便直接调试和编辑，不强制所有流程立刻通过 FAROS UI。

### 2.8 P1 Storage

目标是稳定 runtime persistence，但不急着做不必要的大重写。

主要方向：

- 定义 DB models：
  - `faros_runs`
  - `faros_steps`
  - `faros_artifacts`
  - `faros_memories`
- 初期大 payload 和 artifact files 仍保留在 filesystem。
- 制定从 JSON-only persistence 到 DB-backed metadata 的迁移路径。
- 迁移期间保持向后兼容。

### 2.9 P2 Ecosystem Expansion

目标是让 FAROS 超越单一 LLM paper workflow。

未来 blueprint 类型：

- reproducibility reports
- benchmark studies
- survey / synthesis workflows
- domain-specific scientific workflows

未来 profile 类型：

- LLM-heavy fast profile
- verification-heavy profile
- human-in-the-loop profile
- code-execution-heavy profile

未来还要定义第三方 blueprint、capability 的 plugin contract 和版本兼容规则。

### 2.10 Suggested Task Packaging

建议拆任务时使用固定模板：

- Title
- Target area
- Priority
- Goal
- Allowed write scope
- Interface changes
- Data / storage impact
- Required tests
- Non-goals
- Acceptance criteria

推荐拆分区域：

- Runtime orchestration
- Runtime memory and artifacts
- Provider system
- Idea capability depth
- Experiment capability depth
- Paper evidence grounding
- Review actionability
- Frontend FAROS console
- Storage migration
- Verification framework

### 2.11 当前最高价值任务顺序

如果马上继续开发，原文建议的优先级是：

1. 先做真正的 graph execution，替换线性 planning。
2. 深化 `experiment`，让它做真实 LLM-domain code synthesis 和 execution。
3. 把 experiment outputs 接到 metrics 和 figures。
4. 改善 paper evidence grounding。
5. 升级 verification rules。
6. 增加 FAROS frontend console。

这个顺序的理由是：先稳住 runtime 执行模型，再提升实际工作流价值。

## 3. DEVELOPER_GUIDE.md 大意

`DEVELOPER_GUIDE.md` 是给贡献者看的工作手册，不是概念介绍。它规定了代码放哪里、模块边界怎么守、如何加 capability/blueprint/profile/provider，以及测试和 PR 前检查。

### 3.1 Purpose

文档的核心目的：

- 保留一个可运行的 LLM-domain release baseline。
- 把项目从应用演进为可复用 AutoResearch runtime。
- 支持多人并行开发。
- 让未来重构发生在稳定的模块和 runtime 边界后面。

### 3.2 Current Product Definition

当前 FAROS 的定义：

- runtime 在 `backend/app/faros`
- domain implementations 在 `backend/app/modules/*`
- baseline profile 是 `faros_llm`
- baseline blueprint 是 `ml_paper`
- 当前 workflow 是 `idea -> experiment -> paper -> review`

文档强调：系统是 transitional state。FAROS 已经拥有自己的 runtime 包，但旧的 domain modules 仍提供实际业务逻辑。

### 3.3 Codebase Map

Runtime Layer：

- 路径：`backend/app/faros/`
- 职责：runtime models、blueprint/profile loading、registry、orchestrator、run state、event log、artifact、research memory、verification、FAROS API。
- 未来 runtime 工作应优先放这里。

Domain Module Layer：

- 路径：
  - `backend/app/modules/idea`
  - `backend/app/modules/code`
  - `backend/app/modules/paper`
  - `backend/app/modules/review`
  - `backend/app/modules/platform`
- 职责：当前 LLM workflow 的真实业务逻辑和存储 facade。
- FAROS 通过 capability adapters 调用这些模块。

Legacy / Compatibility Layer：

- 路径：
  - `backend/app/api/v1/*`
  - `backend/app/services/*`
- 职责：兼容包装、旧服务实现、迁移遗留。
- 仍然有运行价值，但不应成为新架构默认承载位置。

### 3.4 Module Ownership and Boundaries

`idea` 模块：

- 负责 idea session 生命周期、文献相关候选生成、ranking、selection、idea contracts 和 storage facade。
- 适合做更好的文献检索、gap extraction、evidence grounding、多 judge scoring、candidate traceability。
- 不应该放全局 orchestration、provider policy、cross-workflow memory。

`code` 模块：

- 负责 code sessions、code projects、generation status、repo browsing/indexing、code project persistence/export。
- 未来适合做 FAROS experiment stage 的真实 code synthesis、repo generation、execution validation。
- 不负责全局 FAROS run state、runtime memory merging、跨 capability provider orchestration。

`paper` 模块：

- 负责 paper records、paper context linkage、LaTeX assembly、PDF generation、venue-aware templates。
- 未来适合加强 evidence grounding、section constraints、claim-to-evidence consistency、citation verification、artifact packaging。
- 不负责通用 workflow logic 或非 paper 的全局 verification policy。

`review` 模块：

- 负责 review records、review generation、action item extraction、improvement requests。
- 未来适合做 review schema refinement、paper/code/idea-specific review tracks、severity normalization、improvement request automation。
- 不负责 global memory 或 runtime policy branching。

`platform` 模块：

- 负责 shared providers、experiments、runs、templates、plan links、共享 storage facade 和系统级 endpoints。
- 适合做 provider settings/lifecycle、experiment/runs shared infrastructure、template distribution。
- 不应该因为“多个模块偶尔碰到”就塞入无关业务逻辑。

### 3.5 FAROS Runtime Boundaries

应该放进 `backend/app/faros` 的内容：

- workflow 执行
- blueprint 解释
- profile binding
- provider resolution
- capability coordination
- FAROS run metadata
- FAROS events
- FAROS runtime memory
- runtime verification
- FAROS runtime APIs

不应该放进 `faros/` 的内容：

- idea generation 的内部实现
- paper drafting 的具体细节
- code project 的存储和索引细节
- review report 的具体格式

Adapter Rule：

如果 FAROS 要复用现有模块能力：

1. 真实逻辑留在模块里。
2. 在 `faros/capabilities/adapters` 新增或修改 adapter。
3. adapter 输出标准化的 `CapabilityResult`。
4. 除非明确要废弃旧模块，否则不要把模块业务逻辑复制到 FAROS。

### 3.6 Approved Development Surfaces

领域模块中适合新工作的文件：

- `router.py`
- `*_api.py`
- `service.py`
- `storage.py`
- `contracts.py`
- `interfaces.py`

FAROS 中适合新工作的目录：

- `models/`
- `runtime/`
- `registry/`
- `capabilities/`
- `providers/`
- `verification/`
- `api/`
- `blueprints/`
- `profiles/`

不建议继续承载长期新架构的位置：

- `backend/app/api/v1/*`
- `backend/app/services/*`

这些地方可以为 release 稳定性或迁移做必要修改，但不要继续累积新架构。

### 3.7 Runtime and Storage Rules

当前存储是混合模型：

- code module 部分状态用 DB。
- 很多 runtime 和 artifact path 用 filesystem。
- papers、reviews、experiments、FAROS runs、部分 metadata 用 JSON。

当前规则：

- 如果系统已经用 filesystem/JSON，且稳定性比纯粹架构更重要，就继续沿用。
- 不要无必要引入第二套新存储模式。
- 如果新增 FAROS runtime state，优先扩展现有 FAROS file-backed store，除非任务本身就是 storage migration。

Artifact Rule：

每个 capability 理想上都应输出：

- 标准化 output payload
- 零个或多个 artifact records
- 足够元数据，便于后续重建和下游消费

只要 capability 产出了持久化内容，就应该登记为 FAROS artifact。

### 3.8 如何新增 Runtime 能力

新增 Capability：

1. 判断是新 capability，还是现有模块内部 refinement。
2. 在 `backend/app/faros/capabilities/adapters` 新增或更新 adapter。
3. 返回标准化 `CapabilityResult`。
4. 在 `capability_registry.py` 注册。
5. 如果输出变了，更新 verification rules。
6. 如果 workflow graph 变了，更新 blueprint assets。
7. 增加或更新测试。

新增 Blueprint：

1. 在 `backend/app/faros/blueprints/<blueprint_id>/blueprint.json` 新增资产。
2. 保持 graph 和 outputs 明确。
3. 清楚声明 capability 顺序和 output expectations。
4. 不要把 provider assumptions 写入 blueprint。
5. 测试它能 load，并且 plan-mode run 能创建。

新增 Profile：

1. 在 `backend/app/faros/profiles/<profile_id>/profile.json` 新增资产。
2. provider bindings 要明确。
3. 不要把业务逻辑写进 profile。
4. profile 表达 execution strategy，不表达 workflow semantics。

新增 Provider：

1. 在 `backend/app/faros/providers` 增加 provider implementation。
2. 在 `provider_registry.py` 注册。
3. 保持 provider API 通用。
4. 不要把某个 provider 的特殊假设泄漏到所有 capabilities。

### 3.9 Parallel Development Rules

项目准备支持多人并行开发，所以任务要按 ownership 拆，而不是随机按文件拆。

推荐拆分维度：

- runtime
- idea
- code
- paper
- review
- platform
- docs / release

每个任务应明确：

- target module 或 runtime area
- allowed write scope
- public interfaces touched
- storage implications
- required tests
- explicit non-goals

如果一个任务同时碰到 `faros/runtime`、`modules/code`、`modules/paper`、`modules/platform` 等多个高风险区域，要清楚说明原因，避免意外耦合。

### 3.10 Testing and Validation

当前预期检查：

- Backend syntax：`python -m py_compile ...`
- Backend tests：`cd backend && conda run -n aist python -m pytest -q tests`
- Release checks：
  - `bash scripts/check_release.sh`
  - `bash backend/scripts/check_backend_release.sh`
  - `bash frontend/scripts/check_frontend_release.sh`

特定修改的强制检查：

- 修改 FAROS runtime：
  - 跑后端测试
  - 验证 FAROS route mounting
  - 验证 blueprint/profile loading
- 修改 paper templates/rendering：
  - 检查 paper path 能 compile
  - 确保 PDF path 仍存在
- 修改 code project storage 或 experiment linkage：
  - 验证 project 和 experiment record 创建路径
  - 确认 IDs 能传递到下游步骤

当前可接受但不应长期忽略的 warning：

- Pydantic v2 config deprecation warnings
- `datetime.utcnow()` deprecation warnings
- FastAPI `on_event` deprecation warnings

### 3.11 Known Technical Debt

当前已知技术债：

1. FAROS 执行仍是线性的，不是真 DAG scheduler。
2. `experiment` 当前只是 scaffold，不是完整 code synthesis + execution。
3. 多个模块仍依赖 legacy `app.services`。
4. 存储混合使用 DB、JSON、filesystem。
5. Verification 仍停留在 baseline/结构层。
6. 前端仍更偏 module-native，不够 FAROS-native。
7. release docs 和 runtime conventions 仍在稳定中。

### 3.12 Pre-PR Checklist

提交 PR 或交付 patch 前应检查：

- backend Python 文件能 compile。
- 目标后端测试通过。
- 不提交 secrets。
- 不提交本地 runtime data。
- 不提交 `node_modules`、`dist`、`.venv`、`.verify-venv` 或生成的 DB 文件。
- 如果改 paper template，要做 compile validation。
- README/docs 要匹配当前 release 方向。
- FAROS-facing 改动要同步反映到 blueprint/profile/runtime docs。

### 3.13 Contributor Rule of Thumb

不确定代码放哪里时：

- 如果改的是 research workflows 如何执行，大概率放 `faros/`。
- 如果改的是某个 domain feature 具体怎么工作，大概率放 `modules/*`。
- 如果某处只是旧兼容层，不要把它变成新的默认扩展面。

## 4. 两份文档共同传达的主线

两份文档其实在说同一件事：FAROS 要从“能跑的基线工作流”进化成“可扩展、可验证、可多人协作开发的 AutoResearch runtime”。

当前最关键的矛盾是：

- Runtime 已经有基本壳子，但执行模型还是线性。
- Domain modules 已经能做 idea/paper/review/code 相关事情，但 FAROS 还没有把它们编排成成熟的图式、多阶段、可验证工作流。
- Provider、memory、artifact、verification 都有雏形，但还不够 runtime-native。
- 前端还没有把 FAROS 作为一等 runtime 暴露出来。

后续开发的正确方向是：

1. 先稳 runtime：DAG、状态、事件、错误、重试、取消。
2. 再加深工作流：idea 更结构化、experiment 真执行、paper 更 evidence-grounded、review 更 actionable。
3. 再补平台能力：provider fallback、human/tool provider、typed memory、verification assets、DB-backed metadata。
4. 再做前端 console：让用户能看到 blueprint/profile/run/event/artifact/verification。
5. 最后做生态扩展：更多 blueprints、profiles、plugins。

## 5. 对当前项目修改的直接启示

如果接下来要改 FAROS，建议遵循这些原则：

- 不要把新架构继续堆到 `backend/app/services/*`。
- FAROS runtime 相关逻辑放 `backend/app/faros/*`。
- 领域业务细节继续留在 `backend/app/modules/*`。
- FAROS 调用模块能力时通过 `faros/capabilities/adapters` 适配。
- Blueprint 只描述 workflow graph 和 outputs，不绑定具体 provider。
- Profile 描述执行策略和 provider binding，不写业务逻辑。
- 每个 capability 产出的持久化结果都要记录 artifact。
- 改 runtime 必须补测试，尤其是 blueprint/profile loading、route mounting、graph execution。
- 当前最高优先级应是 graph execution 和 experiment 深化，它们是后续多智能体、人在回路、证据链和前端 console 的基础。
