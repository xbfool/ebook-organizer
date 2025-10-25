#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书整理系统 - 简化版
直接扫描文件系统，不依赖Calibre数据库
"""

import os
import sys
import json
import sqlite3
import logging
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import re

# 设置标准输出为UTF-8编码
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'ignore')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'ignore')

from metadata_parser import MetadataParser


class SimpleProgressTracker:
    """简化的进度跟踪器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_progress (
                file_path TEXT PRIMARY KEY,
                file_name TEXT,
                status TEXT DEFAULT 'pending',
                target_path TEXT,
                error_message TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_status ON file_progress(status)
        ''')

        self.conn.commit()

    def add_file(self, file_path: str, file_name: str):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO file_progress (file_path, file_name)
                VALUES (?, ?)
            ''', (file_path, file_name))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def update_status(self, file_path: str, status: str, target_path: str = None, error: str = None):
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE file_progress
            SET status = ?, target_path = ?, error_message = ?, processed_at = CURRENT_TIMESTAMP
            WHERE file_path = ?
        ''', (status, target_path, error, file_path))
        self.conn.commit()

    def get_status(self, file_path: str) -> str:
        cursor = self.conn.cursor()
        cursor.execute('SELECT status FROM file_progress WHERE file_path = ?', (file_path,))
        result = cursor.fetchone()
        return result[0] if result else 'pending'

    def get_pending_files(self) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT file_path FROM file_progress WHERE status = "pending"')
        return [row[0] for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, int]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM file_progress
            GROUP BY status
        ''')
        stats = dict(cursor.fetchall())
        return {
            'total': sum(stats.values()),
            'success': stats.get('success', 0),
            'failed': stats.get('failed', 0),
            'pending': stats.get('pending', 0)
        }

    def reset_failed(self):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE file_progress SET status = "pending" WHERE status = "failed"')
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


class SimpleEbookOrganizer:
    """简化的电子书整理器 - 直接扫描文件系统"""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.logger = logging.getLogger(__name__)

        self.progress = SimpleProgressTracker(self.config['progress_db'])
        self.parser = MetadataParser()

        # 作者名到最早日期的缓存
        self.author_date_cache = {}

    def _load_config(self, config_path: str) -> dict:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.config['log_file'], encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def _sanitize_filename(self, name: str) -> str:
        """清理文件名中的非法字符"""
        illegal_chars = r'<>:"/\|?*'
        for char in illegal_chars:
            name = name.replace(char, '_')
        name = name.strip('. ')
        if len(name) > 200:
            name = name[:200]
        return name

    def _detect_language_from_path(self, file_path: str) -> str:
        """从文件路径推断语言"""
        path_lower = file_path.lower()

        if '日语' in file_path or '日文' in file_path:
            return 'jpn'
        if '英语' in file_path or '英文' in file_path:
            return 'eng'
        if '中文' in file_path or '中国' in file_path:
            return 'zho'

        return 'unknown'

    def _get_author_date_prefix(self, author: str) -> str:
        """
        获取作者的日期前缀（从文件修改时间推断）
        由于没有Calibre数据库，我们暂时使用未知，或者未来可以从文件内部解析
        """
        if author in self.author_date_cache:
            return self.author_date_cache[author]

        # 暂时返回未知，后续可以改进为从文件元数据提取
        prefix = "[未知]"
        self.author_date_cache[author] = prefix
        return prefix

    def _classify_book(self, metadata: Dict, language: str) -> Tuple[str, str]:
        """
        分类书籍

        Returns:
            (主分类, 子分类)
        """
        # 简化分类逻辑
        if language == 'jpn':
            # 日文书籍简单分类
            if '文庫' in str(metadata.get('publisher', '')) or 'ノベル' in str(metadata.get('title', '')):
                return 'ライトノベル', ''
            return 'その他', ''

        elif language == 'eng':
            # 英文书籍简单分类
            return 'Fiction', ''

        else:
            return '其他', ''

    def _build_target_path(self, file_path: str, metadata: Dict, language: str) -> str:
        """
        构建目标路径

        格式: 语言/分类/[YYYY-MM] 作者/书名/
        """
        lang_folder = self.config['language_names'].get(language, '其他语言')

        # 获取作者
        authors = metadata.get('authors', [])
        if authors:
            author = authors[0]
        else:
            # 尝试从文件名推断作者
            file_name = Path(file_path).stem
            author_match = re.search(r'\[([^\]]+)\]', file_name)
            author = author_match.group(1) if author_match else "未知"

        date_prefix = self._get_author_date_prefix(author)
        author_folder = f"{date_prefix} {self._sanitize_filename(author)}"

        # 获取书名
        title = metadata.get('title') or Path(file_path).stem

        # 分类
        category, subcategory = self._classify_book(metadata, language)

        # 构建路径
        if language == 'jpn':
            if category == 'ライトノベル':
                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category,
                    author_folder,
                    self._sanitize_filename(title)
                )
            else:
                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category,
                    author_folder,
                    self._sanitize_filename(title)
                )
        else:
            base_path = os.path.join(
                self.config['target_library'],
                lang_folder,
                category,
                author_folder,
                self._sanitize_filename(title)
            )

        return base_path

    def _process_file(self, file_path: str, dry_run: bool = False) -> bool:
        """
        处理单个文件

        Returns:
            是否成功
        """
        try:
            file_ext = Path(file_path).suffix.lower().lstrip('.')

            # 检查是否为支持的格式
            if file_ext not in self.config['supported_formats']:
                self.logger.debug(f"Skipping unsupported format: {file_path}")
                self.progress.update_status(file_path, 'skipped')
                return False

            # 从文件内部或文件名解析元数据
            metadata = self.parser.get_metadata(file_path, file_ext)

            # 推断语言
            language = metadata.get('language', 'unknown')
            if language == 'unknown':
                # 从路径推断
                language = self._detect_language_from_path(file_path)

            if language == 'unknown':
                # 从文件名推断
                language = self.parser.detect_language_from_content(Path(file_path).stem)

            # 标准化语言
            language = self._normalize_language(language)

            # 处理TXT文件
            if file_ext == 'txt':
                txt_folder = os.path.join(self.config['target_library'], self.config['txt_folder'])
                if not dry_run:
                    os.makedirs(txt_folder, exist_ok=True)
                target_file = os.path.join(txt_folder, Path(file_path).name)
            else:
                # 构建目标路径
                target_base = self._build_target_path(file_path, metadata, language)
                if not dry_run:
                    os.makedirs(target_base, exist_ok=True)
                target_file = os.path.join(target_base, Path(file_path).name)

            # 复制文件
            if dry_run:
                self.logger.info(f"[DRY RUN] {file_path} -> {target_file}")
            else:
                shutil.copy2(file_path, target_file)
                self.logger.info(f"Copied: {target_file}")

            self.progress.update_status(file_path, 'success', target_path=target_file)
            return True

        except Exception as e:
            self.logger.error(f"Failed to process {file_path}: {e}", exc_info=True)
            self.progress.update_status(file_path, 'failed', error=str(e))
            return False

    def _normalize_language(self, lang: str) -> str:
        """标准化语言代码"""
        if not lang:
            return 'unknown'

        lang = lang.lower()

        if lang in ('jpn', 'ja', 'japanese'):
            return 'jpn'
        if lang in ('eng', 'en', 'english'):
            return 'eng'
        if lang in ('zho', 'zh', 'chi', 'chinese'):
            return 'zho'

        return 'unknown'

    def scan_files(self) -> List[str]:
        """扫描源文件夹中的所有电子书文件"""
        all_files = []
        supported_exts = set(self.config['supported_formats'])

        for source_folder in self.config['source_folders']:
            self.logger.info(f"Scanning folder: {source_folder}")

            if not os.path.exists(source_folder):
                self.logger.warning(f"Folder not found: {source_folder}")
                continue

            for root, dirs, files in os.walk(source_folder):
                for file in files:
                    file_ext = Path(file).suffix.lower().lstrip('.')
                    if file_ext in supported_exts:
                        file_path = os.path.join(root, file)
                        all_files.append(file_path)
                        self.progress.add_file(file_path, file)

        self.logger.info(f"Found {len(all_files)} files")
        return all_files

    def run(self, dry_run: bool = False, limit: int = None, resume: bool = False, retry_failed: bool = False):
        """运行整理任务"""
        if retry_failed:
            self.logger.info("Retrying failed files...")
            self.progress.reset_failed()

        if not resume:
            self.logger.info("Scanning files...")
            all_files = self.scan_files()
        else:
            self.logger.info("Resuming previous task...")

        # 获取待处理文件
        pending_files = self.progress.get_pending_files()
        total = len(pending_files)

        if limit:
            pending_files = pending_files[:limit]

        self.logger.info(f"Processing {len(pending_files)} files (Total pending: {total})")

        success_count = 0
        failed_count = 0

        for idx, file_path in enumerate(pending_files, 1):
            self.logger.info(f"[{idx}/{len(pending_files)}] Processing: {Path(file_path).name}")

            if self._process_file(file_path, dry_run):
                success_count += 1
            else:
                failed_count += 1

            if idx % 100 == 0:
                self.logger.info(f"Progress: {idx}/{len(pending_files)} | Success: {success_count} | Failed: {failed_count}")

        # 统计
        stats = self.progress.get_statistics()
        self.logger.info("="*60)
        self.logger.info("Organization completed!")
        self.logger.info(f"Total files: {stats['total']}")
        self.logger.info(f"Success: {stats['success']}")
        self.logger.info(f"Failed: {stats['failed']}")
        self.logger.info(f"Pending: {stats['pending']}")
        self.logger.info("="*60)

    def close(self):
        if self.progress:
            self.progress.close()


def main():
    parser = argparse.ArgumentParser(description='电子书整理系统 - 简化版')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--dry-run', action='store_true', help='干运行模式')
    parser.add_argument('--limit', type=int, metavar='N', help='限制处理数量')
    parser.add_argument('--resume', action='store_true', help='继续上次任务')
    parser.add_argument('--retry-failed', action='store_true', help='重试失败的任务')

    args = parser.parse_args()

    if not os.path.isabs(args.config):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, args.config)
    else:
        config_path = args.config

    organizer = SimpleEbookOrganizer(config_path)

    try:
        organizer.run(
            dry_run=args.dry_run,
            limit=args.limit,
            resume=args.resume,
            retry_failed=args.retry_failed
        )
    finally:
        organizer.close()


if __name__ == '__main__':
    main()
