/**
 * Embedding 量化系统性研究 — 实验流程蓝图 Mock 数据
 * 模拟 idea 模块输出后生成的实验 DAG
 */

export type StepStatus = 'pending' | 'running' | 'success' | 'failed'

export interface StepResult {
  summary: string
  metrics: Record<string, string | number>
  artifacts?: string[]
  error?: string
  logs?: string[]
}

export interface ExperimentStep {
  id: string
  label: string
  stage: string
  status: StepStatus
  description: string
  method: string
  inputs: string[]
  outputs: string[]
  result: StepResult | null
  startedAt: string | null
  finishedAt: string | null
  duration: number | null
}

export interface ExperimentBlueprint {
  id: string
  title: string
  description: string
  nodes: ExperimentStep[]
  edges: Array<{ id: string; source: string; target: string }>
}

export const mockBlueprint: ExperimentBlueprint = {
  id: 'bp-embedding-quant-001',
  title: 'Embedding 量化的系统性研究',
  description: '在给定精度损失上限下，最大化 embedding 存储的压缩率。覆盖 PTQ、混合精度、QAT、int2+稀疏化、推理验证全流程。',

  nodes: [
    // ===== Stage 0: 问题定义 =====
    {
      id: 'stage-0-header',
      label: 'Stage 0: 问题定义与指标确立',
      stage: 'Stage 0',
      status: 'success',
      description: '定义核心优化目标、可变维度空间与固定指标',
      method: '形式化约束优化问题：min 存储 s.t. ΔAUC ≤ ε',
      inputs: ['论文调研', '现有实验经验'],
      outputs: ['实验设计文档', '指标体系'],
      result: {
        summary: '成功确立以压缩率-精度 Pareto 最优为核心的评估框架，定义了 6 个可变维度和 4 个固定指标。',
        metrics: {
          '实验维度数': 6,
          '量化位宽选项': 'int8, int4, int3, int2, mixed',
          '核���指标': 'AUC Δ ≤ -0.1%',
          '目标压缩率': '4× → 16×',
        },
        artifacts: ['experiment_design.md', 'metrics_spec.yaml'],
      },
      startedAt: '2026-06-03T09:00:00Z',
      finishedAt: '2026-06-03T09:30:00Z',
      duration: 30,
    },

    // ===== Stage 1: Baseline =====
    {
      id: 'stage-1-header',
      label: 'Stage 1: Baseline 建立',
      stage: 'Stage 1',
      status: 'success',
      description: '建立 FP16 baseline 并对比多种 PTQ 方案',
      method: '统一数据集上的全方案对比实验',
      inputs: ['FP16 pretrained checkpoint', '测试集'],
      outputs: ['baseline metrics', 'PTQ 对比表'],
      result: {
        summary: 'FP16 baseline 已建立。int4 asymmetric row-wise 在 4× 压缩下仅损失 0.08% AUC，为当前最优 uniform PTQ 方案。',
        metrics: {
          'FP16 AUC': 0.7523,
          'FP16 LogLoss': 0.4215,
          'int4 asym AUC': 0.7517,
          'int4 asym ΔAUC': '-0.08%',
          'int4 asym cos_sim_median': 0.987,
        },
        artifacts: ['baseline_report.json', 'ptq_comparison.csv'],
      },
      startedAt: '2026-06-03T09:30:00Z',
      finishedAt: '2026-06-03T11:00:00Z',
      duration: 90,
    },
    {
      id: 's1-fp16-baseline',
      label: '1.1 FP16 Baseline',
      stage: 'Stage 1',
      status: 'success',
      description: '记录 FP16 embedding 全精度下的 AUC/LogLoss，per-row weight statistics，token 频率分布',
      method: '全精度推理 + torch.cuda.memory_stats + tokenizer 频率统计',
      inputs: ['FP16 checkpoint', '测试集 (10 batch)'],
      outputs: ['AUC/LogLoss baseline', 'weight_stats.json', 'token_freq.csv'],
      result: {
        summary: 'FP16 baseline: AUC=0.7523, LogLoss=0.4215。高频行 (top 1%) 贡献 43% 的 lookup 请求。',
        metrics: {
          'AUC': 0.7523,
          'LogLoss': 0.4215,
          'Embedding 大小': '5.2 GB',
          '高频行占比(top 1%)': '43%',
          'weight_std_mean': 0.0234,
          'sparsity_ratio': '0.12%',
        },
        artifacts: ['fp16_baseline.json', 'token_freq.csv', 'weight_stats.json'],
      },
      startedAt: '2026-06-03T09:35:00Z',
      finishedAt: '2026-06-03T10:00:00Z',
      duration: 25,
    },
    {
      id: 's1-ptq-current',
      label: '1.2 当前 PTQ Baseline (uint4 asym row-wise)',
      stage: 'Stage 1',
      status: 'success',
      description: '复现当前 quantize_embedding.py 的量化结果，记录精度损失、cos sim 分布、按频率分桶分析',
      method: '非对称 uint4 row-wise 量化，scale/zp per-row',
      inputs: ['FP16 checkpoint', 'quantize_embedding.py'],
      outputs: ['ptq_current_metrics.json', 'cos_sim_distribution.png'],
      result: {
        summary: 'int4 asym row-wise: ΔAUC=-0.08%, cos_sim median=0.987。低频行 (bottom 90%) 精度损失最大 (cos_sim median=0.72)。',
        metrics: {
          'ΔAUC': '-0.08%',
          'ΔLogLoss': '+0.003',
          'cos_sim_median': 0.987,
          '高频行 cos_sim': 0.998,
          '中频行 cos_sim': 0.991,
          '低频行 cos_sim': 0.720,
          '压缩率': '4×',
          '压缩后大小': '1.30 GB',
        },
        artifacts: ['ptq_current_metrics.json', 'cos_sim_distribution.png', 'freq_bucket_analysis.csv'],
      },
      startedAt: '2026-06-03T10:00:00Z',
      finishedAt: '2026-06-03T10:30:00Z',
      duration: 30,
    },
    {
      id: 's1-ptq-comparison',
      label: '1.3 PTQ 方案全面对比',
      stage: 'Stage 1',
      status: 'success',
      description: '统一数据集上对比 int8/int4/int2 × symmetric/asymmetric × row-wise/per-group 共 10 组方案',
      method: 'Grid scan: bitwidth × symmetry × granularity',
      inputs: ['FP16 checkpoint', '测试集'],
      outputs: ['ptq_full_comparison.csv', 'compression_vs_accuracy.png'],
      result: {
        summary: 'int2 row-wise 在 8× 压缩下 ΔAUC=-4.2%(不可接受)，per-group(group=64) 降低至 -2.1%。int4 per-group 相比 row-wise 提升 0.03% AUC。',
        metrics: {
          'int8 sym row AUC Δ': '-0.01%',
          'int8 asym row AUC Δ': '-0.01%',
          'int4 sym row AUC Δ': '-0.12%',
          'int4 asym row AUC Δ': '-0.08%',
          'int4 per-group64 asym AUC Δ': '-0.05%',
          'int2 sym row AUC Δ': '-5.10%',
          'int2 asym row AUC Δ': '-4.20%',
          'int2 per-group64 asym AUC Δ': '-2.10%',
        },
        artifacts: ['ptq_full_comparison.csv', 'compression_accuracy_pareto.png'],
      },
      startedAt: '2026-06-03T10:30:00Z',
      finishedAt: '2026-06-03T11:00:00Z',
      duration: 30,
    },

    // ===== Stage 2: 混合精度 =====
    {
      id: 'stage-2-header',
      label: 'Stage 2: 混合精度量化',
      stage: 'Stage 2',
      status: 'running',
      description: '设计频率-敏感度联合驱动的混合精度分配策略',
      method: '频率-AUC贡献分析 + 敏感度映射 + 约束优化分配',
      inputs: ['Stage 1 PTQ 结果', 'token 频率数据'],
      outputs: ['混合精度分配方案', 'Pareto 曲线'],
      result: null,
      startedAt: '2026-06-03T11:00:00Z',
      finishedAt: null,
      duration: null,
    },
    {
      id: 's2-freq-sensitivity',
      label: '2.1 频率-精度分析',
      stage: 'Stage 2',
      status: 'success',
      description: '对每行做 mask-out 实验和不同位宽量化，得到 frequency × importance × quantization_sensitivity 三维映射表',
      method: 'Per-row mask-out + per-row multi-bitwidth quant + 三维映射',
      inputs: ['FP16 checkpoint', 'token_freq.csv', '测试集'],
      outputs: ['freq_importance_sensitivity.csv', '3d_mapping.png'],
      result: {
        summary: '完成全行 (3894行) 的三维分析。top 1% 行对 AUC 贡献 43%，但对量化极其敏感（int4 下 cos_sim 从 1.0→0.998）；bottom 50% 行贡献仅 2% AUC，可以激进压缩。',
        metrics: {
          '分析行数': 3894,
          'top 1% AUC 贡献': '43%',
          'bottom 50% AUC 贡献': '2%',
          '高频行量化敏感度': '0.998 (cos_sim@int4)',
          '低频行量化宽容度': '0.72 (cos_sim@int4)',
          '实验耗时': '45 min',
        },
        artifacts: ['freq_importance_sensitivity.csv', '3d_mapping.html'],
      },
      startedAt: '2026-06-03T11:00:00Z',
      finishedAt: '2026-06-03T11:45:00Z',
      duration: 45,
    },
    {
      id: 's2-strategy-a',
      label: '2.2a 策略 A: 频率驱动',
      stage: 'Stage 2',
      status: 'success',
      description: 'top K% → int8, next M% → int4, bottom → int2，网格扫描 (K, M) 找最优',
      method: '网格扫描 over (K, M) ∈ [1,5,10] × [5,10,20,30]',
      inputs: ['freq_importance_sensitivity.csv'],
      outputs: ['strategy_a_scan_results.csv'],
      result: {
        summary: '最优分配：top 2% → int8, next 8% → int4, bottom 90% → int2。压缩率 6.2×，ΔAUC=-0.35%。',
        metrics: {
          '最优 K': '2%',
          '最优 M': '8%',
          '压缩率': '6.2×',
          'ΔAUC': '-0.35%',
          'int8 行占比': '2% (78 rows)',
          'int4 行占比': '8% (311 rows)',
          'int2 行占比': '90% (3505 rows)',
        },
        artifacts: ['strategy_a_scan.csv'],
      },
      startedAt: '2026-06-03T11:45:00Z',
      finishedAt: '2026-06-03T12:15:00Z',
      duration: 30,
    },
    {
      id: 's2-strategy-b',
      label: '2.2b 策略 B: 敏感度驱动',
      stage: 'Stage 2',
      status: 'success',
      description: '按 per-row cos_sim 阈值分配位宽，扫描阈值找最优',
      method: 'cos_sim threshold scan: low=[0.7,0.8,0.85], high=[0.95,0.97,0.99]',
      inputs: ['per_row_cos_sim.csv'],
      outputs: ['strategy_b_scan_results.csv'],
      result: {
        summary: '最优阈值：cos<0.80→int8, 0.80≤cos<0.95→int4, cos≥0.95→int2。压缩率 5.8×，ΔAUC=-0.28%。',
        metrics: {
          '低阈值': 0.80,
          '高阈值': 0.95,
          '压缩率': '5.8×',
          'ΔAUC': '-0.28%',
          'int8 行占比': '3%',
          'int4 行占比': '12%',
          'int2 行占比': '85%',
        },
        artifacts: ['strategy_b_scan.csv'],
      },
      startedAt: '2026-06-03T12:15:00Z',
      finishedAt: '2026-06-03T12:45:00Z',
      duration: 30,
    },
    {
      id: 's2-strategy-c',
      label: '2.2c 策略 C: 联合优化',
      stage: 'Stage 2',
      status: 'running',
      description: '形式化约束优化：min 存储 s.t. ΔAUC ≤ 0.1%，用拉格朗日乘子法求解',
      method: 'Lagrangian relaxation + greedy per-row bitwidth assignment',
      inputs: ['freq_importance_sensitivity.csv', 'strategy_a/b results'],
      outputs: ['strategy_c_optimal.json'],
      result: null,
      startedAt: '2026-06-03T12:45:00Z',
      finishedAt: null,
      duration: null,
    },
    {
      id: 's2-kernel',
      label: '2.3 混合精度 Kernel 实现',
      stage: 'Stage 2',
      status: 'pending',
      description: '修改 fused_int4_embedding.cu，支持同一 batch 内不同 slot/行查不同位宽的表',
      method: 'Multi-bitwidth CUDA kernel，每位宽独立 packed tensor',
      inputs: ['fused_int4_embedding.cu'],
      outputs: ['fused_mixed_embedding.cu'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },

    // ===== Stage 3: QAT =====
    {
      id: 'stage-3-header',
      label: 'Stage 3: 量化感知训练 (QAT)',
      stage: 'Stage 3',
      status: 'pending',
      description: '使用 STE 对量化后 embedding 做 finetune，对比 PTQ vs QAT vs QAT+distillation',
      method: 'Straight-Through Estimator + warm start from FP16 ckpt',
      inputs: ['FP16 checkpoint', 'int4 量化器'],
      outputs: ['QAT checkpoint', 'QAT metrics'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's3-qat',
      label: '3.1 QAT (STE Finetune)',
      stage: 'Stage 3',
      status: 'pending',
      description: '从 FP16 warm start，只 finetune embedding 权重 (scale/zp 同步更新)，其余冻结。3 epoch + cosine LR',
      method: 'STE + per-row int4 quantizer + cosine LR decay 1e-4→1e-5',
      inputs: ['FP16 checkpoint', 'int4 quant/dequant 模块'],
      outputs: ['qat_checkpoint.pt', 'qat_metrics.json'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's3-distillation',
      label: '3.2 QAT + Distillation',
      stage: 'Stage 3',
      status: 'pending',
      description: 'FP16 teacher → int4 student，加 KL 蒸馏 loss',
      method: 'Knowledge Distillation: KL(teacher_logits || student_logits) + λ × task_loss',
      inputs: ['FP16 checkpoint (teacher)', 'QAT checkpoint (student init)'],
      outputs: ['qat_distill_checkpoint.pt', 'distill_metrics.json'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's3-analysis',
      label: '3.3 QAT 效果分析',
      stage: 'Stage 3',
      status: 'pending',
      description: '分析 QAT 后 per-row cos_sim 改善分布、收敛曲线、不同 warmup 策略影响',
      method: 'Per-row cos_sim 偏移分析 + epoch-wise AUC 曲线 + ablation on warmup',
      inputs: ['QAT checkpoint', 'PTQ baseline metrics'],
      outputs: ['qat_analysis_report.md', 'qat_convergence.png'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },

    // ===== Stage 4: 极限压缩 =====
    {
      id: 'stage-4-header',
      label: 'Stage 4: 极限压缩 (int2 + 稀疏化)',
      stage: 'Stage 4',
      status: 'pending',
      description: '实现 int2 per-group 量化 + 非结构化/结构化剪枝 + 联合压缩',
      method: 'int2 quantization + unstructured/structured pruning + joint compression',
      inputs: ['int4 量化模块', 'pruning 模块'],
      outputs: ['int2+sparse checkpoint', '极限压缩 metrics'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's4-int2',
      label: '4.1 int2 量化实现',
      stage: 'Stage 4',
      status: 'failed',
      description: '实现 int2 per-group asymmetric 量化 (group=64)，PTQ→QAT 两阶段优化',
      method: 'int2 per-group(64) asymmetric quant + STE QAT',
      inputs: ['FP16 checkpoint'],
      outputs: ['int2_quantized_model.pt'],
      result: {
        summary: 'int2 per-group PTQ 在 ΔAUC=-2.1% 处失败——精度损失超过可接受阈值（-0.5%）。需要稀疏化补偿。',
        metrics: {
          'int2 PTQ ΔAUC': '-2.10%',
          'int2 QAT ΔAUC': '-0.95%',
          'cos_sim_median': 0.62,
          '失败原因': 'int2 表达范围 {0,1,2,3} 不足以区分 embedding 向量的细粒度差异',
          '建议': '联合稀疏化，仅对重要维度保留更多信息',
        },
        error: '精度损失超标：int2 QAT 后 ΔAUC=-0.95%，超过 -0.5% 可接受上限。需引入稀疏化或混合 int2/int4 方案。',
        artifacts: ['int2_failure_analysis.json'],
        logs: [
          '[PTQ] int2 per-group64 asym: scale range [1.2e-4, 3.8e-2], zp range [-2, 5]',
          '[QAT] Epoch 1/3: ΔAUC=-1.80%, cos_sim median=0.58',
          '[QAT] Epoch 2/3: ΔAUC=-1.20%, cos_sim median=0.61',
          '[QAT] Epoch 3/3: ΔAUC=-0.95%, cos_sim median=0.62',
          '[ERROR] Did not converge to target ΔAUC≤0.5%. int2 alone is insufficient for this embedding space.',
        ],
      },
      startedAt: '2026-06-03T12:45:00Z',
      finishedAt: '2026-06-03T14:30:00Z',
      duration: 105,
    },
    {
      id: 's4-sparse',
      label: '4.2 稀疏化实验',
      stage: 'Stage 4',
      status: 'failed',
      description: '非结构化剪枝 (50/75/90%) + 结构化剪枝 (dim reduction)',
      method: 'Magnitude-based pruning + CSR storage + structured column pruning',
      inputs: ['FP16 checkpoint'],
      outputs: ['sparse_models/'],
      result: {
        summary: '非结构化 75% sparsity + int4: ΔAUC=-0.45%, 通过。但 90% sparsity 下精度崩溃 (ΔAUC=-12%)。结构化剪枝 20% dims: ΔAUC=-0.18%，可行但压缩率有限。',
        metrics: {
          '50% sparse + int4 ΔAUC': '-0.12%',
          '75% sparse + int4 ΔAUC': '-0.45%',
          '90% sparse ΔAUC': '-12.00%',
          '90% sparse 失败原因': 'embedding dim=128 下 90% 稀疏意味着每行仅 13 个非零值，信息损失过大',
          '结构化 20% dim ΔAUC': '-0.18%',
        },
        error: '90% 非结构化稀疏度下精度崩溃 (ΔAUC=-12%)，远超过 -0.5% 可接受上限。建议最大稀疏度设为 75%。',
        artifacts: ['sparsity_ablation.csv', 'sparse_kernel_profiling.txt'],
        logs: [
          '[PRUNE] Unstructured 50%: 2.6M non-zero → 1.3M non-zero, storage 2.6 GB',
          '[PRUNE] Unstructured 75%: 2.6M non-zero → 0.65M non-zero + CSR idx, storage 0.85 GB',
          '[PRUNE] Unstructured 90%: 2.6M non-zero → 0.26M non-zero, AUC collapse detected',
          '[ERROR] 90% sparse lookup produces garbage embeddings for 60%+ of rows.',
        ],
      },
      startedAt: '2026-06-03T14:30:00Z',
      finishedAt: '2026-06-03T16:00:00Z',
      duration: 90,
    },

    // ===== Stage 5: 推理验证 =====
    {
      id: 'stage-5-header',
      label: 'Stage 5: 推理系统验证',
      stage: 'Stage 5',
      status: 'pending',
      description: '端到端延迟测量、GPU memory profiling、Nsight Compute 分析',
      method: 'CUDA event timing + torch.cuda.memory_stats + Nsight Compute',
      inputs: ['各量化 checkpoint', 'benchmark script'],
      outputs: ['latency_report.csv', 'memory_profile.json', 'nc_profiling.txt'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's5-latency',
      label: '5.1 延迟与吞吐测量',
      stage: 'Stage 5',
      status: 'pending',
      description: '对每种量化方案，端到端测量 embedding lookup 延迟 (ms/batch)',
      method: 'CUDA event warmup(50) + benchmark(500) per config',
      inputs: ['各量化 kernel', 'benchmark 脚本'],
      outputs: ['latency_throughput.csv'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's5-memory',
      label: '5.2 显存与带宽分析',
      stage: 'Stage 5',
      status: 'pending',
      description: '实际 GPU 显存占用 + Nsight Compute memory throughput 分析',
      method: 'torch.cuda.memory_allocated() + Nsight Compute --metrics gputime,l1_throughput,memory_throughput',
      inputs: ['各量化 kernel'],
      outputs: ['memory_profile.csv', 'nsight_report.ncu-rep'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },

    // ===== Stage 6: 消融与打磨 =====
    {
      id: 'stage-6-header',
      label: 'Stage 6: 消融实验与论文打磨',
      stage: 'Stage 6',
      status: 'pending',
      description: '逐一去掉组件测量 ΔAUC、在 Criteo 数据集上验证可推广性、生成最终可视化',
      method: 'Ablation study + cross-dataset validation + visualization',
      inputs: ['最优 checkpoint', 'Criteo 数据集'],
      outputs: ['ablation_results.csv', 'criteo_validation.json', 'paper_figures/'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's6-ablation',
      label: '6.1 消融实验',
      stage: 'Stage 6',
      status: 'pending',
      description: '固定最优配置，逐一去掉混合精度/QAT/per-group/asymmetric，测量 ΔAUC',
      method: 'Ablation: remove one component at a time, measure ΔAUC',
      inputs: ['最优混合精度+QAT checkpoint'],
      outputs: ['ablation_results.csv'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's6-criteo',
      label: '6.2 可推广性验证 (Criteo)',
      stage: 'Stage 6',
      status: 'pending',
      description: '在 Criteo 公开数据集上复现最优方案，证明方法通用性',
      method: 'Same pipeline on Criteo dataset (45M samples, 13 continuous + 26 categorical features)',
      inputs: ['Criteo dataset', '最优配置 yaml'],
      outputs: ['criteo_validation.json'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
    {
      id: 's6-visualization',
      label: '6.3 论文可视化',
      stage: 'Stage 6',
      status: 'pending',
      description: '生成混合精度热力图、QAT 前后 cos_sim 偏移、Pareto frontier 图',
      method: 'Matplotlib + Seaborn heatmap + Plotly interactive 3D surface',
      inputs: ['全量实验数据'],
      outputs: ['paper_figures/ (6 张图)'],
      result: null,
      startedAt: null,
      finishedAt: null,
      duration: null,
    },
  ],

  edges: [
    // Stage 0 → 1
    { id: 'e-0-1', source: 'stage-0-header', target: 'stage-1-header' },
    { id: 'e-1a-1b', source: 'stage-1-header', target: 's1-fp16-baseline' },
    { id: 'e-1b-1c', source: 's1-fp16-baseline', target: 's1-ptq-current' },
    { id: 'e-1c-1d', source: 's1-ptq-current', target: 's1-ptq-comparison' },

    // Stage 1 → 2
    { id: 'e-1-2', source: 's1-ptq-comparison', target: 'stage-2-header' },
    { id: 'e-2a-2b', source: 'stage-2-header', target: 's2-freq-sensitivity' },
    { id: 'e-2b-2c', source: 's2-freq-sensitivity', target: 's2-strategy-a' },
    { id: 'e-2b-2d', source: 's2-freq-sensitivity', target: 's2-strategy-b' },
    { id: 'e-2c-2e', source: 's2-strategy-a', target: 's2-strategy-c' },
    { id: 'e-2d-2e', source: 's2-strategy-b', target: 's2-strategy-c' },
    { id: 'e-2e-2f', source: 's2-strategy-c', target: 's2-kernel' },

    // Stage 2 → 3
    { id: 'e-2-3', source: 's2-strategy-c', target: 'stage-3-header' },
    { id: 'e-3a-3b', source: 'stage-3-header', target: 's3-qat' },
    { id: 'e-3b-3c', source: 's3-qat', target: 's3-distillation' },
    { id: 'e-3c-3d', source: 's3-distillation', target: 's3-analysis' },

    // Stage 3 → 4
    { id: 'e-3-4', source: 's3-analysis', target: 'stage-4-header' },
    { id: 'e-4a-4b', source: 'stage-4-header', target: 's4-int2' },
    { id: 'e-4a-4c', source: 'stage-4-header', target: 's4-sparse' },

    // Stage 4 → 5
    { id: 'e-4-5', source: 'stage-4-header', target: 'stage-5-header' },
    { id: 'e-5a-5b', source: 'stage-5-header', target: 's5-latency' },
    { id: 'e-5b-5c', source: 's5-latency', target: 's5-memory' },

    // Stage 5 → 6
    { id: 'e-5-6', source: 's5-memory', target: 'stage-6-header' },
    { id: 'e-6a-6b', source: 'stage-6-header', target: 's6-ablation' },
    { id: 'e-6b-6c', source: 's6-ablation', target: 's6-criteo' },
    { id: 'e-6c-6d', source: 's6-criteo', target: 's6-visualization' },
  ],
}
