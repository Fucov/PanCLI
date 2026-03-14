# BHPAN CLI 现代化重构版

本项目提供了上传、下载、管理北航网盘文件的精美现代化命令行工具。
基于 Typer + Rich + httpx + Pydantic 构筑，适合在无 GUI 的服务器上高效使用。

> 核心 API 参考自：[Anyshare OpenDoc](https://developers.aishutech.com/openDoc/product/2/version/3/doc/15)

## 特性亮点

- **精美终端 UI**：使用 Rich 渲染带颜色的目录表格、文件结构树和超高颜值的上传/下载进度条。
- **现代化架构**：
  - **网络层**：使用 `httpx` 连接池复用，支持流式并发网络 IO，内置 SSL 证书补丁与智能重试机制。
  - **参数解析**：使用 `Typer` 提供开箱即用的 `--help` 与完美输入校验。
  - **数据验证**：使用 `Pydantic` 将散乱字典收敛为前置校验强类型对象。
- **安全与标准**：使用 `platformdirs` 将密码与缓存合规安放于操作系统的 `user_config_dir`，告别硬编码。
- **打包友好**：无缝支持 PyInstaller，无冗余 C 扩展，极简打包为单执行文件。

---

## 安装说明

### 环境要求
- Python 3.10+

### 安装步骤

1. 克隆代码或下载源码到本地：
```bash
cd /Users/ykw/Code/Pycharm/PanCLI
```

2. 安装依赖并注册为系统全局命令行工具（推荐）：
```bash
pip install -e .
```
> 安装完成后，你可以在系统的任何位置直接使用 `bhpan` 命令。

### 第一次运行授权说明
1. 第一次运行任意命令（如 `bhpan ls home`）时，会提示你在终端里输入`Username:` 与 `Password:`。
2. **凭据存储**：程序将使用北航网盘官方提供的公钥以 RSA 加密你的密码，并存储在当前系统的标准用户数据配置目录下：
   - Windows: `%APPDATA%/bhpan/config.json`
   - macOS: `~/Library/Application Support/bhpan/config.json`
   - Linux: `~/.config/bhpan/config.json`
3. 如果你不希望在磁盘存储凭据（每次手动输入），你可以找到上述的 `config.json` 文件，并将其中的 `"store_password": true` 改为 `false`。

---

## 命令指南

### 列出目录内容 (`ls`)
列出远程文件夹，支持可读文件大小格式。
```bash
bhpan ls [远程文件/文件夹]

# 例：列出网盘文档根目录，并以人类可读（K/M/G）显示文件大小
bhpan ls home -h
```

### 上传文件 (`upload`)
将本地文件或目录上传到云端。
```bash
bhpan upload [本地文件/文件夹] [远程目标系统文件夹]

# 递归上传整个目录
bhpan upload docs/ home/my_docs -r

# 上传并重命名
bhpan upload video.mp4 home/ --rename new_video.mp4
```

### 下载文件 (`download`)
将云端文件或目录下载到本地计算机。
```bash
bhpan download [远程文件/文件夹] [本地目标目录]

# 递归下载云端的整个目录
bhpan download home/my_docs /tmp/local_docs -r
```

### 删除文件 / 目录 (`rm`)
```bash
bhpan rm [远程文件/文件夹]

# 删除整个目录及内容
bhpan rm home/useless_folder -r
```

### 查看并读取文件内容 (`cat`)
将远程远端文件内容直接吐出到 `stdout`，非常适合配合 `grep`, `tail` 等 Linux 下游管道命令一同处理。
```bash
bhpan cat home/app.log | tail -n 50
```

### 移动与重命名 (`mv` / `cp`)
文件系统内操作机制与标准 Linux 无缝对齐。
```bash
# 重命名
bhpan mv home/test.png home/test2.png

# 移动到存在的目录
bhpan mv home/dir1/test.png home/dir2/dir3

# 移动并重命名
bhpan mv home/dir1/test.png home/dir2/dir3/test2.png

# 使用 -f 强制覆盖目标位置已存在的文件
bhpan mv home/test.png home/foo.png -f

# 复制文件 (cp)
bhpan cp home/test.png home/test2.png
```

### 创建目录 (`mkdir`)
一次性递归创建多级目录，路径不存在的部分自动补齐。
```bash
bhpan mkdir home/test/1/2/3
```

---

## 外链与分享 (`link`)

北航网盘支持为指定文件动态生成分享拉取外链，提供类似百度网盘的提取码分享功能。

### 查看文件的外链信息
如果当前文件已有启用的对外链接，此命令将显示 URL 与提取码。
```bash
bhpan link show [远程文件/文件夹]
```

### 创建分享链接
若文件尚无分享链接，使用此命令创建；若已有链接，此命令将以最新参数**覆盖修改**其外链权限。
```bash
# 常见用法：
bhpan link create home/share.zip

# 参数详解：
# -p, --password      生成带提取码保护的私密分享（默认公开）
# --allow-upload      生成的文件收集黑洞链接（允许任意陌生人向该链接上传文件）
# --no-download       生成禁止下载与预览的链接
# -e, --expires <天数> 指定链接在多少天后失效（默认 30 天）

# 高级组合示例：加密收集信箱，7天后失效，不准对方下载
bhpan link create home/InboxFolder -p -e 7 --allow-upload --no-download
```

### 取消分享链接
强制终止对某文件的公共访问权限。
```bash
bhpan link delete home/share.zip
```

---

## 开发者文档

### 架构一览
```
pancli/
├── __init__.py    # 版本号
├── models.py      # Pydantic 数据模型层（FileMetaData/ResourceInfo/LinkInfo）
├── config.py      # platformdirs 配置管理持久层
├── network.py     # httpx 封装内核（自动处理重定向追问、Session 降级与证书验证）
├── auth.py        # OAuth2 授权处理引擎与 RSA 哈希换算环
├── api.py         # ApiManager：抽象出网盘云端底层的所有 RESTful 函数映射
└── main.py        # 顶层交互枢纽（Typer 路由映射及 Rich 组件驱动）
```

### PyInstaller 单体应用打包
如果你希望把它部署给不想装 Python 运行时的普通同事，可以在克隆代码库后执行打包：
```bash
pip install pyinstaller
pyinstaller --onefile --name bhpan run.py
```
这将在 `dist/` 文件夹下方产出一个跨终端的 `bhpan` 单文件二进制包。
