# EdgeBridge

通过一条命令将控制电脑的代理共享给 Ubuntu 边缘设备，并安装 Codex CLI。支持批量部署、连通性验证和断线重连。

> 测试版：已通过模拟测试，尚未在真实 Ubuntu 设备上完成验证。

在有网络出口的控制电脑上运行。每台设备通过 SSH 借用控制电脑上的 Mihomo / Clash HTTP 或 Mixed 代理，可以共用一个上游。代理只应用于部署器启动的命令，也可以显式应用于设备上的其他命令。

## 准备

- 控制电脑使用 macOS 或 Linux，需要 Python 3.8+、OpenSSH 客户端、Bash 和 curl。
- 控制电脑上的 HTTP 或 Mixed 代理已经启动并监听 `127.0.0.1`。用自己的实际端口替换例子中的 `7890`。
- 每台 Ubuntu 使用普通账号，已有 Python 3.8+ 和 curl，SSH 服务允许远程 Unix 套接字转发。首次使用先手动 `ssh 设备别名` 核对指纹，并确认密钥登录可用。可复用现有 SSH 配置，包括 `ProxyJump`。

在能够访问 GitHub 的控制电脑上获取代码，然后进入目录：

```bash
git clone https://github.com/ffxx76/EdgeBridge.git
cd EdgeBridge
```

也可以在 GitHub 页面使用 **Code → Download ZIP** 下载并解压。下面命令都在控制电脑执行。

## 仅开启代理转发

只需要临时让设备上的命令使用代理时，在控制电脑执行：

```bash
bash proxy.sh 7890 edge-device
```

把 `7890` 换成控制电脑的 HTTP / Mixed 代理端口，把 `edge-device` 换成自己的 SSH 别名。批量设备可以使用 `bash proxy.sh 7890 --host-file hosts.txt`。该模式默认验证 `https://example.com/`，也可通过 `--check-url https://你的验证地址/` 自定义验证地址。

在设备的 SSH 终端中，对需要代理的命令使用：

```bash
~/.local/share/edge-deploy/with-proxy curl https://example.com/
```

代理通过环境变量传递给该命令及其子进程，支持代理环境变量的程序才能使用。停止转发执行 `python3 edge-deploy.py down edge-device`，或对批量设备使用 `--host-file hosts.txt`。控制电脑及其代理需要保持在线。

## 安装 Codex

部署代理和 Codex，随后启动设备码登录：

```bash
python3 edge-deploy.py up edge-device --proxy-port 7890 --login
```

如账号或组织允许设备码登录，按终端提示在浏览器授权。安装阶段不需要授权。

编辑 `hosts.txt`，每行一个 SSH 主机别名，支持 `#` 注释。四台并发部署，脚本逐台报告成功或失败：

```bash
python3 edge-deploy.py up --host-file hosts.txt --proxy-port 7890 --jobs 4
python3 edge-deploy.py status --host-file hosts.txt
```

已经安装的 Codex 会先验证版本，重复执行会跳过可用的安装。修正失败原因后，可重跑整批或只重跑失败设备。批量安装完成后，需要在哪台登录，就对那台执行 `python3 edge-deploy.py login 设备别名`。

打开远程 Codex：

```bash
python3 edge-deploy.py codex edge-device
```

设备上已经建好转发时，也可以手动使用它：

```bash
~/.local/share/edge-deploy/with-proxy codex
```

停止转发：

```bash
python3 edge-deploy.py down --host-file hosts.txt
```

## 验证与恢复

- 仅转发模式从控制电脑和设备分别请求验证地址。安装 Codex 模式则检查安装地址和登录域名。登录域名收到 HTTP 响应表示能连到域名，账号可用性要等真实登录验证。
- SSH 断开后后台自动重连。代理检查连续两次失败会重建该设备的转发。控制端代理恢复后，下一次重试会重新验证。重连优先沿用设备的本地端口，被占用时换一个；后者可能需要重开已运行的 Codex。
- 自动尝试官方独立安装器，失败后尝试设备上已有的 Node/npm，安装在用户目录。两条都失败时可传入匹配设备架构的已验证离线包：`--bundle codex-linux.tar.gz --sha256 <发布方校验值>`。包内顶层需要可执行文件 `codex` 和它的配套文件。混合架构批量离线部署时，按架构分批执行。
- `status` 会显示背景进程状态和日志路径。SSH 密钥被拒绝时检查登录账号和密钥；未知主机指纹先手动核对。SSH 禁止 Unix 套接字转发时请设备管理员确认转发策略。控制端代理校验失败时检查端口类型、上游节点和网络。
- 每台设备一个 SSH 隧道。后台守护会在退出终端后继续运行。控制电脑断电、关机或重启会影响整批设备，开机后运行同一条 `up` 命令恢复。需要无人值守时，在每个现场使用常开的控制机。
- 设备侧监听仅限回环地址，设备上的其他本地用户也可能访问该端口。只连接你信任的设备。工具可在普通账号下运行，不要求修改防火墙或 SSH 服务配置。

## 分发

控制电脑可以通过 GitHub 克隆仓库或下载 ZIP。若控制电脑无法访问 GitHub，可以通过其他渠道复制完整目录，设备通过已有 SSH 连接建立转发。
