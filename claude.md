# Claude开发记录 - 电子书整理系统

## 项目概述

这是一个用于整理大量电子书的Python工具，支持从Calibre数据库或直接从文件系统扫描并整理电子书。

### 主要功能

- ✅ 智能语言识别（日文/英文/中文）
- ✅ 从电子书文件内部提取元数据（EPUB/MOBI/AZW3）
- ✅ 作者前缀显示最早作品发行年月 `[YYYY-MM] 作者名/`
- ✅ 多层分类：语言 → 类型 → 作者 → 系列/书名
- ✅ TXT文件单独存放
- ✅ 断点续传机制
- ✅ 干运行模式预览

## 技术栈

- Python 3.10
- ebooklib - EPUB元数据解析
- mobi - MOBI/AZW3元数据解析
- chardet - 字符编码检测
- lxml - XML解析
- sqlite3 - 进度跟踪

## 项目结构

```
ebook_organizer/
├── organize.py              # 基于Calibre数据库的完整版本
├── organize_simple.py       # 直接扫描文件系统的简化版本
├── metadata_parser.py       # 元数据解析器
├── config.json              # 配置文件
├── requirements.txt         # Python依赖
├── README.md                # 用户文档
├── claude.md                # 本文件 - 开发记录
├── .gitignore              # Git忽略配置
└── progress.db             # 进度数据库（自动生成）
```

## 已完成的工作

### 阶段1：环境搭建 ✅
- [x] 创建conda环境 `ebook`
- [x] 安装依赖包（ebooklib, mobi, chardet等）
- [x] 配置UTF-8编码支持（Windows兼容）

### 阶段2：核心功能开发 ✅
- [x] 元数据解析器
  - EPUB内部元数据提取
  - MOBI/AZW3内部元数据提取
  - 文件名推断元数据
  - 语言检测（日文/英文/中文）

- [x] 主整理脚本 (organize.py)
  - Calibre数据库读取
  - 作者最早作品日期计算
  - 智能分类（日文轻小说/文芸/推理等）
  - 文件复制和目录创建
  - 进度跟踪和断点续传

- [x] 简化版脚本 (organize_simple.py)
  - 文件系统直接扫描
  - 不依赖Calibre数据库
  - 支持大规模文件处理

### 阶段3：增强功能 ✅
- [x] 语言标准化
  - 统一所有语言为 eng/jpn/zho
  - 其他欧洲语言映射到eng
  - 从书名智能推断语言

- [x] 特殊格式处理
  - TXT文件单独放置到 `TXT文件/` 目录

- [x] 路径处理
  - Windows非法字符清理
  - 路径长度限制（240字符）
  - 中日英文文件名支持

### 阶段4：文档和部署 ✅
- [x] README.md 用户文档
- [x] config.json 配置说明
- [x] Git仓库初始化
- [x] 项目迁移到工作目录

## 待完成的工作

### 高优先级 🔴

1. **创建整合版脚本 (organize_merged.py)** ✅ 已完成
   - [x] 同时处理Calibre数据库和文件系统
   - [x] 智能去重机制
     - 文件大小比对
     - 文件名相似度计算
     - 部分内容哈希比对 (MD5部分哈希)
   - [x] Calibre元数据优先策略
   - [x] 冲突处理和报告
   - [x] 完整的process_item实现
   - [ ] 优化扫描性能（当前扫描50K+书籍较慢）
   - [ ] 测试和验证

2. **完善作者日期前缀功能**
   - [ ] 从EPUB/MOBI内部提取出版日期
   - [ ] 缓存作者-日期映射到文件
   - [ ] 支持手动修正日期

3. **增强分类逻辑**
   - [ ] 改进轻小说识别
   - [ ] 添加更多出版社关键词
   - [ ] 支持自定义分类规则

### 中优先级 🟡

4. **性能优化**
   - [ ] 多线程文件复制
   - [ ] 元数据缓存机制
   - [ ] 批量处理优化

5. **错误处理**
   - [ ] 更详细的错误日志
   - [ ] 自动重试失败文件
   - [ ] 损坏文件检测和隔离

6. **测试**
   - [ ] 单元测试
   - [ ] 集成测试
   - [ ] 大规模数据测试

### 低优先级 🟢

7. **用户体验**
   - [ ] 进度条显示
   - [ ] Web界面
   - [ ] 配置向导

8. **扩展功能**
   - [ ] PDF支持
   - [ ] 封面提取和整理
   - [ ] 重复书籍检测（不同格式）

## 已知问题

1. **扫描性能**
   - 问题：扫描4万+文件时较慢
   - 影响：首次运行需要较长时间
   - 解决方案：添加文件数量预估和进度显示

2. **网络驱动器Git支持**
   - 问题：Git在网络驱动器(Z盘)上有兼容性问题
   - 解决方案：已迁移到本地D盘

3. **语言检测准确性**
   - 问题：部分书籍语言检测不准确
   - 影响：少量书籍可能分类错误
   - 改进方向：增加更多语言特征检测

## 配置说明

### config.json 关键参数

```json
{
  "source_folders": [
    "z:\\电子书\\日语\\已放入calibre",
    "z:\\电子书\\英语书大全已放入calibre"
  ],
  "target_library": "z:\\电子书\\整理后书库",
  "use_calibre_db": false,
  "supported_formats": ["epub", "azw3", "mobi", "txt", "pdf"],
  "txt_folder": "TXT文件"
}
```

- `source_folders`: 源书籍目录列表
- `target_library`: 整理后的目标目录
- `use_calibre_db`: 是否使用Calibre数据库
- `supported_formats`: 支持的文件格式
- `txt_folder`: TXT文件单独存放的目录名

## 使用场景

### 场景1：基于Calibre数据库整理
```bash
conda activate ebook
cd d:\work-2025\ebook_organizer
python organize.py --preview 50      # 预览前50本
python organize.py --dry-run         # 干运行
python organize.py                   # 正式执行
```

### 场景2：直接扫描文件系统
```bash
conda activate ebook
python organize_simple.py --dry-run --limit 100
python organize_simple.py
```

### 场景3：断点续传
```bash
python organize.py --resume          # 继续上次任务
python organize.py --retry-failed    # 重试失败项
```

## 目标输出结构

```
整理后书库/
├── 日文图书/
│   ├── ライトノベル/
│   │   ├── [2009-04] 川原礫/
│   │   │   └── ソードアート・オンライン/
│   │   │       ├── 01 ソードアート・オンライン.epub
│   │   │       ├── 01 ソードアート・オンライン.mobi
│   │   │       └── 02 ...
│   │   └── [2015-06] 其他作者/
│   ├── 文芸/
│   ├── ミステリー/
│   └── SF・ファンタジー/
│
├── 英文图书/
│   ├── Fiction/
│   │   ├── Mystery/
│   │   │   └── [1997-06] Author Name/
│   │   ├── Science Fiction/
│   │   └── Fantasy/
│   └── Non-Fiction/
│
├── 中文图书/
└── TXT文件/
```

## 开发笔记

### 数据库结构 (Calibre)

重要表：
- `books` - 书籍基本信息
- `authors` - 作者
- `books_authors_link` - 书籍-作者关联
- `series` - 系列
- `books_series_link` - 书籍-系列关联
- `tags` - 标签
- `languages` - 语言
- `publishers` - 出版社

关键字段：
- `books.pubdate` - 出版日期
- `books.series_index` - 系列中的序号（在books表而非link表）
- `languages.lang_code` - 语言代码

### 元数据提取技巧

1. **EPUB**: 使用ebooklib读取OPF元数据
2. **MOBI**: 使用mobi库提取到临时目录，解析OPF
3. **语言检测**:
   - 日文：平假名/片假名特征
   - 中文：汉字但无假名
   - 英文：ASCII字母占主导

### 性能考虑

- 50,175本书的数据库查询优化
- 文件复制使用shutil.copy2保留元数据
- 进度数据库使用索引加速查询
- 作者日期缓存避免重复计算

## 后续计划

### 近期（1-2周）

1. 实现整合版脚本支持去重
2. 完善作者日期提取
3. 添加进度显示

### 中期（1个月）

1. 性能优化和多线程支持
2. 完善测试覆盖
3. Web界面原型

### 远期

1. 云端同步支持
2. 移动端阅读集成
3. 元数据编辑功能

## GitHub仓库

- 仓库名：`ebook-organizer`
- 用户：xbfool (xbfool@gmail.com)
- 本地路径：`d:\work-2025\ebook_organizer`

## 许可

MIT License - 仅供个人使用，请尊重版权。

---

*最后更新：2025-10-25*
*开发者：Claude (Anthropic) & xbfool*
