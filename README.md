# AnyShare (PanCLI) — 现代化命令行客户端

本项目是一个专为 **AnyShare 7 架构网盘**（如北航网盘）设计的现代化 CLI 工具。
采用 **Typer + prompt-toolkit + Rich + httpx.AsyncClient** 全异步架构，提供两种使用模式：

| 模式 | 使用方式 | 场景 |
|---|---|---|
| **沉浸式 Shell** | 直接运行 `pancli` | 交互式文件管理，Tab 补全，CWD 状态保持 |
| **单次命令** | `pancli ls home` / `pancli upload file.txt .` | 脚本自动化、CI/CD 流水线 |

## ✨ 核心亮点

- **双模架构**：Typer 路由单次命令 + prompt-toolkit 沉浸式 REPL，共享同一套异步业务引擎
- **全异步网络**：基于 `httpx.AsyncClient`，所有 API 调用均为 async
- **并发传输**：`upload`/`download` 支持 `-j N` 多任务并发（docker pull 风格多行进度条）
- **断点续传**：下载中断后再次执行，自动从断点追加（HTTP `Range` header）
- **Tab 智能补全**：上下文感知 — 自动区分远程网盘路径 vs 本地文件系统补全
- **全局搜索**：`find` 命令支持 `*` `?` 通配符递归搜索
- **本地穿透**：Shell 中 `!ls -al` 直接执行本地命令，`!cd` 切换本地目录
- **泛 AnyShare 兼容**：修改 `config.json` 即可连接任意 AnyShare 分发中心

---

## 🛠 安装

### 前置要求
- Python 3.10+
- 推荐使用 [uv](https://github.com/astral-sh/uv)

### 方式一：uv（推荐）
```bash
cd PanCLI
uv pip install -e .
pancli              # 进入沉浸式 Shell
pancli ls home      # 单次列目录
```

### 方式二：标准 venv + pip
```bash
cd PanCLI
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
pancli
```

> **💡** 遇到 `command not found: pancli`？请确保已激活虚拟环境。

### 更新（Git Pull）
```bash
git pull origin main
# -e 可编辑模式下代码立即生效，无需重装
```
*(仅当引入了新的外部依赖时才需重新 `pip install -e .`)*

---

## 📦 PyInstaller 单体打包

```bash
uv pip install pyinstaller
pyinstaller --onefile --name pancli pancli/main.py
chmod +x dist/pancli   # 产物在 dist/ 目录下
```

---

## 📖 命令参考

### 沉浸式 Shell（`pancli` 直接进入）
| 分类 | 命令 | 说明 |
|---|---|---|
| **导航** | `ls [dir]`, `cd <dir>`, `pwd`, `tree [dir]` | 浏览网盘目录 |
| **文件** | `cat`, `head [-n N]`, `tail [-n N]`, `stat` | 查看内容/元信息 |
| **管理** | `touch`, `mkdir`, `rm [-r]`, `mv`, `cp` | 增删改 |
| **传输** | `upload <本地> [远程] [-r] [-j N]` | 并发上传 |
| | `download <远程> [本地] [-r] [-j N]` | 并发下载（断点续传） |
| **搜索** | `find <keyword> [-d depth]` | 递归搜索（支持 `*` `?` 通配符） |
| **账户** | `whoami`, `logout`, `su [user]` | 账户管理 |
| **穿透** | `!<cmd>`, `!cd <dir>` | 执行本地系统命令 |

### 单次命令（脚本化）
```bash
pancli ls home              # 列目录
pancli upload ./data . -r -j 4  # 递归并发上传
pancli download home/file . # 下载（支持断点续传）
pancli find "*.pdf" -d 3    # 搜索 PDF 文件
pancli --whoami             # 查看登录状态
pancli --logout             # 清除凭据
pancli -v                   # 查看版本
```

---

## ⚙️ 连接其他 AnyShare 阵地

编辑 `~/.config/bhpan/config.json`（macOS: `~/Library/Application Support/bhpan/config.json`），将 `host` 替换为目标网盘地址即可。

---

## 📚 鸣谢

- **基座灵感**：[xdedss/dist_bhpan](https://github.com/xdedss/dist_bhpan)（现已年久失修），我们在其基础上全面异步重构
- **协议文档**：[AnyShare RESTful API](https://developers.aishutech.com/openDoc?productId=1&versonId=30&docId=338)

