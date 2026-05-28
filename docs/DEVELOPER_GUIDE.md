# FAROS 开发者文档

## 1. 文档目的

这份文档是 FAROS 当前发布基线的开发手册。

它面向两类人：

- 维护 FAROS 基座框架的开发者
- 在 FAROS 之上继续优化 `idea / experiment / paper / review / platform` 子模块的开发者

它不是产品宣传文档，也不是研究方案文档。
它的目标是回答下面这些工程问题：

1. 当前仓库哪些部分属于稳定发布面，哪些属于内部实现面。
2. 后续开发应该改哪里，不应该改哪里。
3. FAROS 基座和具体 LLM 领域模块之间的职责边界是什么。
4. 多人并行开发时，如何避免互相污染接口和运行时行为。
5. 当前版本上线后，后续迭代应该遵守哪些约束。

---

## 2. 当前发布定义

当前发布目录是一个 **FAROS RC 基线**，不是最终形态的通用 AutoResearch 平台。

当前发布已经具备：

- FAROS runtime
- blueprint / profile / agent / skill / verifier / provider 抽象
- package lifecycle 基础治理
- mixed-provider 执行路径
- memory / artifact / verification / checkpoint 基础层
- 一个完整参考链路：
  - `idea -> experiment -> paper -> review`

当前发布还不代表：

- 全学科可复用的成熟平台
- 分布式 worker 系统
- 完整的 DAG 调度引擎
- 成熟的多租户平台
- 已完成效果优化的科研自动化系统

所以后续开发的原则是：

- **先保护基座，再优化模块**
- **先保证接口稳定，再扩功能**
- **先维护运行时一致性，再做局部效果增强**

---

## 3. 代码结构与职责地图

### 3.1 基座层

路径：`backend/app/faros/`

这一层是 FAROS 的核心运行时。

它负责：

- blueprint 加载与校验
- profile 加载与校验
- agent / skill / verifier / provider registry
- capability adapter 执行入口
- orchestrator 调度
- run / step 状态持久化
- event / artifact / memory / checkpoint
- verification dispatch
- package lifecycle / trust / compatibility / rollback
- FAROS API

这一层是后续平台化演进的中心。

### 3.2 领域模块层

路径：

- `backend/app/modules/idea`
- `backend/app/modules/code`
- `backend/app/modules/paper`
- `backend/app/modules/review`
- `backend/app/modules/platform`

这一层提供当前 LLM 领域的实际业务实现。

职责是：

- 保留已存在的业务逻辑
- 暴露相对稳定的模块接口
- 被 FAROS capability adapter 调用
- 支撑当前前端和模块原生 API

这层不是基座调度器，不应该反向长出全局 runtime 逻辑。

### 3.3 兼容和遗留层

路径：

- `backend/app/api/v1/*`
- `backend/app/services/*`

当前状态：

- 仍有历史兼容价值
- 但不是新架构的首选扩展面

原则：

- 不要再往这层增加新的主业务逻辑
- 如果必须修改，应以兼容和收口为目标，而不是继续扩散

### 3.4 前端层

路径：`frontend/src/`

当前前端的角色：

- 提供模块级工作台
- 提供 Settings / Providers / Runs / Papers 等现有视图
- 逐步向 FAROS runtime console 靠拢

当前前端还不是完整的 FAROS runtime console。
因此，后续前端开发应当：

- 尽量围绕稳定 API 构建
- 减少对模块私有细节的耦合
- 逐步提升对 FAROS runtime 运行态的可视化能力

---

## 4. 运行时稳定边界

### 4.1 当前应视为稳定的对象模型

这些对象现在应被视为 FAROS 发布面的核心契约：

- `Blueprint`
- `Profile`
- `AgentSpec`
- `SkillSpec`
- `VerifierPackageSpec`
- `ProviderDescriptor`
- `ProviderHealth`
- `ExecutionContext`
- `FarosRunRecord`
- `StepState`
- `VerificationSuiteResult`
- `VerifierPackDescriptor`

原则：

- 后续可以扩字段
- 不应随意更改已对外暴露字段的语义
- 如果要改语义，应先走兼容设计，而不是直接覆盖

### 4.2 当前应视为稳定的 API 面

当前可作为稳定面使用的 API 类别包括：

- FAROS health / metadata
- blueprints / profiles / providers / verifiers / artifacts 查询
- package validate / install / refresh / uninstall / rollback / trust / dependency / audit
- run create / preflight / resume / retry / skip / replay / detail
- memory query / recall

原则：

- 新能力尽量在 FAROS API 下继续长
- 不要把新平台能力继续堆回旧 `api/v1` 路由

### 4.3 当前仅视为内部实现面的模块

以下模块当前不应当被其他人当成稳定 contract：

- `orchestrator.py`
- `state_store.py`
- `event_log.py`
- `artifact_store.py`
- `package_audit.py`
- `external_backend.py`
- `package_compatibility.py`
- `package_trust.py`

这些模块可以继续演进，但要保证外部稳定接口不随意变化。

---

## 5. 开发边界规则

### 5.1 什么应该写进 `faros/`

下面这些内容应该进入 `backend/app/faros/`：

- workflow 调度逻辑
- provider 绑定逻辑
- memory / artifact / verification 公共层
- package lifecycle
- runtime 状态机
- checkpoint / retry / replay / resume
- blueprint / profile / agent / skill / verifier 相关平台能力

### 5.2 什么不应该写进 `faros/`

下面这些内容不应该直接写进 `faros/`：

- idea 内部 prompt 细节
- paper 具体段落生成实现细节
- review 模块自己的输出格式细节
- code/experiment 内部仓库规划实现细节

这些逻辑应该留在各自领域模块中，通过 adapter 接进 FAROS。

### 5.3 Adapter 规则

如果 FAROS 需要复用某个模块能力：

1. 实际业务逻辑留在模块内。
2. 在 `faros/capabilities/adapters/` 中封装调用。
3. 输出统一的 `CapabilityResult`。
4. 不要把模块业务逻辑复制进 FAROS runtime。

---

## 6. 当前各子模块的职责与禁止事项

### 6.1 `idea`

拥有：

- idea session
- candidate generation
- ranking / selection
- literature 相关结构化结果

后续适合优化：

- literature grounding
- gap extraction
- candidate traceability
- 多评审打分
- 下游 experiment 可消费的结构化输出

禁止：

- 在这里写全局 runtime 状态
- 在这里写 provider 调度策略
- 在这里管理跨模块 memory

### 6.2 `experiment` / `code`

当前语义上，LLM 领域仍然依赖 `code` 模块来支撑 `experiment` 能力。
对外描述时应优先使用 `experiment`，实现上仍允许复用 `code`。

拥有：

- code sessions
- code projects
- experiment scaffold
- repo/context 浏览能力

后续适合优化：

- 真正的 experiment design
- repo understanding
- greenfield project generation
- execution spec / run spec
- metrics / figure / artifact linkage

禁止：

- 在这里写全局 run 状态机
- 在这里写 package lifecycle
- 在这里写跨 capability provider 策略

### 6.3 `paper`

拥有：

- paper records
- paper context linkage
- LaTeX assembly
- venue-aware PDF 生成

后续适合优化：

- stronger evidence grounding
- claim-to-evidence binding
- citation verification
- methods/experiments consistency
- richer artifact packaging

禁止：

- 在这里写通用 verification framework
- 在这里写 runtime memory 合并逻辑

### 6.4 `review`

拥有：

- review records
- review generation
- action item extraction
- follow-up requests

后续适合优化：

- review schema refinement
- idea/paper/experiment 分轨 review
- severity normalization
- automated improvement requests

禁止：

- 在这里写 run recovery 策略
- 在这里写 provider fallback 策略

### 6.5 `platform`

拥有：

- shared provider settings
- runs / experiments / templates / shared storage facade
- system-level cross-module endpoints

后续适合优化：

- provider lifecycle
- shared storage normalization
- experiment/runs infrastructure
- template distribution

禁止：

- 把任何“多个模块都碰过一次”的逻辑都丢进 platform

---

## 7. 包与插件体系开发规则

当前 FAROS 已经支持四类 package：

- `blueprint`
- `agent`
- `skill`
- `verifier`

这些 package 已经具备：

- validate
- install
- refresh
- uninstall
- rollback
- audit
- trust
- compatibility

后续开发原则：

1. 新的扩展尽量走 package 化，而不是直接改核心代码。
2. package 的 metadata、integrity、signature 要保持一致策略。
3. 不要在 release 分支里引入“动态执行不受信任 Python 代码”的设计。
4. package 的兼容性规则必须先设计，再增加安装入口。

---

## 8. Provider 与执行后端规则

当前 provider 类型：

- `llm`
- `tool`
- `execution`
- `human`

当前 backend 模式：

- `file`
- `workspace_file`
- `command`
- `queue_file`
- `approval_file`
- `approval_queue`

开发原则：

1. provider contract 先扩接口，再接具体后端。
2. 非 LLM provider 的真实执行路径，应优先走 provider-owned execution。
3. 如果新增 worker/queue/approval 能力，先保持协议简单、可观测、可回放。
4. 所有新 backend 都应该能通过 run detail / events / checkpoint 被观察到。

---

## 9. Memory / Artifact / Verification 规则

### 9.1 Memory

当前 memory 已支持：

- data
- summary
- scopes
- history
- archives
- query
- recall

后续原则：

- memory 结构必须可解释
- 不要退化回一个大字典
- archive 和 active memory 要分清
- capability 消费 memory 时优先走明确 scope

### 9.2 Artifact

当前 artifact 已有 registry 和 contract。

后续原则：

- 所有重要跨阶段结果尽量显式 artifact 化
- 先定义 artifact schema，再接运行时生产逻辑
- 不要靠自由文本跨阶段传递关键结构

### 9.3 Verification

当前 verification 已支持：

- registry
- packs
- node-level plugin verifier
- verifier package lifecycle

后续原则：

- verification 先做结构化，再做智能化
- 新 verifier 优先声明式、可组合
- 不要把质量控制写成某个模块私有 prompt

---

## 10. 测试与上线要求

### 10.1 当前发布验证入口

后端：

- `backend/scripts/smoke_runtime_surface.sh`
- `backend/scripts/smoke_package_governance.sh`
- `backend/scripts/smoke_external_backends.sh`
- `backend/scripts/check_backend_launch.sh`

顶层：

- `scripts/check_launch.sh`
- `scripts/check_release.sh`

原则：

- 改 FAROS runtime 的 PR，至少要能通过相关 smoke
- 改 package/provider/verification/runtime recovery 相关逻辑时，不允许跳过 targeted smoke

### 10.2 当前允许的 residual

当前 RC 仍接受的 residual：

- `httpx TestClient` deprecation warning
- npm dependency / audit noise

它们当前不构成上线阻塞，但必须在文档中保持可见。

---

## 11. 多人并行开发建议

推荐按下面分工：

- **基座组**：`faros/runtime`, `faros/registry`, `faros/providers`, `faros/verification`, `faros/memory`
- **idea 组**：`modules/idea` + 对应 FAROS adapter
- **experiment 组**：`modules/code` / experiment adapter / runs linkage
- **paper 组**：`modules/paper` / latex / evidence grounding
- **review 组**：`modules/review` / verifier policy packs
- **frontend 组**：`frontend/src` + FAROS runtime console 能力

并行开发规则：

1. 先确认修改的是稳定面还是内部面。
2. 改稳定面前，先补兼容设计。
3. 子模块不要直接篡改全局 runtime 语义。
4. 新能力若跨越多个模块，优先先立 contract 再分工。

---

## 12. 当前开发总原则

当前 FAROS 已进入“可上线候选”阶段。
因此开发优先级必须是：

1. 保护基座稳定性
2. 保证接口可持续
3. 控制架构扩散
4. 再做模块效果优化

一句话总结：

**把 FAROS 当成运行时平台维护，而不是继续当成一个单体 LLM 科研应用去堆功能。**
