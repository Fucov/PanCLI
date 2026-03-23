# PanCLI v3 — 异步引擎 + 并发传输 + Typer/Shell 混合架构

> AnyShare (北航网盘) 现代化命令行工具，继承自 v2 REPL 交互模式，全面升级为异步高并发架构。

## 核心亮点

- **异步高并发引擎**：全面采用 `httpx.AsyncClient` + `asyncio`，彻底告别同步阻塞
- **Typer 混合入口**：单次命令即用即走 (`pancli ls home`)，也可进入沉浸式 Shell (`pancli` / `pancli shell`)
- **极速并发传输**：下载/上传支持 `-j/--jobs` 并发数控制（默认 4），配合 `asyncio.Semaphore` 流量控制
- **断点续传**：自动比对本地文件大小，在 `httpx` 请求中动态注入 `Range: bytes=...` Header
- **Rich 炫酷 UI**：多任务并行进度条（类似 `docker pull` 效果）、自适应浅/深色主题
- **新增 `find` 全局搜索**：递归遍历目录树，匹配文件名关键词，用 Rich 表格优雅输出
- **Pydantic 类型安全**：所有数据模型静态类型校验，`FileMetaData`、`TransferTask` 等全面重构
- **配置热迁移**：`platformdirs` 标准配置目录，`_CURRENT_REVISION = 4` 自动升级旧版本配置

---

## 安装与快速启动

### 前置要求

- Python 3.10+
- 推荐使用 [uv](https://github.com/astral-sh/uv) 管理器

### 开发级安装

```bash
# 克隆源仓库
cd PanCLI

# uv 安装（推荐）
uv pip install -e .

# 或标准 pip 安装
pip install -e .

# 启动！
pancli
```

### 更新（Git Pull）

```bash
git pull origin main
# 代码立即生效（可编辑模式 -e）
```

---

## 使用方式

### 模式一：单次命令（Typer 路由）

```bash
pancli ls home -h                    # 列出目录，-h 人类可读大小
pancli tree home --depth 5           # 显示目录树，最大深度 5
pancli find homework --path home -d 5 # 搜索 "homework" 文件
pancli stat home/file.pdf            # 查看文件元信息
pancli mkdir home/newdir/subdir      # 递归创建目录
pancli rm home/old.txt              # 删除文件
pancli mv home/a.txt home/b.txt      # 移动/重命名
pancli cp home/a.txt home/b.txt      # 复制文件

# 上传/下载（支持并发）
pancli upload ./local_file home/dir -j 8   # 8 并发上传
pancli download home/bigdir ./local -j 8 -r # 递归下载整个目录
```

### 模式二：交互式 Shell

```bash
pancli              # 直接进入 REPL
pancli shell        # 等效，显式进入 Shell
```

进入 Shell 后，享受类似 Bash 的状态保持体验：

```bash
PanCLI [/home/你的名字] $ ls
PanCLI [/home/你的名字] $ cd documents
PanCLI [/home/你的名字/documents] $ find report
PanCLI [/home/你的名字/documents] $ upload ./local.pdf . -j 4
PanCLI [/home/你的名字/documents] $ download remote.pdf . -j 4
PanCLI [/home/你的名字/documents] $ exit
```

### 全局选项

```bash
pancli --whoami          # 查看本地缓存的账号信息
pancli --logout          # 清除登录凭据
pancli --version         # 输出版本号
pancli -h                # 显示帮助
```

---

## 命令参考

| 命令 | 描述 | 示例 |
|------|------|------|
| `ls [path] [-h]` | 列出目录 | `pancli ls home -h` |
| `tree [path] [--depth N]` | 显示目录树 | `pancli tree home -d 3` |
| `find <keyword> [--path p] [--depth d]` | **新增** 全局搜索 | `pancli find homework -p home -d 5` |
| `stat <path>` | 查看元信息 | `pancli stat home/file.pdf` |
| `mkdir <path>` | 创建目录 | `pancli mkdir home/a/b/c` |
| `touch <path>` | 创建空文件 | `pancli touch home/empty.txt` |
| `rm <path> [-r]` | 删除 | `pancli rm home/trash -r` |
| `mv <src> <dst> [-f]` | 移动/重命名 | `pancli mv a.txt b.txt -f` |
| `cp <src> <dst> [-f]` | 复制 | `pancli cp a.txt b.txt -f` |
| `cat <file> [--head N] [--tail N]` | 查看文件内容 | `pancli cat home/readme.txt --head 20` |
| `upload <local> [remote] [-j N] [-r]` | 上传文件/目录 | `pancli upload ./f home -j 4 -r` |
| `download <remote> [local] [-j N] [-r]` | 下载文件/目录 | `pancli download home/dir . -j 4 -r` |
| `link <path> [-c/-d]` | 外链管理 | `pancli link home/file.pdf -c` |
| `shell` | 进入交互式 Shell | `pancli shell` |

---

## 架构设计

```
main.py          Typer app 入口 + 各命令路由
  ├─ shell.py    prompt-toolkit 交互 Shell（pancli shell）
  ├─ transfer.py 并发传输引擎（Semaphore + Rich Progress + 断点续传）
  ├─ api.py      AsyncApiManager（全异步业务 API）
  ├─ network.py  httpx.AsyncClient + 同步 Client（供 auth）
  ├─ auth.py     OAuth2 登录（保持同步）
  ├─ config.py   platformdirs 配置管理
  └─ models.py   Pydantic 数据模型
```

### 模块说明

| 模块 | 职责 |
|------|------|
| `models.py` | Pydantic 数据模型（`FileMetaData`, `TransferTask`, `AppConfig`, `SearchResult` 等） |
| `config.py` | `platformdirs` 配置管理，支持版本迁移，新增 `theme` 字段 |
| `network.py` | HTTP 传输层，保留同步接口供 `auth.py` 使用，新增全量异步接口 |
| `auth.py` | OAuth2 登录 + RSA 加密（无变更，逻辑保持） |
| `api.py` | `AsyncApiManager` 全异步业务 API，支持 `search_recursive` 搜索 |
| `transfer.py` | **新增** 并发传输引擎，`batch_download` / `batch_upload` 带 Rich 多行进度条 |
| `main.py` | Typer 入口，无子命令默认进入 Shell，支持 `--whoami` / `--logout` 全局回调 |
| `shell.py` | prompt-toolkit REPL，复用 `main.py` 业务逻辑，维护 CWD 状态 |

---

## 主题配色

PanCLI v3 使用 Rich Theme 系统，**严禁硬编码纯黑/纯白**：

| 元素 | 浅色终端 | 深色终端 |
|------|----------|----------|
| 普通文本 | 默认色 | 默认色 |
| 文件夹 | Cyan | Cyan |
| 文件 | White/默认 | White/默认 |
| 成功 | Green Bold | Green |
| 警告 | Yellow Bold | Yellow |
| 错误 | Red Bold | Red Bold |

通过 `~/.config/bhpan/config.json` 中的 `theme` 字段切换（`auto` / `dark` / `light`）。

---

## 断点续传原理

```python
# 下载时自动检测本地已有文件大小
local_size = Path(dest).stat().st_size if Path(dest).exists() else 0
if local_size < remote_size:
    headers["Range"] = f"bytes={local_size}-"
    # 从断点处继续下载
```

---

## 并发传输原理

```python
# transfer.py
semaphore = asyncio.Semaphore(jobs)  # 控制最大并发数

async def worker(task):
    async with semaphore:
        # 执行下载/上传
        ...

# 并发执行所有任务
await asyncio.gather(*[worker(t) for t in tasks])
```

---

## 进阶配置

配置文件位于：
- **Linux/macOS**: `~/.config/bhpan/config.json`
- **Windows**: `C:\Users\<用户名>\AppData\Local\bhpan\config.json`

示例配置：

```json
{
  "revision": 4,
  "host": "bhpan.buaa.edu.cn",
  "username": "你的学号",
  "encrypted": "RSA加密后的密码",
  "store_password": true,
  "theme": "auto",
  "cached_token": {
    "token": "...",
    "expires": 1234567890.0
  }
}
```

---

## 鸣谢

- 项目初始逻辑参考 [xdedss/dist_bhpan](https://github.com/xdedss/dist_bhpan)
- API 文档参考 [AnyShare 开放文档](https://developers.aishutech.com/openDoc?productId=1&versonId=30&docId=338)
