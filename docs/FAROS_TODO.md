# FAROS 后续开发计划 TODO

## 1. 文档目标

这份文档记录 FAROS 发布后的后续开发计划。

它覆盖两类内容：

1. **整体基座框架** 的后续建设路线
2. **每个子模块** 的独立优化与重构路线

它不是 issue 列表，也不是临时想法收集区。
它应当作为后续任务拆分、排期、分工和验收的总依据。

---

## 2. 总体目标

FAROS 的长期目标不是继续做一个“更强的 LLM Scientist 应用”，而是做成：

- 面向 AutoResearch 的基础运行时
- 支持 blueprint / profile / agent / skill / verifier / provider 的插件化生态
- 支持多领域工作流装配
- 支持 LLM 与非 LLM 混合执行
- 支持可追踪、可验证、可恢复、可治理的研究执行过程

当前发布版已经具备第一阶段基座。
后续开发应该围绕“从 RC 基线走向可持续平台”展开。

---

## 3. 总路线图

### Phase A：基座上线后稳定化

目标：

- 让当前 RC 基线更稳、更可运维、更可恢复
- 不再继续扩散架构

重点：

- runtime hardening
- warning cleanup
- deployment discipline
- stronger recovery / observability

### Phase B：基座平台化增强

目标：

- 让 FAROS 真正具备跨蓝图、跨 provider、跨插件生态的可持续能力

重点：

- worker/queue 深化
- package trust 深化
- richer dependency resolution
- verifier / policy 扩展
- memory / artifact 深化

### Phase C：LLM 领域 workflow 深化

目标：

- 提升当前 `idea -> experiment -> paper -> review` 的真实效果和实用性

重点：

- idea evidence grounding
- experiment execution
- paper evidence binding
- review actionability

### Phase D：扩展到更通用的 AutoResearch 场景

目标：

- 让 FAROS 不再只服务于 LLM 论文流程

重点：

- 新 blueprint
- 新 profile
- 新 provider
- 新 verifier pack

---

## 4. 整体基座框架 TODO

### A1. Runtime 状态机强化

当前状态：

- 已支持 `skip / retry / resume / replay`
- 已有 run / step state guard
- 已有 checkpoint summary

后续任务：

1. 增加更细的 run/step phase 语义
2. 明确 retryable failure 与 terminal failure 的差别
3. 明确 provider failure / capability failure / verification failure 的状态影响
4. 补 step-level replay policy
5. 增加多 ready node 调度策略抽象

验收标准：

- 任意 run 故障都能在 detail 中解释清楚状态来源
- recovery 动作不会让 run 进入模糊状态

### A2. Worker / Queue 基座深化

当前状态：

- 已有 `queue_file / approval_queue`
- 已有 registration / claim / ack baseline

后续任务：

1. 增加 lease / heartbeat 语义
2. 增加 worker timeout 和 claim expiry
3. 明确 remote worker protocol 抽象
4. 明确 approval backend 的状态流转
5. 增加 queue operation observability

验收标准：

- worker 丢失时 runtime 能正确标红或恢复
- claim 和 ack 生命周期可追踪

### A3. Package Governance 深化

当前状态：

- validate / install / refresh / uninstall / rollback / audit
- metadata + integrity + signature
- dependency validation / grouped conflict summary / baseline solver

后续任务：

1. richer transitive version solving
2. source trust policy 分级
3. package origin allowlist / denylist
4. install policy presets
5. package compatibility report 可视化接口

验收标准：

- package 安装失败时可以明确说明是信任、兼容还是依赖问题
- package 升级能给出清晰的冲突解释

### A4. Verification 平台化

当前状态：

- verifier registry
- policy packs
- verifier package lifecycle
- node-level plugin verifier

后续任务：

1. richer verifier metadata
2. blueprint/profile 级 verifier policy assets
3. scoring + gating
4. human approval verifier
5. verification report 标准化输出

验收标准：

- verification 不再只是 pass/fail
- 各节点质量门禁可独立配置

### A5. Memory / Artifact 深化

当前状态：

- memory query / recall / archive
- artifact registry / contract / schema baseline

后续任务：

1. per-node memory retrieval policy
2. archive recall strategy
3. artifact compiler abstraction
4. structured evidence bundles
5. memory / artifact cross-run reuse policy

验收标准：

- 下游节点不再依赖大段未分层上下文
- artifact 可以成为真正的跨阶段契约

### A6. Console / Runtime UX

当前状态：

- run detail
- timeline
- dependency summary
- verification summary

后续任务：

1. build FAROS runtime console 页面
2. graph-level run visualization
3. package governance 观察面
4. worker / queue 观察面
5. checkpoint / replay 可视化

验收标准：

- 运维和开发者不需要读底层文件就能定位 runtime 问题

---

## 5. `idea` 模块 TODO

### 目标

把 `idea` 从“能生成候选”推进到“有证据、有可追踪性、有下游可消费结构”的研究问题生成模块。

### 当前短板

- literature grounding 还不够强
- gap extraction 仍然偏弱
- candidate ranking 解释性不足
- 下游 experiment 可消费的结构化结果还不够充分

### 后续任务

1. literature retrieval 强化
2. gap analysis schema 化
3. candidate evidence mapping
4. multi-judge ranking
5. idea artifact contract 丰富化
6. reviewer-aware idea critique

### 交付要求

- 输出必须能直接供 `experiment` 使用
- 关键候选必须能回溯证据来源

---

## 6. `experiment` / `code` 模块 TODO

### 目标

把当前 scaffold 型 experiment 推进到真正可执行、可评估、可产出 evidence 的研究执行模块。

### 当前短板

- 仍偏 project scaffold
- 缺少真正的 code synthesis 和 execution loop
- 缺少 metrics / figure / run 结果的标准契约

### 后续任务

1. experiment design contract
2. repo planning / greenfield generation
3. run spec / execution spec
4. metrics ingestion
5. figure generation and registration
6. experiment artifact bundle
7. run-to-paper evidence linkage

### 交付要求

- experiment 阶段输出必须能直接供 paper 和 review 使用
- 不再只是创建项目目录和 metadata

---

## 7. `paper` 模块 TODO

### 目标

把 `paper` 从“可生成论文”推进到“以实验结果和证据为中心的论文装配器”。

### 当前短板

- evidence binding 仍可增强
- claim-to-result consistency 仍不够强
- citation / section coherence 仍有提升空间

### 后续任务

1. experiment evidence grounding
2. section intent / claim graph
3. richer citation verification
4. figure/table grounding
5. venue policy verification
6. stronger artifact packaging

### 交付要求

- paper 输出必须可追踪到上游 experiment / run / metrics / figures

---

## 8. `review` 模块 TODO

### 目标

把 `review` 从“生成评论”推进到“结构化质量控制与改进请求生成器”。

### 当前短板

- action items 还可更规范
- review 类型还不够细分
- review 与上游 evidence 的连接还可增强

### 后续任务

1. review schema refinement
2. severity normalization
3. idea / experiment / paper 分轨 review
4. action item linking to artifacts
5. improvement request automation
6. human-in-the-loop review channel

### 交付要求

- review 输出必须对下游修订或人类决策有明确帮助

---

## 9. `platform` 模块 TODO

### 目标

把 `platform` 从“共享 API 集合”推进到“支撑模块协作的稳定基础设施层”。

### 当前短板

- 部分 provider / runs / experiments 仍偏实现导向
- shared storage surface 还有进一步标准化空间

### 后续任务

1. provider settings lifecycle
2. experiments / runs shared contract normalization
3. template distribution lifecycle
4. shared storage cleanup
5. cross-module lookup APIs

### 交付要求

- platform 应该只承载真正跨模块基础设施，不成为杂项堆放层

---

## 10. 前端 TODO

### 目标

把前端从当前模块工作台逐步推进到 FAROS runtime console。

### 当前短板

- 对 FAROS runtime 的观察面仍有限
- 与 package governance / worker runtime / verification detail 的联动不足

### 后续任务

1. FAROS run console
2. graph / dependency / checkpoint visualization
3. package governance pages
4. provider / verifier / artifact schema inspection pages
5. memory query / recall UI
6. LLM readiness and deployment diagnostics UI

### 交付要求

- 前端要能支持 runtime 运维，而不只是模块功能演示

---

## 11. 优先级排序

### P0：上线后必须尽快做

1. runtime hardening
2. worker / queue reliability
3. experiment 真正执行化
4. paper evidence grounding
5. runtime console baseline

### P1：平台能力增强

1. package governance 深化
2. verification 平台化增强
3. memory / artifact 深化
4. verifier policy metadata

### P2：生态扩展

1. 新 blueprint
2. 新 profile
3. 新 provider
4. 更通用的非 LLM research workflows

---

## 12. 分工建议

推荐按下面方式拆：

- **Runtime 组**：runtime / queue / checkpoint / memory / provider
- **Governance 组**：package / trust / compatibility / verifier lifecycle
- **Idea 组**：idea grounding / ranking / evidence
- **Experiment 组**：code / repo / execution / metrics / figures
- **Paper 组**：paper grounding / latex / evidence / consistency
- **Review 组**：review schema / action items / quality gates
- **Frontend 组**：runtime console / inspection UI / operational views

---

## 13. 总结

当前 FAROS 发布版已经把“基座框架”立住了。
后续开发不应再回到“边写功能边想架构”的方式。

正确路径是：

1. 先保护基座
2. 再深化运行时
3. 再强化 LLM 领域 workflow
4. 最后扩到更通用的 AutoResearch 平台

一句话总结：

**后续 TODO 的核心不是继续堆模块，而是让 FAROS 从一个已上线的研究运行时基线，成长为真正可扩展的 AutoResearch foundation runtime。**
