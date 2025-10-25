#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书整理系统 - 整合版
同时处理Calibre数据库和文件系统，支持智能去重
"""

import os
import sys
import json
import sqlite3
import logging
import shutil
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict

# 设置标准输出为UTF-8编码
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'ignore')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'ignore')

from metadata_parser import MetadataParser


class BookFingerprint:
    """书籍指纹 - 用于去重"""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file_name = Path(file_path).name
        self.file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        self.file_ext = Path(file_path).suffix.lower().lstrip('.')
        self.partial_hash = None

    def calculate_partial_hash(self, chunk_size: int = 1024 * 64) -> str:
        """计算文件部分哈希（前64KB+后64KB）"""
        if self.partial_hash:
            return self.partial_hash

        if not os.path.exists(self.file_path):
            return None

        try:
            hasher = hashlib.md5()
            file_size = os.path.getsize(self.file_path)

            with open(self.file_path, 'rb') as f:
                # 读取文件开头
                chunk = f.read(chunk_size)
                hasher.update(chunk)

                # 如果文件够大，读取文件结尾
                if file_size > chunk_size * 2:
                    f.seek(-chunk_size, 2)
                    chunk = f.read(chunk_size)
                    hasher.update(chunk)

            self.partial_hash = hasher.hexdigest()
            return self.partial_hash

        except Exception as e:
            logging.error(f"Failed to calculate hash for {self.file_path}: {e}")
            return None

    def is_duplicate(self, other: 'BookFingerprint', check_hash: bool = True) -> bool:
        """
        判断是否为重复文件

        Args:
            other: 另一个书籍指纹
            check_hash: 是否计算哈希值进行比对

        Returns:
            True if duplicate
        """
        # 格式不同，不算重复（保留所有格式）
        if self.file_ext != other.file_ext:
            return False

        # 文件大小相同
        if self.file_size == other.file_size:
            if not check_hash:
                return True

            # 计算部分哈希比对
            hash1 = self.calculate_partial_hash()
            hash2 = other.calculate_partial_hash()

            if hash1 and hash2 and hash1 == hash2:
                return True

        # 文件名非常相似（编辑距离）
        if self._name_similarity(other.file_name) > 0.9:
            return True

        return False

    def _name_similarity(self, other_name: str) -> float:
        """计算文件名相似度（简化版Levenshtein）"""
        name1 = Path(self.file_name).stem.lower()
        name2 = Path(other_name).stem.lower()

        if name1 == name2:
            return 1.0

        # 简单的相似度计算
        common_chars = sum(1 for c in name1 if c in name2)
        max_len = max(len(name1), len(name2))

        return common_chars / max_len if max_len > 0 else 0.0


class MergedProgressTracker:
    """整合的进度跟踪器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS merged_progress (
                source_type TEXT,
                source_id TEXT,
                file_path TEXT,
                title TEXT,
                status TEXT DEFAULT 'pending',
                target_path TEXT,
                error_message TEXT,
                is_duplicate BOOLEAN DEFAULT 0,
                duplicate_of TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_type, source_id)
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_status ON merged_progress(status)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_file_path ON merged_progress(file_path)
        ''')

        self.conn.commit()

    def add_item(self, source_type: str, source_id: str, file_path: str, title: str = None):
        """添加处理项"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO merged_progress
                (source_type, source_id, file_path, title)
                VALUES (?, ?, ?, ?)
            ''', (source_type, source_id, file_path, title))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def update_status(self, source_type: str, source_id: str, status: str,
                     target_path: str = None, error: str = None,
                     is_duplicate: bool = False, duplicate_of: str = None):
        """更新状态"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE merged_progress
            SET status = ?, target_path = ?, error_message = ?,
                is_duplicate = ?, duplicate_of = ?, processed_at = CURRENT_TIMESTAMP
            WHERE source_type = ? AND source_id = ?
        ''', (status, target_path, error, is_duplicate, duplicate_of, source_type, source_id))
        self.conn.commit()

    def get_pending_items(self) -> List[Tuple[str, str]]:
        """获取待处理项"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT source_type, source_id
            FROM merged_progress
            WHERE status = "pending"
        ''')
        return cursor.fetchall()

    def get_statistics(self) -> Dict[str, int]:
        """获取统计信息"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM merged_progress
            GROUP BY status
        ''')
        stats = dict(cursor.fetchall())

        cursor.execute('SELECT COUNT(*) FROM merged_progress WHERE is_duplicate = 1')
        duplicates = cursor.fetchone()[0]

        return {
            'total': sum(stats.values()),
            'success': stats.get('success', 0),
            'failed': stats.get('failed', 0),
            'pending': stats.get('pending', 0),
            'duplicates': duplicates
        }

    def reset_failed(self):
        """重置失败项"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE merged_progress SET status = "pending" WHERE status = "failed"')
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


class MergedEbookOrganizer:
    """整合版电子书整理器"""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.logger = logging.getLogger(__name__)

        self.progress = MergedProgressTracker(self.config['progress_db'].replace('progress.db', 'merged_progress.db'))
        self.parser = MetadataParser()

        # Calibre数据库连接
        if self.config.get('use_calibre_db', True):
            self.calibre_db = sqlite3.connect(self.config['metadata_db'])
        else:
            self.calibre_db = None

        # 去重缓存：file_path -> BookFingerprint
        self.fingerprint_cache: Dict[str, BookFingerprint] = {}

        # 作者日期缓存
        self.author_date_cache = {}

    def _load_config(self, config_path: str) -> dict:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _setup_logging(self):
        log_file = self.config['log_file'].replace('organize.log', 'merged_organize.log')
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def scan_all_sources(self) -> Tuple[int, int]:
        """
        扫描所有来源（Calibre数据库 + 文件系统）

        Returns:
            (calibre_count, filesystem_count)
        """
        calibre_count = 0
        filesystem_count = 0

        # 扫描Calibre数据库
        if self.calibre_db:
            self.logger.info("Scanning Calibre database...")
            cursor = self.calibre_db.cursor()
            cursor.execute('SELECT id, title, path FROM books')

            for book_id, title, path in cursor.fetchall():
                # 获取所有格式
                cursor.execute('SELECT format FROM data WHERE book = ?', (book_id,))
                formats = [row[0] for row in cursor.fetchall()]

                # 为每个格式添加到进度跟踪
                for fmt in formats:
                    file_path = os.path.join(self.config.get('calibre_library', ''), path, f"{Path(path).name}.{fmt.lower()}")
                    self.progress.add_item('calibre', f"{book_id}_{fmt}", file_path, title)
                    calibre_count += 1

        # 扫描文件系统
        if 'source_folders' in self.config:
            self.logger.info("Scanning filesystem folders...")
            for folder in self.config['source_folders']:
                if not os.path.exists(folder):
                    self.logger.warning(f"Folder not found: {folder}")
                    continue

                for root, dirs, files in os.walk(folder):
                    for file in files:
                        file_ext = Path(file).suffix.lower().lstrip('.')
                        if file_ext in self.config.get('supported_formats', ['epub', 'mobi', 'azw3']):
                            file_path = os.path.join(root, file)
                            self.progress.add_item('filesystem', file_path, file_path, file)
                            filesystem_count += 1

        self.logger.info(f"Scanned {calibre_count} from Calibre, {filesystem_count} from filesystem")
        return calibre_count, filesystem_count

    def _is_duplicate_of_processed(self, fingerprint: BookFingerprint) -> Optional[str]:
        """
        检查是否与已处理的文件重复

        Returns:
            如果重复，返回原文件路径；否则返回None
        """
        for existing_path, existing_fp in self.fingerprint_cache.items():
            if fingerprint.is_duplicate(existing_fp, check_hash=True):
                return existing_path

        return None

    def _sanitize_filename(self, name: str) -> str:
        """清理文件名中的非法字符"""
        illegal_chars = r'<>:"/\|?*'
        for char in illegal_chars:
            name = name.replace(char, '_')
        name = name.strip('. ')
        if len(name) > 200:
            name = name[:200]
        return name

    def _normalize_language(self, lang_code: str) -> str:
        """标准化语言代码"""
        if not lang_code or lang_code == 'unknown':
            return 'unknown'

        lang_code = lang_code.lower()

        if lang_code in ('eng', 'en', 'english'):
            return 'eng'
        if lang_code in ('jpn', 'ja', 'japanese'):
            return 'jpn'
        if lang_code in ('zho', 'zh', 'chi', 'chinese', 'cmn'):
            return 'zho'

        # 其他欧洲语言映射到英文
        if lang_code in ('deu', 'de', 'fra', 'fr', 'spa', 'es', 'ita', 'it', 'por', 'pt', 'rus', 'ru'):
            return 'eng'

        return 'unknown'

    def _get_author_date_prefix(self, author: str) -> str:
        """获取作者日期前缀"""
        if author in self.author_date_cache:
            return self.author_date_cache[author]

        # 暂时使用未知，后续可以从数据库或文件提取
        prefix = "[未知]"
        self.author_date_cache[author] = prefix
        return prefix

    def _build_target_path(self, metadata: Dict, language: str) -> str:
        """构建目标路径"""
        lang_folder = self.config['language_names'].get(language, '其他语言')

        # 获取作者
        authors = metadata.get('authors', [])
        if authors:
            if isinstance(authors[0], tuple):
                author = authors[0][1]  # Calibre格式: (id, name)
            else:
                author = authors[0]
        else:
            author = "未知"

        date_prefix = self._get_author_date_prefix(author)
        author_folder = f"{date_prefix} {self._sanitize_filename(author)}"

        # 获取书名
        title = metadata.get('title', 'Unknown')

        # 简化分类
        if language == 'jpn':
            category = 'その他'
        elif language == 'eng':
            category = 'Fiction'
        else:
            category = '其他'

        # 构建路径
        base_path = os.path.join(
            self.config['target_library'],
            lang_folder,
            category,
            author_folder,
            self._sanitize_filename(title)
        )

        return base_path

    def _copy_file(self, source_file: str, target_base: str, filename: str, dry_run: bool) -> bool:
        """复制单个文件"""
        try:
            target_file = os.path.join(target_base, filename)

            if dry_run:
                self.logger.info(f"[DRY RUN] {source_file} -> {target_file}")
            else:
                os.makedirs(target_base, exist_ok=True)
                shutil.copy2(source_file, target_file)
                self.logger.info(f"Copied: {target_file}")

            return True
        except Exception as e:
            self.logger.error(f"Failed to copy {source_file}: {e}")
            return False

    def process_item(self, source_type: str, source_id: str, dry_run: bool = False) -> bool:
        """
        处理单个项目

        Args:
            source_type: 'calibre' 或 'filesystem'
            source_id: 书籍ID或文件路径
            dry_run: 是否为干运行

        Returns:
            是否成功
        """
        try:
            # 获取文件路径和元数据
            if source_type == 'calibre':
                # 从Calibre数据库获取
                book_id, fmt = source_id.split('_')
                book_id = int(book_id)

                cursor = self.calibre_db.cursor()
                cursor.execute('SELECT title, path FROM books WHERE id = ?', (book_id,))
                result = cursor.fetchone()

                if not result:
                    self.logger.error(f"Book {book_id} not found in Calibre DB")
                    self.progress.update_status(source_type, source_id, 'failed', error='Not found')
                    return False

                title, path = result
                source_file = os.path.join(self.config['calibre_library'], path, f"{Path(path).name}.{fmt.lower()}")

                # 获取作者
                cursor.execute('''
                    SELECT a.name
                    FROM authors a
                    JOIN books_authors_link bal ON a.id = bal.author
                    WHERE bal.book = ?
                    LIMIT 1
                ''', (book_id,))
                author_result = cursor.fetchone()
                authors = [author_result[0]] if author_result else []

                # 获取语言
                cursor.execute('''
                    SELECT l.lang_code
                    FROM books_languages_link bl
                    JOIN languages l ON bl.lang_code = l.id
                    WHERE bl.book = ?
                    LIMIT 1
                ''', (book_id,))
                lang_result = cursor.fetchone()
                language = self._normalize_language(lang_result[0] if lang_result else 'unknown')

                metadata = {'title': title, 'authors': authors}

            else:  # filesystem
                source_file = source_id
                file_ext = Path(source_file).suffix.lower().lstrip('.')

                # 从文件提取元数据
                metadata = self.parser.get_metadata(source_file, file_ext)
                language = self._normalize_language(metadata.get('language', 'unknown'))

                if language == 'unknown':
                    # 从文件名推断
                    language = self.parser.detect_language_from_content(Path(source_file).stem)
                    language = self._normalize_language(language)

            # 检查文件是否存在
            if not os.path.exists(source_file):
                self.logger.warning(f"Source file not found: {source_file}")
                self.progress.update_status(source_type, source_id, 'failed', error='File not found')
                return False

            # 创建指纹并检查重复
            fingerprint = BookFingerprint(source_file)
            duplicate_of = self._is_duplicate_of_processed(fingerprint)

            if duplicate_of:
                self.logger.info(f"Duplicate file skipped: {source_file} (duplicate of {duplicate_of})")
                self.progress.update_status(source_type, source_id, 'success',
                                          is_duplicate=True, duplicate_of=duplicate_of)
                return True

            # TXT文件特殊处理
            if Path(source_file).suffix.lower() == '.txt':
                txt_folder = os.path.join(self.config['target_library'], self.config['txt_folder'])
                success = self._copy_file(source_file, txt_folder, Path(source_file).name, dry_run)
            else:
                # 构建目标路径
                target_base = self._build_target_path(metadata, language)
                success = self._copy_file(source_file, target_base, Path(source_file).name, dry_run)

            if success:
                # 添加到指纹缓存
                self.fingerprint_cache[source_file] = fingerprint
                self.progress.update_status(source_type, source_id, 'success', target_path=target_base)
                return True
            else:
                self.progress.update_status(source_type, source_id, 'failed', error='Copy failed')
                return False

        except Exception as e:
            self.logger.error(f"Error processing {source_type}:{source_id}: {e}", exc_info=True)
            self.progress.update_status(source_type, source_id, 'failed', error=str(e))
            return False

    def run(self, dry_run: bool = False, limit: int = None, resume: bool = False):
        """运行整合整理任务"""
        if not resume:
            self.logger.info("Starting merged organization...")
            calibre_count, fs_count = self.scan_all_sources()

        pending_items = self.progress.get_pending_items()
        total_pending = len(pending_items)

        if limit:
            pending_items = pending_items[:limit]

        self.logger.info(f"Processing {len(pending_items)} items (Total pending: {total_pending})...")

        success_count = 0
        failed_count = 0

        for idx, (source_type, source_id) in enumerate(pending_items, 1):
            self.logger.info(f"[{idx}/{len(pending_items)}] Processing {source_type}:{source_id}")

            if self.process_item(source_type, source_id, dry_run):
                success_count += 1
            else:
                failed_count += 1

            # 每50项输出一次进度
            if idx % 50 == 0:
                self.logger.info(f"Progress: {idx}/{len(pending_items)} | Success: {success_count} | Failed: {failed_count}")

        # 最终统计
        stats = self.progress.get_statistics()
        self.logger.info("="*60)
        self.logger.info("Merged organization completed!")
        self.logger.info(f"Total: {stats['total']}")
        self.logger.info(f"Success: {stats['success']}")
        self.logger.info(f"Failed: {stats['failed']}")
        self.logger.info(f"Duplicates: {stats['duplicates']}")
        self.logger.info("="*60)

    def close(self):
        if self.calibre_db:
            self.calibre_db.close()
        if self.progress:
            self.progress.close()


def main():
    parser = argparse.ArgumentParser(description='电子书整理系统 - 整合版（支持去重）')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--dry-run', action='store_true', help='干运行模式')
    parser.add_argument('--limit', type=int, metavar='N', help='限制处理数量')
    parser.add_argument('--resume', action='store_true', help='继续上次任务')

    args = parser.parse_args()

    if not os.path.isabs(args.config):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, args.config)
    else:
        config_path = args.config

    organizer = MergedEbookOrganizer(config_path)

    try:
        organizer.run(
            dry_run=args.dry_run,
            limit=args.limit,
            resume=args.resume
        )
    finally:
        organizer.close()


if __name__ == '__main__':
    main()
