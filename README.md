# EdgeBridge

通过一条命令将控制电脑的代理共享给 Ubuntu 边缘设备，并安装 Codex CLI。支持批量部署、连通性验证和断线重连。

> 正式版：已在 Windows 和 Ubuntu 环境中完成测试。部署过程会显示各阶段和安装输出。

在有网络出口的控制电脑上运行。每台设备通过 SSH 借用控制电脑上的 Mihomo / Clash HTTP 或 Mixed 代理，可以共用一个上游。代理只应用于部署器启动的命令，也可以显式应用于设备上的其他命令。

## 快速使用

在运行 HTTP/Mixed 代理、能通过 SSH 连接边缘设备的控制电脑上获取项目。控制电脑需要能够访问 GitHub；若不能访问，可以用另一台能访问的电脑下载 ZIP 后复制到控制电脑。边缘设备无需访问 GitHub。以下克隆和部署命令均在控制电脑执行：

```bash
git clone https://github.com/ffxx76/EdgeBridge.git
cd EdgeBridge
```

**Windows CMD / PowerShell**，使用 Windows 上的代理连接一台设备：

```cmd
proxy-windows.cmd 7890 user@192.0.2.10
```

**Mac 终端**，使用 Mac 上的代理连接一台 Ubuntu 设备：

```bash
bash proxy.sh 7890 user@192.0.2.10
```

要在设备上同时安装 Codex CLI，可以在 Mac 上运行：

```bash
python3 edge-deploy.py up user@192.0.2.10 --proxy-port 7890 --login
```

Mac、Ubuntu 和 WSL 主入口都使用 SSH 密钥登录。首次连接前，在控制电脑执行 `ssh user@192.0.2.10` 核对设备指纹，并配置好密钥登录。代理端口应填写控制电脑上的 HTTP/Mixed 端口；设备地址填写 SSH 能够访问的目标地址。Mac 控制端尚未经过真实设备完整实测。

**Ubuntu 终端**，使用本机代理连接一台设备：

```bash
bash proxy.sh 7890 user@192.0.2.10
```

Ubuntu 控制电脑如果还需要为设备安装 Codex CLI：

```bash
python3 edge-deploy.py up user@192.0.2.10 --proxy-port 7890 --login
```

将 `7890`、`user` 和示例 IP 换成自己的代理端口、设备用户名与设备地址。上述方式都要求控制电脑上的 HTTP/Mixed 代理已启动，且能够 SSH 连接目标设备。仅转发入口的作用范围请见下方说明。

## 准备

- macOS、Linux 或 WSL 主入口需要 Python 3.8+、OpenSSH 客户端、Bash 和 curl。Windows 原生转发入口需要 OpenSSH 客户端和 curl.exe。
- 控制电脑上的 HTTP 或 Mixed 代理已经启动。用自己的实际端口替换例子中的 `7890`。WSL NAT 使用 Windows 代理时，代理必须在 WSL 可访问的地址监听。
- 每台 Ubuntu 使用普通账号，已有带 SSL 的 Python 3.8+，SSH 服务允许远程 Unix 套接字转发。首次使用先手动 `ssh 设备别名` 核对指纹，并确认密钥登录可用。可复用现有 SSH 配置，包括 `ProxyJump`。安装 Codex 需要设备具备 curl 或 wget。

在能够访问 GitHub 的控制电脑上获取代码，然后进入目录：

```bash
git clone https://github.com/ffxx76/EdgeBridge.git
cd EdgeBridge
```

也可以在 GitHub 页面使用 **Code → Download ZIP** 下载并解压。没有 GitHub 访问能力的控制电脑可接收其他电脑复制的完整目录。下面命令都在控制电脑执行。

## Windows 直接使用

Windows 用户可以直接在命令提示符或 PowerShell 中运行，不需要 WSL、Python 或 Git Bash：

```cmd
proxy-windows.cmd 7890 user@192.0.2.10
```

把端口替换为 Windows 上的 HTTP / Mixed 代理端口，用户名和 IP 替换为设备的 SSH 地址。首次连接按提示核对 SSH 主机指纹并登录。程序会先检查 Windows 代理，再建立 SSH 转发并检查设备能否通过代理访问 HTTPS。成功后进入设备的代理终端，在其中运行需要代理的命令；输入 `exit` 关闭连接。Windows 电脑及其代理需要保持运行。

该入口适合临时连接一台设备，断开后需要重新执行命令。批量部署、后台自动重连和 Codex 安装目前需要使用 macOS、Linux 或 WSL 的主入口。远端默认使用回环端口 17890，如果冲突，可以通过 PowerShell 使用 `-RemotePort` 选择其他端口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\proxy-windows.ps1 -ProxyPort 7890 -Target user@192.0.2.10 -RemotePort 17891
```

要求 Windows 安装 OpenSSH 客户端和 curl.exe，远端 Ubuntu 需要 bash 和 curl。代理环境变量只对该 SSH 终端及子进程生效，具体程序需要支持这些变量。Windows 和 Ubuntu 环境已完成测试。

## Windows 与 WSL

建议在 WSL 终端内克隆仓库。Git 会根据 `.gitattributes` 保持脚本的 LF 行尾。已有目录如果出现 `invalid option name: pipefail`，在 WSL 终端的项目目录执行：

```bash
sed -i 's/\r$//' proxy.sh edge-codex.sh
```

Windows 命令提示符下先运行 `wsl` 进入 WSL。已检出的脚本可能仍保留 CRLF，可手动转换或重新克隆。

WSL 2 NAT 模式下，Windows 的 `127.0.0.1` 与 WSL 的 `127.0.0.1` 不同。部署器会先检查 WSL 本地代理，再尝试默认网关。如果代理只监听 Windows 的回环地址，WSL NAT 无法通过网关连接。可使用支持的 WSL mirrored 网络，或者让代理监听 WSL 可访问的地址并配置防火墙。后者可能使代理被局域网访问，应限制监听范围和访问权限。Windows 11 22H2 及更高版本可参考 [Microsoft WSL 网络文档](https://learn.microsoft.com/windows/wsl/networking)。

也可手动指定代理所在的 IPv4，用 `ip route show default` 查询 WSL 的 Windows 网关，替换以下示例地址：

```bash
bash proxy.sh 7890 user@edge-device --proxy-host 172.30.0.1
```

SSH 目标若只写 IP，会使用当前 WSL 用户名，目标用户名不同时写成 `user@IP`。批量部署前先确认每台设备的 SSH 主机指纹和密钥登录。

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

`--login` 会在安装完成后启动**设备码登录**。若账号或组织允许该方式，保持终端打开，在控制电脑的浏览器打开终端显示的地址、登录并输入一次性设备码，等待终端确认成功。如果已经完成安装，可以在控制电脑单独启动：

```bash
python3 edge-deploy.py login edge-device
```

设备码登录需要在 ChatGPT 账户安全设置或组织工作区中启用。设备码不可分享。详见 [OpenAI 无界面设备登录说明](https://learn.chatgpt.com/docs/auth?surface=cli#login-on-headless-devices)。

如设备码登录不可用，也可以使用**浏览器回调登录**。先在控制电脑启动 EdgeBridge 代理并安装 Codex，然后从控制电脑运行：

```bash
ssh -L 1455:127.0.0.1:1455 edge-device
```

在同一个 SSH 会话的设备终端中运行：

```bash
~/.local/share/edge-deploy/with-proxy codex login
```

如果终端找不到 `codex`，请确认其安装位置已加入设备用户的 `PATH`。在控制电脑浏览器打开终端显示的登录地址，并保持 SSH 窗口及登录进程运行至成功。控制电脑的 1455 端口需要可用，回调地址和授权码不要分享。

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
- 代理连通性检查使用设备已有的 Python，不要求设备安装 curl。Codex 安装优先使用官方独立安装器，支持 curl 或 wget，失败后尝试设备已有的 Node/npm。两条都失败时可传入匹配设备架构的已验证离线包：`--bundle codex-linux.tar.gz --sha256 <发布方校验值>`。包内顶层需要可执行文件 `codex` 和它的配套文件。混合架构批量离线部署时，按架构分批执行。
- 如果设备既没有 curl 也没有 wget，且允许免密码 sudo，程序会通过代理尝试用 apt 安装 curl。若需要管理员密码或安装失败，代理仍保持运行，可在设备终端通过下面的命令安装后重试部署。

  ```bash
  edge_proxy="http://127.0.0.1:$(cat "$HOME/.local/share/edge-deploy/proxy-port")"
  sudo apt-get -o "Acquire::http::Proxy=$edge_proxy" -o "Acquire::https::Proxy=$edge_proxy" update
  sudo apt-get -o "Acquire::http::Proxy=$edge_proxy" -o "Acquire::https::Proxy=$edge_proxy" install -y curl
  ```
- `status` 会显示背景进程状态和日志路径。SSH 密钥被拒绝时检查登录账号和密钥；未知主机指纹先手动核对。SSH 禁止 Unix 套接字转发时请设备管理员确认转发策略。控制端代理校验失败时检查端口类型、上游节点和网络。
- 每台设备一个 SSH 隧道。后台守护会在退出终端后继续运行。控制电脑断电、关机或重启会影响整批设备，开机后运行同一条 `up` 命令恢复。需要无人值守时，在每个现场使用常开的控制机。
- 设备侧监听仅限回环地址，设备上的其他本地用户也可能访问该端口。只连接你信任的设备。工具可在普通账号下运行，不要求修改防火墙或 SSH 服务配置。

## 分发

控制电脑可以通过 GitHub 克隆仓库或下载 ZIP。若控制电脑无法访问 GitHub，可以通过其他渠道复制完整目录，设备通过已有 SSH 连接建立转发。
