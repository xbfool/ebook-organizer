#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书整理系统 - 主脚本
支持断点续传、元数据提取、智能分类
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

# 设置标准输出为UTF-8编码
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'ignore')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'ignore')

from metadata_parser import MetadataParser


class ProgressTracker:
    """进度跟踪器 - 使用SQLite实现断点续传"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._init_db()

    def _init_db(self):
        """初始化进度数据库"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS book_progress (
                book_id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                title TEXT,
                status TEXT DEFAULT 'pending',
                target_path TEXT,
                error_message TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_status ON book_progress(status)
        ''')

        self.conn.commit()

    def add_book(self, book_id: int, path: str, title: str = None):
        """添加书籍到进度跟踪"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO book_progress (book_id, path, title)
                VALUES (?, ?, ?)
            ''', (book_id, path, title))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def update_status(self, book_id: int, status: str, target_path: str = None, error: str = None):
        """更新书籍处理状态"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE book_progress
            SET status = ?, target_path = ?, error_message = ?, processed_at = CURRENT_TIMESTAMP
            WHERE book_id = ?
        ''', (status, target_path, error, book_id))
        self.conn.commit()

    def get_status(self, book_id: int) -> str:
        """获取书籍状态"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT status FROM book_progress WHERE book_id = ?', (book_id,))
        result = cursor.fetchone()
        return result[0] if result else 'pending'

    def get_pending_books(self) -> List[int]:
        """获取待处理的书籍ID列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT book_id FROM book_progress WHERE status = "pending"')
        return [row[0] for row in cursor.fetchall()]

    def get_failed_books(self) -> List[Tuple[int, str, str]]:
        """获取失败的书籍列表"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT book_id, path, error_message
            FROM book_progress
            WHERE status = "failed"
        ''')
        return cursor.fetchall()

    def get_statistics(self) -> Dict[str, int]:
        """获取处理统计"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT status, COUNT(*)
            FROM book_progress
            GROUP BY status
        ''')
        stats = dict(cursor.fetchall())
        return {
            'total': sum(stats.values()),
            'success': stats.get('success', 0),
            'failed': stats.get('failed', 0),
            'pending': stats.get('pending', 0),
            'skipped': stats.get('skipped', 0)
        }

    def reset_failed(self):
        """重置失败的任务为待处理"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE book_progress SET status = "pending" WHERE status = "failed"')
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


class EbookOrganizer:
    """电子书整理器"""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.logger = logging.getLogger(__name__)

        self.metadata_db = sqlite3.connect(self.config['metadata_db'])
        self.progress = ProgressTracker(self.config['progress_db'])
        self.parser = MetadataParser()

        # 缓存作者的最早日期
        self.author_earliest_date = {}

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _setup_logging(self):
        """设置日志"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.config['log_file'], encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def _get_author_earliest_date(self, author_id: int) -> str:
        """
        获取作者最早作品的发行日期

        Returns:
            格式化的日期字符串 "[YYYY-MM]" 或 "[未知]"
        """
        if author_id in self.author_earliest_date:
            return self.author_earliest_date[author_id]

        cursor = self.metadata_db.cursor()

        # 查询该作者所有书籍，找出最早的日期
        cursor.execute('''
            SELECT b.pubdate
            FROM books b
            JOIN books_authors_link bal ON b.id = bal.book
            WHERE bal.author = ? AND b.pubdate IS NOT NULL
            ORDER BY b.pubdate ASC
            LIMIT 1
        ''', (author_id,))

        result = cursor.fetchone()

        if result and result[0]:
            try:
                # 解析日期 (格式: "YYYY-MM-DD HH:MM:SS+TZ")
                date_str = result[0].split()[0]  # 取日期部分
                year_month = '-'.join(date_str.split('-')[:2])  # 取年-月
                formatted = f"[{year_month}]"
            except:
                formatted = "[未知]"
        else:
            formatted = "[未知]"

        self.author_earliest_date[author_id] = formatted
        return formatted

    def _sanitize_filename(self, name: str) -> str:
        """清理文件名中的非法字符"""
        # Windows不允许的字符
        illegal_chars = r'<>:"/\|?*'
        for char in illegal_chars:
            name = name.replace(char, '_')

        # 移除首尾空格和点
        name = name.strip('. ')

        # 限制长度
        if len(name) > 200:
            name = name[:200]

        return name

    def _normalize_language(self, lang_code: str) -> str:
        """
        标准化语言代码，只返回 eng/jpn/zho/unknown

        Args:
            lang_code: 原始语言代码

        Returns:
            标准化后的语言代码
        """
        if not lang_code or lang_code == 'unknown':
            return 'unknown'

        lang_code = lang_code.lower()

        # 英文相关
        if lang_code in ('eng', 'en', 'english'):
            return 'eng'
        # 日文相关
        if lang_code in ('jpn', 'ja', 'japanese'):
            return 'jpn'
        # 中文相关
        if lang_code in ('zho', 'zh', 'chi', 'chinese', 'cmn', 'zh-cn', 'zh-tw'):
            return 'zho'

        # 其他欧洲语言默认为英文
        if lang_code in ('deu', 'de', 'fra', 'fr', 'spa', 'es', 'ita', 'it', 'por', 'pt', 'rus', 'ru'):
            self.logger.warning(f"Language {lang_code} mapped to 'eng'")
            return 'eng'

        self.logger.warning(f"Unknown language code: {lang_code}, setting to unknown")
        return 'unknown'

    def _get_language_code(self, book_id: int, title: str = None) -> str:
        """
        获取书籍的语言代码，并进行标准化

        Args:
            book_id: 书籍ID
            title: 书名（用于未知语言时的推断）

        Returns:
            标准化的语言代码: eng/jpn/zho/unknown
        """
        cursor = self.metadata_db.cursor()
        cursor.execute('''
            SELECT l.lang_code
            FROM books_languages_link bl
            JOIN languages l ON bl.lang_code = l.id
            WHERE bl.book = ?
            LIMIT 1
        ''', (book_id,))

        result = cursor.fetchone()
        lang_code = result[0] if (result and result[0]) else 'unknown'

        # 标准化语言代码
        normalized = self._normalize_language(lang_code)

        # 如果还是unknown，尝试从书名推断
        if normalized == 'unknown' and title:
            detected = self.parser.detect_language_from_content(title)
            if detected != 'unknown':
                self.logger.info(f"Language detected from title '{title}': {detected}")
                normalized = detected

        return normalized

    def _classify_japanese_book(self, book_id: int, tags: List[str], publisher: str) -> str:
        """
        分类日文书籍

        Returns:
            分类名称: light_novel, literature, mystery, scifi_fantasy, other
        """
        tags_lower = [t.lower() for t in tags]
        publisher_lower = publisher.lower() if publisher else ''

        # 检查是否为轻小说
        for keyword in self.config['light_novel_keywords']:
            if keyword.lower() in publisher_lower or any(keyword.lower() in t for t in tags_lower):
                return 'light_novel'

        # 检查其他分类
        if any(tag in tags_lower for tag in ['mystery', 'detective', 'ミステリー', '推理']):
            return 'mystery'

        if any(tag in tags_lower for tag in ['science fiction', 'fantasy', 'sf', 'ファンタジー']):
            return 'scifi_fantasy'

        if any(tag in tags_lower for tag in ['literary', '文芸', '純文学', '文学']):
            return 'literature'

        return 'other'

    def _classify_english_book(self, tags: List[str]) -> Tuple[str, str]:
        """
        分类英文书籍

        Returns:
            (主分类, 子分类)  例如: ('fiction', 'mystery')
        """
        tags_lower = [t.lower() for t in tags]

        # 检查是否为经典文学
        if 'classics' in tags_lower or 'classic' in tags_lower:
            return 'classics', ''

        # 检查小说类型
        for subcat, keywords in self.config['fiction_tags'].items():
            if any(k.lower() in t for k in keywords for t in tags_lower):
                return 'fiction', subcat

        # 检查是否为小说
        if any(tag in tags_lower for tag in ['fiction', 'novel']):
            return 'fiction', 'general'

        # 默认为非小说
        return 'non_fiction', ''

    def _get_book_metadata(self, book_id: int) -> Dict:
        """从Calibre数据库获取书籍元数据"""
        cursor = self.metadata_db.cursor()

        # 获取基本信息
        cursor.execute('''
            SELECT title, path, pubdate
            FROM books
            WHERE id = ?
        ''', (book_id,))

        result = cursor.fetchone()
        if not result:
            return None

        title, path, pubdate = result

        # 获取作者
        cursor.execute('''
            SELECT a.id, a.name
            FROM authors a
            JOIN books_authors_link bal ON a.id = bal.author
            WHERE bal.book = ?
            ORDER BY a.name
        ''', (book_id,))
        authors = cursor.fetchall()

        # 获取系列
        cursor.execute('''
            SELECT s.name, b.series_index
            FROM series s
            JOIN books_series_link bs ON s.id = bs.series
            JOIN books b ON bs.book = b.id
            WHERE bs.book = ?
        ''', (book_id,))
        series_result = cursor.fetchone()
        series_name = series_result[0] if series_result else None
        series_index = series_result[1] if series_result else None

        # 获取标签
        cursor.execute('''
            SELECT t.name
            FROM tags t
            JOIN books_tags_link btl ON t.id = btl.tag
            WHERE btl.book = ?
        ''', (book_id,))
        tags = [row[0] for row in cursor.fetchall()]

        # 获取出版社
        cursor.execute('''
            SELECT p.name
            FROM publishers p
            JOIN books_publishers_link bpl ON p.id = bpl.publisher
            WHERE bpl.book = ?
        ''', (book_id,))
        publisher_result = cursor.fetchone()
        publisher = publisher_result[0] if publisher_result else None

        # 获取文件格式
        cursor.execute('''
            SELECT format, name
            FROM data
            WHERE book = ?
        ''', (book_id,))
        formats = cursor.fetchall()

        return {
            'book_id': book_id,
            'title': title,
            'path': path,
            'pubdate': pubdate,
            'authors': authors,  # [(id, name), ...]
            'series': series_name,
            'series_index': series_index,
            'tags': tags,
            'publisher': publisher,
            'formats': formats  # [(format, filename), ...]
        }

    def _build_target_path(self, metadata: Dict, language: str) -> str:
        """
        构建目标路径

        格式: 语言/类型/[YYYY-MM] 作者/系列或书名/文件
        """
        lang_folder = self.config['language_names'].get(language, self.config['language_names']['unknown'])

        # 获取主要作者及其日期前缀
        if metadata['authors']:
            author_id, author_name = metadata['authors'][0]
            date_prefix = self._get_author_earliest_date(author_id)
            author_folder = f"{date_prefix} {self._sanitize_filename(author_name)}"
        else:
            author_folder = "[未知] Unknown"

        # 根据语言分类
        if language == 'jpn':
            category = self._classify_japanese_book(
                metadata['book_id'],
                metadata['tags'],
                metadata['publisher']
            )
            category_folder = self.config['japanese_categories'][category]

            # 轻小说进一步分为有系列和单行本
            if category == 'light_novel':
                if metadata['series']:
                    subcategory = "【有系列】"
                    book_folder = self._sanitize_filename(metadata['series'])
                else:
                    subcategory = "【单行本】"
                    book_folder = self._sanitize_filename(metadata['title'])

                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category_folder,
                    subcategory,
                    author_folder,
                    book_folder
                )
            else:
                # 其他类型：语言/类型/作者/系列或书名
                if metadata['series']:
                    book_folder = self._sanitize_filename(metadata['series'])
                else:
                    book_folder = self._sanitize_filename(metadata['title'])

                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category_folder,
                    author_folder,
                    book_folder
                )

        elif language == 'eng':
            main_cat, sub_cat = self._classify_english_book(metadata['tags'])
            category_folder = self.config['english_categories'][main_cat]

            # 构建路径
            if sub_cat:
                sub_folder = sub_cat.title()
                if metadata['series']:
                    book_folder = self._sanitize_filename(metadata['series'])
                else:
                    book_folder = self._sanitize_filename(metadata['title'])

                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category_folder,
                    sub_folder,
                    author_folder,
                    book_folder
                )
            else:
                if metadata['series']:
                    book_folder = self._sanitize_filename(metadata['series'])
                else:
                    book_folder = self._sanitize_filename(metadata['title'])

                base_path = os.path.join(
                    self.config['target_library'],
                    lang_folder,
                    category_folder,
                    author_folder,
                    book_folder
                )

        else:
            # 其他语言：简单分类
            lang_folder = self.config['language_names'].get(language, '其他语言')

            if metadata['series']:
                book_folder = self._sanitize_filename(metadata['series'])
            else:
                book_folder = self._sanitize_filename(metadata['title'])

            base_path = os.path.join(
                self.config['target_library'],
                lang_folder,
                author_folder,
                book_folder
            )

        # 检查路径长度
        if len(base_path) > self.config['max_path_length']:
            self.logger.warning(f"Path too long, truncating: {base_path}")
            # 截断书名部分
            book_folder = book_folder[:50]
            base_path = os.path.dirname(base_path)
            base_path = os.path.join(base_path, book_folder)

        return base_path

    def _copy_book_files(self, metadata: Dict, target_base: str, dry_run: bool = False) -> bool:
        """
        复制书籍文件到目标目录

        Args:
            metadata: 书籍元数据
            target_base: 目标基础路径
            dry_run: 是否为干运行模式

        Returns:
            是否成功
        """
        try:
            if not dry_run:
                os.makedirs(target_base, exist_ok=True)

            source_base = os.path.join(self.config['source_library'], metadata['path'])

            for format_type, filename in metadata['formats']:
                source_file = os.path.join(source_base, f"{filename}.{format_type.lower()}")

                if not os.path.exists(source_file):
                    self.logger.warning(f"Source file not found: {source_file}")
                    continue

                # TXT文件单独放到TXT文件夹
                if format_type.lower() == 'txt':
                    txt_folder = os.path.join(self.config['target_library'], self.config['txt_folder'])
                    if not dry_run:
                        os.makedirs(txt_folder, exist_ok=True)
                    target_filename = f"{self._sanitize_filename(metadata['title'])}.txt"
                    target_file = os.path.join(txt_folder, target_filename)
                else:
                    # 构建目标文件名
                    if metadata['series'] and metadata['series_index']:
                        # 有系列：添加序号
                        target_filename = f"{int(float(metadata['series_index'])):02d} {self._sanitize_filename(metadata['title'])}.{format_type.lower()}"
                    else:
                        target_filename = f"{self._sanitize_filename(metadata['title'])}.{format_type.lower()}"

                    target_file = os.path.join(target_base, target_filename)

                if dry_run:
                    self.logger.info(f"[DRY RUN] Would copy: {source_file} -> {target_file}")
                else:
                    shutil.copy2(source_file, target_file)
                    self.logger.info(f"Copied: {target_file}")

            return True

        except Exception as e:
            self.logger.error(f"Failed to copy files for book {metadata['book_id']}: {e}")
            return False

    def process_book(self, book_id: int, dry_run: bool = False) -> bool:
        """
        处理单本书籍

        Returns:
            是否成功
        """
        try:
            # 获取元数据
            metadata = self._get_book_metadata(book_id)
            if not metadata:
                self.logger.error(f"Failed to get metadata for book {book_id}")
                self.progress.update_status(book_id, 'failed', error='Metadata not found')
                return False

            # 获取语言
            language = self._get_language_code(book_id, metadata['title'])

            # 构建目标路径
            target_path = self._build_target_path(metadata, language)

            # 复制文件
            success = self._copy_book_files(metadata, target_path, dry_run)

            if success:
                self.progress.update_status(book_id, 'success', target_path=target_path)
                return True
            else:
                self.progress.update_status(book_id, 'failed', error='Failed to copy files')
                return False

        except Exception as e:
            self.logger.error(f"Error processing book {book_id}: {e}", exc_info=True)
            self.progress.update_status(book_id, 'failed', error=str(e))
            return False

    def initialize_progress(self):
        """初始化进度跟踪 - 扫描所有书籍"""
        self.logger.info("Initializing progress tracker...")

        cursor = self.metadata_db.cursor()
        cursor.execute('SELECT id, title FROM books')

        count = 0
        for book_id, title in cursor.fetchall():
            self.progress.add_book(book_id, '', title)
            count += 1

        self.logger.info(f"Added {count} books to progress tracker")

    def run(self, dry_run: bool = False, limit: int = None, resume: bool = False, retry_failed: bool = False):
        """
        运行整理任务

        Args:
            dry_run: 是否为干运行（只预览不执行）
            limit: 限制处理数量（用于预览）
            resume: 是否继续上次任务
            retry_failed: 是否重试失败的任务
        """
        if retry_failed:
            self.logger.info("Retrying failed books...")
            self.progress.reset_failed()

        if not resume:
            self.logger.info("Starting new organization task...")
            self.initialize_progress()

        # 获取待处理书籍
        pending_books = self.progress.get_pending_books()
        total = len(pending_books)

        if limit:
            pending_books = pending_books[:limit]

        self.logger.info(f"Processing {len(pending_books)} books (Total pending: {total})")

        success_count = 0
        failed_count = 0

        for idx, book_id in enumerate(pending_books, 1):
            self.logger.info(f"Processing [{idx}/{len(pending_books)}] Book ID: {book_id}")

            if self.process_book(book_id, dry_run):
                success_count += 1
            else:
                failed_count += 1

            # 每100本输出一次统计
            if idx % 100 == 0:
                self.logger.info(f"Progress: {idx}/{len(pending_books)} | Success: {success_count} | Failed: {failed_count}")

        # 最终统计
        stats = self.progress.get_statistics()
        self.logger.info("="*60)
        self.logger.info("Organization completed!")
        self.logger.info(f"Total books: {stats['total']}")
        self.logger.info(f"Success: {stats['success']}")
        self.logger.info(f"Failed: {stats['failed']}")
        self.logger.info(f"Pending: {stats['pending']}")
        self.logger.info(f"Skipped: {stats['skipped']}")
        self.logger.info("="*60)

        # 输出失败列表
        if stats['failed'] > 0:
            self.logger.info("Failed books:")
            failed_books = self.progress.get_failed_books()
            with open(self.config['error_file'], 'w', encoding='utf-8') as f:
                for book_id, path, error in failed_books:
                    msg = f"Book ID: {book_id} | Path: {path} | Error: {error}"
                    self.logger.error(msg)
                    f.write(msg + '\n')

    def generate_preview_report(self, limit: int = 100):
        """生成预览报告"""
        self.logger.info(f"Generating preview report for first {limit} books...")

        cursor = self.metadata_db.cursor()
        cursor.execute(f'SELECT id FROM books LIMIT {limit}')
        book_ids = [row[0] for row in cursor.fetchall()]

        report = []
        report.append("=" * 80)
        report.append(f"电子书整理预览报告 - 前 {limit} 本")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 80)
        report.append("")

        for book_id in book_ids:
            metadata = self._get_book_metadata(book_id)
            if not metadata:
                continue

            language = self._get_language_code(book_id, metadata['title'])
            target_path = self._build_target_path(metadata, language)

            report.append(f"书名: {metadata['title']}")
            report.append(f"作者: {', '.join([a[1] for a in metadata['authors']])}")
            report.append(f"语言: {language}")
            if metadata['series']:
                report.append(f"系列: {metadata['series']} (#{metadata['series_index']})")
            report.append(f"目标路径: {target_path}")
            report.append("-" * 80)

        report_text = '\n'.join(report)

        # 保存到文件
        report_file = os.path.join(os.path.dirname(self.config['progress_db']), 'preview_report.txt')
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_text)

        self.logger.info(f"Preview report saved to: {report_file}")
        print(report_text)

    def close(self):
        """关闭数据库连接"""
        if self.metadata_db:
            self.metadata_db.close()
        if self.progress:
            self.progress.close()


def main():
    parser = argparse.ArgumentParser(description='电子书整理系统')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--dry-run', action='store_true', help='干运行模式（只预览不执行）')
    parser.add_argument('--preview', type=int, metavar='N', help='生成前N本书的预览报告')
    parser.add_argument('--limit', type=int, metavar='N', help='限制处理数量')
    parser.add_argument('--resume', action='store_true', help='继续上次任务')
    parser.add_argument('--retry-failed', action='store_true', help='重试失败的任务')

    args = parser.parse_args()

    # 获取配置文件的绝对路径
    if not os.path.isabs(args.config):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, args.config)
    else:
        config_path = args.config

    organizer = EbookOrganizer(config_path)

    try:
        if args.preview:
            organizer.generate_preview_report(args.preview)
        else:
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
