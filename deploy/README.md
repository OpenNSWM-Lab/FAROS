# FAROS 部署与依赖指南

本文档给出一套可从空机器复现的部署基线。开发环境、内网计算节点和公网网关承担不同职责，不应把所有依赖都安装在公网服务器上。

## 1. 推荐架构

```text
Browser
  -> HTTPS + Basic Auth (public gateway / Caddy)
  -> /api/* reverse proxy on 127.0.0.1:18005
  -> SSH reverse tunnel
  -> FastAPI on the private compute node
  -> Docker CPU/GPU sandboxes, DATA_DIR, LaTeX/PDF artifacts
```

公网网关只负责 TLS、账号认证、静态前端和 API 转发。后端、数据库、用户 Provider 配置、论文工程及实验产物均留在计算节点。Caddy 必须把已认证用户名写入 `X-Faros-User`，否则用户级 API Key 隔离不会生效。

## 2. 依赖矩阵

| 依赖 | 本地开发 | 计算节点 | 公网网关 | 用途 |
| --- | --- | --- | --- | --- |
| Python 3.11+ | 必需 | 必需 | 不需要 | FastAPI、智能体和实验管理 |
| `backend/requirements.txt` | 必需 | 必需 | 不需要 | 后端运行时 |
| Node.js 18+ / npm | 必需 | 构建机可选 | 预构建部署时不需要 | React 构建和测试 |
| Docker Engine | 可选 | 必需 | 不需要 | Code/Experiment 隔离执行 |
| CPU 沙箱镜像 | 可选 | 必需 | 不需要 | 通用实验与测试 |
| NVIDIA 驱动与 Container Toolkit | 可选 | GPU 实验必需 | 不需要 | GPU 调度和容器透传 |
| `latexmk` + XeLaTeX | 推荐 | 必需 | 不需要 | `ctexart` 正式论文 PDF |
| CJK 字体 | 推荐 | 必需 | 不需要 | `fpdf2` 回退渲染 |
| Git | 必需 | 必需 | 可选 | 代码、版本来源和运行记录 |
| OpenSSH client | 通常已有 | 必需 | 不需要 | 计算节点反向隧道 |
| OpenSSH server | 不需要 | 通常已有 | 必需 | 接收反向隧道 |
| Caddy 2 | 不需要 | 不需要 | 必需 | HTTPS、认证、代理和静态文件 |
| `rsync` / `curl` | 推荐 | 推荐 | 推荐 | 原子发布和健康检查 |

至少还需要一个 OpenAI-compatible LLM Provider。推荐千问/DashScope；Semantic Scholar、OpenAlex、Crossref 和 arXiv 的基础检索不依赖付费 API Key，配置联系邮箱或可选 Key 可以改善限流体验。

阻尼振子代表实验额外依赖 requirements 中锁定范围的 SciPy；CPU 即可运行，
不应为了该案例申请 GPU。部署预检会同时校验 NumPy、SciPy 和 Matplotlib 可导入。

## 3. 自动预检

仓库提供分角色预检，不会输出 API Key 或凭据内容：

```bash
# 本地开发机
./scripts/check_deployment_dependencies.sh --role local

# 内网计算节点；同时强制验证 NVIDIA 运行时和 GPU 镜像
DATA_DIR=/opt/faros/runtime/data \
MPLCONFIGDIR=/opt/faros/runtime/matplotlib \
./scripts/check_deployment_dependencies.sh --role compute --require-gpu

# 公网网关
sudo ./scripts/check_deployment_dependencies.sh --role gateway
```

`[fail]` 表示该角色无法完整运行；`[warn]` 表示可选能力不可用。预检会验证 Python 包版本及导入、旧 `fpdf` 冲突、npm 锁文件、Docker 权限和镜像、TeX 中文宏包、CJK 字体、Caddy 配置及前端发布目录。

## 4. 本地开发环境

### 4.1 系统基础包（Ubuntu/WSL）

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip git curl ca-certificates rsync
```

Node.js 必须是 18 或更高版本。安装完成后先检查：

```bash
python3 --version
node --version
npm --version
```

### 4.2 Python 环境

推荐在仓库根目录或 `backend/` 创建虚拟环境，两种位置都会被 FAROS 脚本识别：

```bash
cd FAROS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-dev.txt
python -m pip check
```

运行时 PDF 回退实现使用 **fpdf2**，不是 2008 年的旧包 `fpdf`。升级已有环境时必须先移除旧包，避免两个发行包争用同一个 `fpdf` 命名空间：

```bash
python -m pip uninstall -y fpdf
python -m pip install --upgrade --force-reinstall 'fpdf2>=2.8,<3'
```

若虚拟环境由 `uv` 创建且不带 pip，可使用：

```bash
uv pip install --python /path/to/venv/bin/python -r backend/requirements-dev.txt
```

### 4.3 前端环境

必须使用仓库锁文件，不要用会改写依赖解析结果的无约束安装：

```bash
cd frontend
npm ci
npm run typecheck
npm run test -- --run
npm run build
```

### 4.4 本地启动

```bash
cd backend
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8005 --reload
```

另开终端：

```bash
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8005 npm run dev
```

本地只检查页面时可以使用默认 `subprocess` 沙箱。涉及评委演示、依赖安装或不可信代码时，应将 `SANDBOX_DEFAULT_BACKEND` 设为 `docker`。

## 5. 计算节点

### 5.1 系统依赖

正式论文链路使用 `ctexart`，因此 `pdflatex` 单独存在并不够：

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip git curl ca-certificates rsync \
  openssh-client latexmk texlive-xetex texlive-lang-chinese \
  texlive-latex-extra fonts-noto-cjk
```

`algorithm2e` 是可选增强包。缺失时 FAROS 的 LaTeX 模板会使用内置算法块，不影响主流程；如需原生样式，可额外安装发行版提供的 TeX science 包。

### 5.2 Docker 与沙箱镜像

安装 Docker Engine 后，将运行 FAROS 的用户加入 `docker` 组并重新登录：

```bash
sudo usermod -aG docker "$USER"
docker info
```

构建与服务环境变量同名的镜像：

```bash
cd FAROS
docker build -f backend/docker/codegen-test.Dockerfile \
  -t faros/codegen-test:3.12 backend

docker build -f backend/docker/codegen-gpu.Dockerfile \
  -t faros/codegen-gpu:cuda12.4 backend
```

GPU 方案还需要主机 NVIDIA 驱动和 NVIDIA Container Toolkit。用真实容器验证，不要只检查 `nvidia-smi`：

```bash
docker run --rm --gpus all faros/codegen-gpu:cuda12.4 \
  python -c 'import torch; print(torch.cuda.is_available(), torch.cuda.device_count())'
```

### 5.3 目录与 Python

参考部署以 `/opt/faros` 为根，与仓库中的 systemd 单元一致。若目标机器使用其他路径，必须同步修改单元和两个环境文件：

```bash
mkdir -p /opt/faros/{current,releases,runtime/data,runtime/matplotlib,runtime/provider-configs}
python3 -m venv /opt/faros/venv
/opt/faros/venv/bin/python -m pip install --upgrade pip
/opt/faros/venv/bin/python -m pip install -r backend/requirements.txt
```

所有目录必须归 systemd 中的 `User` 所有。`DATA_DIR`、`MPLCONFIGDIR` 和 Provider 配置目录必须可写；SQLite 数据库不应通过 Git 或 rsync 覆盖。

### 5.4 运行环境与凭据

复制并修改两个模板：

```bash
install -m 0640 deploy/systemd/faros-compute.env.example \
  /opt/faros/runtime/backend.env
install -m 0600 deploy/systemd/faros-credentials.env.example \
  /opt/faros/runtime/credentials.env
```

用部署虚拟环境生成稳定的 Fernet Key，填入 `FAROS_CREDENTIAL_KEY`：

```bash
/opt/faros/venv/bin/python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

该 Key 必须在升级时保持不变，否则已保存的用户 API Key 无法解密。推荐让每个账号在 UI 的“设置 / LLM Provider”中填写自己的 Key；服务环境中的 `QWEN_API_KEY` 可以留空。

ReviewX 正式签核必须使用专用 signer 账号。生产模板启用
`FAROS_REVIEWX_AUTH_MODE=proxy`，Caddy 在完成 Basic Auth 后覆盖
`X-Faros-User`，后端再用 `FAROS_REVIEWX_SIGNER_USERS` 校验允许名单。
`faros-judge` 始终只读，`faros-team` 只有被显式加入 signer 允许名单时才可签核。
本地 `local` 模式仅用于技术测试，产生的 `authAssurance=local_test` 不得作为正式生产签核。

### 5.5 数据库迁移

升级前先备份 `DATA_DIR`，再执行：

```bash
cd /opt/faros/current/backend
DATA_DIR=/opt/faros/runtime/data /opt/faros/venv/bin/alembic upgrade head
```

### 5.6 systemd

仓库中的单元包含示例用户和路径，安装前必须按目标机器替换：

```bash
sudo install -m 0644 deploy/systemd/faros-compute.service \
  /etc/systemd/system/faros-compute.service
sudo install -m 0644 deploy/systemd/faros-reverse-tunnel.service \
  /etc/systemd/system/faros-reverse-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now faros-compute.service
sudo systemctl enable --now faros-reverse-tunnel.service
```

服务默认只监听计算节点 `127.0.0.1:18005`，不应直接向公网开放。检查：

```bash
systemctl status faros-compute faros-reverse-tunnel --no-pager
journalctl -u faros-compute -n 100 --no-pager
```

## 6. 公网网关

### 6.1 最小依赖

公网机只需要 Caddy 2、OpenSSH server、`curl` 和用于发布的 `rsync`。前端若在 CI/开发机预先构建，公网机不需要 Node、Python、Docker 或 GPU 依赖。

应只开放：

- `22/tcp`：受限 SSH 管理与反向隧道；
- `80/tcp`：HTTP 到 HTTPS 跳转/证书校验；
- `443/tcp`：评委访问；
- 不开放 `18005/tcp`，它只绑定 `127.0.0.1`。

### 6.2 前端原子发布

```bash
cd frontend
npm ci
npm run build

release="/opt/faros/frontend-releases/$(date +%Y%m%d%H%M%S)"
sudo mkdir -p "$release"
sudo rsync -a --delete dist/ "$release/"
sudo ln -sfn "$release" /opt/faros/frontend-current
```

保留上一版 release 目录即可快速回滚软链接。不要把包含运行数据的 `backend/data` 同步到公网机。

### 6.3 Caddy、认证与用户隔离

以 `deploy/caddy/Caddyfile.example` 为起点：

```bash
caddy hash-password --plaintext 'replace-with-a-strong-password'
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

关键约束：

1. `/api/*` 必须代理到反向隧道的 `127.0.0.1:18005`。
2. `header_up X-Faros-User {http.auth.user.id}` 不可删除。
3. 团队和评委使用不同 Caddy 用户；后端据此隔离 Provider Key。
4. 真实密码只保存为 Caddy hash，不进入仓库。
5. 生产环境使用受信任 HTTPS 证书；直接用 IP 时需自行提供覆盖该 IP 的证书。

### 6.4 反向隧道

公网机为隧道创建独立、无交互 shell 的低权限账号，只允许远程端口转发。计算节点保存专用私钥和固定 `known_hosts`，文件权限均为 `0600`。确认公网机出现回环监听：

```bash
ss -ltn | grep '127.0.0.1:18005'
```

## 7. 发布验收顺序

每次升级按以下顺序执行，避免“前端已更新但后端能力缺失”：

```bash
# 1. 仓库级回归
bash ./scripts/check_release.sh

# 2. 计算节点依赖
./scripts/check_deployment_dependencies.sh --role compute --require-gpu

# 3. 数据库迁移并重启后端、隧道
sudo systemctl restart faros-compute faros-reverse-tunnel

# 4. 发布前端并重载 Caddy
sudo systemctl reload caddy

# 5. 公网网关依赖
sudo ./scripts/check_deployment_dependencies.sh --role gateway
```

随后至少人工完成一次：Provider 测试、Idea 检索、PlanPackage、Code 沙箱运行、Experiment 指标入库、Paper 原生 PDF、ReviewX 反馈和单人签核。只有 HTTP 健康检查不能证明科研闭环可用。

## 8. 常见依赖故障

| 现象 | 依赖原因 | 处理 |
| --- | --- | --- |
| 后端启动时报 `No module named cryptography` | 选错 Python 或环境未按清单安装 | 设置 `FAROS_PYTHON`，重新安装 `requirements.txt` |
| 中文 PDF 乱码或只生成回退 PDF | 缺少 XeLaTeX/`ctex`/CJK 字体 | 安装 TeX 中文包和 `fonts-noto-cjk`，再点“重新渲染 PDF” |
| `fpdf` API 异常或字体加载失败 | 同时安装了旧 `fpdf` 与 `fpdf2` | 卸载 `fpdf`，强制重装 `fpdf2` |
| Code 一直等待或 SSE 断开 | Docker 不可用、镜像缺失或网关超时 | 运行 compute 预检，检查 `journalctl` 和镜像 |
| GPU profile 退回 CPU | NVIDIA runtime/镜像/显存门槛不满足 | 用 `--require-gpu` 预检并运行真实 GPU 容器 |
| 刷新后用户看到了错误的 Provider 状态 | 代理未注入稳定用户名 | 恢复 Caddy 的 `X-Faros-User` 转发 |
| 前端新功能不存在 | `frontend-current` 仍指向旧 release | 检查软链接并原子切换 |
| 公网 502 | 反向隧道未监听 `127.0.0.1:18005` | 检查两端 SSH 服务和 tunnel systemd 日志 |

## 9. 安全与备份边界

- 不提交 `.env`、API Key、Fernet Key、Caddy hash 对应明文、SSH 私钥和运行数据库。
- `credentials.env`、隧道私钥、`known_hosts` 使用 `0600`。
- 评委账号使用自己的 API Key；团队环境 Key 不作为回退暴露给评委用户。
- 每次迁移前备份 `DATA_DIR` 和稳定的 `FAROS_CREDENTIAL_KEY`。
- 公网网关不承载实验，不安装 Docker，也不保存科研运行数据。
- 正式结果必须检查论文 PDF 的编译状态为 `latexmk`，而不是仅确认文件存在。
