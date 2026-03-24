# PanCLI — AnyShare 全异步现代化命令行客户端

> 基于 [xdedss/dist_bhpan](https://github.com/xdedss/dist_bhpan) 全面异步重构，参照 [AnyShare RESTful API](https://developers.aishutech.com/openDoc?productId=1&versonId=30&docId=338)

专为 **AnyShare 7 架构网盘**（北航网盘、各高校/政企 AnyShare 分发中心）设计。修改 `config.json` 中的 `host` 即可连接至任意 AnyShare 实例。

## 双模架构

| 模式 | 用法 | 场景 |
|---|---|---|
| **沉浸式 Shell** | `pancli` 直接回车 | 交互式文件管理，Tab 补全，CWD 状态保持 |
| **单次命令** | `pancli ls home` / `pancli upload file .` | 脚本自动化、CI/CD |

```
pancli/
├── main.py      Typer 入口（无参数→Shell，有参数→单次执行）
├── shell.py     prompt-toolkit 全异步 REPL + 智能补全
├── core.py      共享业务逻辑（do_ls / do_upload / do_find …）
├── api.py       AsyncApiManager（全异步，httpx.AsyncClient）
├── network.py   HTTP 引擎（异步+同步双模，SSL 证书补丁，重试）
├── auth.py      OAuth2 登录 + RSA 加密（同步，独立 Client）
├── config.py    platformdirs 配置管理
└── models.py    Pydantic 数据模型
```

**核心设计**：`core.py` 是唯一的业务逻辑层。`main.py`（Typer）和 `shell.py`（prompt-toolkit）均 `await` 调用同一套 `do_*` 函数，**零重复代码**。

---

## 安装

### 前置要求
- Python 3.10+
- 推荐 [uv](https://github.com/astral-sh/uv)

### uv（推荐）
```bash
cd PanCLI
uv pip install -e .
pancli         # 进入沉浸式 Shell
pancli ls .    # 单次列目录
```

### venv + pip
```bash
cd PanCLI
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pancli
```

### 更新
```bash
git pull origin main
# -e 可编辑模式下代码立即生效，无需重装
# 仅当引入新依赖时才需重新 pip install -e .
```

---

## 命令速查

### 沉浸式 Shell（`pancli` 直接进入）

#### 导航与属性
| 命令 | 说明 |
|---|---|
| `ls [dir]` | 列目录（Rich 表格，含创建者/大小/时间） |
| `cd <dir>` | 切换目录（支持 `..`、绝对/相对路径） |
| `pwd` | 打印当前远程路径 |
| `tree [dir]` | 树状图展示目录结构 |
| `stat <path>` | 查看 DocID、版本、标签等元信息 |

#### 文件操作
| 命令 | 说明 |
|---|---|
| `cat <file>` | 打印文件全部内容 |
| `head <file> [-n N]` | 打印前 N 行（默认 10） |
| `tail <file> [-n N]` | 打印末 N 行（默认 10） |
| `touch <file>` | 创建空文件 |
| `mkdir <dir>` | 创建目录（支持多级） |
| `rm <path> [-r]` | 删除（目录需 `-r`） |
| `mv <src> <dst>` | 移动 |
| `cp <src> <dst>` | 复制 |

#### 传输（并发 + 断点续传）
| 命令 | 说明 |
|---|---|
| `upload <本地> [远程] [-r] [-j N]` | 上传（省略远程→当前目录） |
| `download <远程> [本地] [-r] [-j N]` | 下载（省略本地→`.`，支持断点续传） |

- `-j N`：并发数（默认 4），多文件时显示 docker-pull 风格多行进度条
- 断点续传：下载中断后再次执行自动从断点追加（HTTP `Range` header）

#### 搜索
| 命令 | 说明 |
|---|---|
| `find <关键词> [-d 深度]` | 递归搜索，支持 `*` `?` 通配符 |

不含通配符时自动匹配子串（如 `find 期末` 等价于 `find *期末*`）。

#### 账户管理
| 命令 | 说明 |
|---|---|
| `whoami` | 查看当前登录账户 |
| `logout` | 清除本地凭据 |
| `su [user]` | 切换账号 |

#### 本地命令穿透（`!` 前缀）
| 命令 | 说明 |
|---|---|
| `!ls [目录]` | Rich 美化的本地文件列表（与网盘 ls 同风格） |
| `!cd <目录>` | 切换本地工作目录 |
| `!<任意命令>` | 直接执行本地系统命令（如 `!cat README.md`） |

#### 智能 Tab 补全
- **网盘命令**（`ls`, `cd`, `cat` 等）→ 自动补全远程路径（含 `..`）
- **upload 第 1 参数** → 补全本地文件
- **upload 第 2 参数** → 补全远程路径
- **download** → 反过来（第 1 远程，第 2 本地）
- **`!` 开头** → 补全本地文件

### 单次命令（脚本化）
```bash
pancli ls home                   # 列目录
pancli upload ./data . -r -j 4   # 递归并发上传
pancli download home/file .      # 下载（断点续传）
pancli find "*.pdf" -d 3         # 搜索 PDF
pancli --whoami                  # 查看登录状态
pancli --logout                  # 清除凭据
pancli -v                        # 版本号
pancli -h                        # Typer 帮助
```

---

## 连接其他 AnyShare 实例

编辑配置文件，将 `host` 替换为目标地址即可：

| 系统 | 路径 |
|---|---|
| Linux | `~/.config/bhpan/config.json` |
| macOS | `~/Library/Application Support/bhpan/config.json` |
| Windows | `%APPDATA%\bhpan\config.json` |

---

## PyInstaller 单体打包

```bash
pip install pyinstaller
pyinstaller --onefile --name pancli pancli/main.py
# 产物 dist/pancli 可直接拷贝至无 Python 环境的服务器使用
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| CLI 路由 | [Typer](https://typer.tiangolo.com/) (`invoke_without_command` 双模) |
| 交互 Shell | [prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/) (`prompt_async`) |
| 终端 UI | [Rich](https://rich.readthedocs.io/) (Table / Progress / Tree / Panel) |
| HTTP 引擎 | [httpx](https://www.python-httpx.org/) (`AsyncClient` + SSL 补丁 + 自动重试) |
| 数据模型 | [Pydantic v2](https://docs.pydantic.dev/) |
| 配置管理 | [platformdirs](https://github.com/platformdirs/platformdirs) |
