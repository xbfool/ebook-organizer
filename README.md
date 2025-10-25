# 电子书整理系统

自动整理Calibre电子书库，按照语言、类型、作者、系列进行智能分类。

## 功能特性

- ✅ 从Calibre数据库提取完整元数据
- ✅ 支持从电子书文件内部解析元数据（EPUB/MOBI/AZW3）
- ✅ 智能语言识别（日文/英文/中文）
- ✅ 多层分类：语言 → 类型 → [YYYY-MM] 作者 → 系列/书名
- ✅ 作者前缀显示最早作品发行年月，方便时间排序
- ✅ 断点续传机制，中断后可继续
- ✅ 详细日志和错误报告
- ✅ 保留所有格式（EPUB/MOBI/AZW3）

## 目录结构示例

```
整理后书库/
├── 日文图书/
│   ├── ライトノベル（轻小说）/
│   │   ├── 【有系列】/
│   │   │   ├── [2009-04] 川原礫/
│   │   │   │   └── ソードアート・オンライン/
│   │   │   │       ├── 01 ソードアート・オンライン.epub
│   │   │   │       ├── 01 ソードアート・オンライン.mobi
│   │   │   │       └── 02 ...
│   │   └── 【单行本】/
│   │       └── [2015-06] 某作者/
│   ├── 文芸（纯文学）/
│   ├── ミステリー（推理）/
│   └── SF・ファンタジー/
│
├── 英文图书/
│   ├── Fiction/
│   │   ├── Mystery/
│   │   ├── Science Fiction/
│   │   └── Fantasy/
│   └── Non-Fiction/
│
└── 中文图书/
```

## 环境准备

### 1. 创建Conda环境

```bash
conda create -n ebook python=3.10 -y
conda activate ebook
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

或者手动安装：

```bash
pip install ebooklib mobi python-magic-bin chardet lxml
```

## 使用方法

### 激活环境

每次使用前先激活conda环境：

```bash
conda activate ebook
```

### 1. 生成预览报告

在执行整理前，先生成预览报告查看分类效果：

```bash
python organize.py --preview 100
```

这会生成前100本书的分类预览，保存到 `preview_report.txt`。

### 2. 干运行模式（推荐）

先用干运行模式测试，只预览不实际移动文件：

```bash
# 干运行前10本书
python organize.py --dry-run --limit 10

# 干运行所有书籍
python organize.py --dry-run
```

### 3. 正式整理

确认预览无误后，执行正式整理：

```bash
# 整理所有书籍
python organize.py

# 限制整理前1000本（测试）
python organize.py --limit 1000
```

### 4. 断点续传

如果中途中断，可以继续上次任务：

```bash
python organize.py --resume
```

### 5. 重试失败的书籍

```bash
python organize.py --retry-failed
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--config CONFIG` | 指定配置文件路径（默认：config.json） |
| `--preview N` | 生成前N本书的预览报告 |
| `--dry-run` | 干运行模式（只预览不执行） |
| `--limit N` | 限制处理前N本书 |
| `--resume` | 继续上次任务 |
| `--retry-failed` | 重试失败的任务 |

## 配置说明

编辑 `config.json` 可以自定义：

- 源书库和目标书库路径
- 语言分类名称
- 轻小说识别关键词
- 文件格式优先级
- 最大路径长度

## 文件说明

- `organize.py` - 主整理脚本
- `metadata_parser.py` - 元数据解析器
- `config.json` - 配置文件
- `progress.db` - 进度数据库（自动生成）
- `organize.log` - 运行日志（自动生成）
- `error_books.txt` - 错误书籍列表（自动生成）
- `preview_report.txt` - 预览报告（使用--preview生成）

## 处理流程

1. 从Calibre的`metadata.db`读取书籍信息
2. 提取语言、作者、系列、标签、出版社等元数据
3. 智能分类：
   - 日文书籍：根据出版社和标签判断是否为轻小说
   - 英文书籍：根据标签分类为Mystery/SciFi/Fantasy等
   - 中文书籍：简单按作者和系列分类
4. 计算作者最早作品的发行年月作为前缀
5. 构建目标路径并复制文件（保留所有格式）
6. 记录处理状态到进度数据库

## 注意事项

1. **备份重要**：整理前请确保有完整备份
2. **空间充足**：需要足够磁盘空间（约原书库大小）
3. **路径长度**：Windows路径限制240字符，脚本会自动截断
4. **编码问题**：支持中日英文文件名
5. **断点续传**：可随时中断，不会重复处理已完成的书籍

## 常见问题

### Q: 如何重新开始整理？

删除 `progress.db` 文件，然后重新运行。

### Q: 某些书籍分类不准确怎么办？

可以编辑 `config.json` 中的分类关键词，或手动修改整理后的文件。

### Q: 整理后原文件会被删除吗？

不会。脚本使用复制而非移动，原Calibre书库保持不变。

### Q: 如何查看失败的书籍？

查看 `error_books.txt` 文件和 `organize.log` 日志。

### Q: 支持增量整理吗？

支持。新增书籍可以运行 `python organize.py --resume`，只处理新增的书籍。

## 示例工作流

```bash
# 1. 激活环境
conda activate ebook

# 2. 生成预览
python organize.py --preview 50

# 3. 查看预览报告
cat preview_report.txt

# 4. 干运行测试
python organize.py --dry-run --limit 100

# 5. 正式整理（建议先小批量测试）
python organize.py --limit 1000

# 6. 检查日志和错误
cat organize.log
cat error_books.txt

# 7. 继续整理剩余书籍
python organize.py --resume

# 8. 重试失败的书籍
python organize.py --retry-failed
```

## 许可

本工具仅供个人使用，请尊重版权。
