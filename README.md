# pansh

AnyShare / 北航网盘命令行工具，支持：

- `pip install .` / `pip install -e .`
- `pansh` / `python -m pansh`
- Typer 单次命令与交互式 shell
- Rich 进度条、实时速率、平均速率、ETA、状态
- 多文件、`glob`、`regex` 上传下载
- `platformdirs` 用户配置目录
- YAML 设置文件和 JSON 凭据缓存

## 安装

```bash
pip install -e .
```

安装后可直接使用：

```bash
pansh --help
python -m pansh --help
pansh --version
```

## 配置目录

默认配置目录使用 `platformdirs.user_config_dir("pansh")`。

典型文件布局：

```text
settings.yaml
auth.json
```

支持环境变量覆盖配置文件路径：

```bash
pansh_CONFIG=/path/to/settings.yaml
```

首次运行会自动生成内置默认 `settings.yaml` 模板。

## 常用命令

```bash
pansh ls home
pansh stat home/file.pdf
pansh whoami --json
pansh quota --json
```

### 上传

```bash
pansh upload a.txt b.txt c.txt home/test -y
pansh upload --glob "*.pdf" --glob "*.docx" home/test -y
pansh upload --regex ".*\\.(pdf|docx)$" ./docs home/test --recursive -y
pansh upload src1 src2 src3 --exclude "*.tmp" home/test -y
```

### 下载

```bash
pansh download home/a.pdf home/b.pdf ./downloads -y
pansh download --glob "*.zip" ./downloads -y
pansh download --regex ".*2026.*\\.pdf$" home/docs ./downloads --recursive -y
pansh download --search --regex ".*报告.*" --range home/docs ./downloads -y
```

### 输出模式

```bash
pansh ls home --plain
pansh ls home --json
pansh find 报告 --json
```

## Shell

```bash
pansh
pansh shell
```

交互模式内置命令：

- `cd`, `pwd`
- `lcd`, `lpwd`, `lls`
- `!<cmd>` 执行本地 shell 命令
- 其余命令复用同一套 CLI 实现

## 进阶命令

```bash
pansh revisions home/file.docx
pansh restore-revision home/file.docx REVISION_ID
```
