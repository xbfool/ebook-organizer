#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子书元数据解析器
支持从EPUB、MOBI、AZW3文件内部提取元数据
"""

import os
import re
import logging
from typing import Optional, Dict, Any
from pathlib import Path

try:
    from ebooklib import epub
    EPUB_AVAILABLE = True
except ImportError:
    EPUB_AVAILABLE = False
    logging.warning("ebooklib not available, EPUB parsing disabled")

try:
    import mobi
    MOBI_AVAILABLE = True
except ImportError:
    MOBI_AVAILABLE = False
    logging.warning("mobi not available, MOBI/AZW3 parsing disabled")

import chardet


class MetadataParser:
    """电子书元数据解析器"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def parse_epub(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        从EPUB文件解析元数据

        Returns:
            dict: {title, authors, language, publisher, pubdate, series, series_index}
        """
        if not EPUB_AVAILABLE:
            return None

        try:
            book = epub.read_epub(file_path, options={'ignore_ncx': True})

            metadata = {
                'title': self._get_epub_metadata(book, 'title'),
                'authors': self._get_epub_metadata(book, 'creator', multiple=True),
                'language': self._get_epub_metadata(book, 'language'),
                'publisher': self._get_epub_metadata(book, 'publisher'),
                'pubdate': self._get_epub_metadata(book, 'date'),
                'series': None,
                'series_index': None
            }

            # 尝试从calibre metadata获取系列信息
            calibre_series = book.get_metadata('DC', 'calibre:series')
            if calibre_series:
                metadata['series'] = calibre_series[0][0]

            calibre_series_index = book.get_metadata('DC', 'calibre:series_index')
            if calibre_series_index:
                metadata['series_index'] = calibre_series_index[0][0]

            return metadata

        except Exception as e:
            self.logger.error(f"Failed to parse EPUB {file_path}: {e}")
            return None

    def _get_epub_metadata(self, book, key: str, multiple: bool = False):
        """从EPUB获取特定元数据字段"""
        try:
            values = book.get_metadata('DC', key)
            if not values:
                return [] if multiple else None

            if multiple:
                return [v[0] for v in values if v]
            else:
                return values[0][0] if values[0] else None
        except:
            return [] if multiple else None

    def parse_mobi(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        从MOBI/AZW3文件解析元数据

        Returns:
            dict: {title, authors, language, publisher, pubdate}
        """
        if not MOBI_AVAILABLE:
            return None

        try:
            # 使用mobi库解析
            tempdir, filepath = mobi.extract(file_path)

            # mobi库会提取到临时目录，读取opf文件
            opf_files = list(Path(tempdir).glob("*.opf"))
            if not opf_files:
                return None

            # 解析OPF文件
            import xml.etree.ElementTree as ET
            tree = ET.parse(opf_files[0])
            root = tree.getroot()

            # 定义命名空间
            ns = {
                'dc': 'http://purl.org/dc/elements/1.1/',
                'opf': 'http://www.idpf.org/2007/opf'
            }

            metadata = {
                'title': self._get_xml_text(root, './/dc:title', ns),
                'authors': self._get_xml_text_list(root, './/dc:creator', ns),
                'language': self._get_xml_text(root, './/dc:language', ns),
                'publisher': self._get_xml_text(root, './/dc:publisher', ns),
                'pubdate': self._get_xml_text(root, './/dc:date', ns),
                'series': None,
                'series_index': None
            }

            # 清理临时文件
            import shutil
            shutil.rmtree(tempdir, ignore_errors=True)

            return metadata

        except Exception as e:
            self.logger.error(f"Failed to parse MOBI/AZW3 {file_path}: {e}")
            return None

    def _get_xml_text(self, root, xpath: str, namespaces: dict) -> Optional[str]:
        """从XML获取文本"""
        elem = root.find(xpath, namespaces)
        return elem.text if elem is not None and elem.text else None

    def _get_xml_text_list(self, root, xpath: str, namespaces: dict) -> list:
        """从XML获取文本列表"""
        elems = root.findall(xpath, namespaces)
        return [e.text for e in elems if e.text]

    def detect_language_from_content(self, text: str) -> str:
        """
        从文本内容检测语言

        Returns:
            'jpn', 'eng', 'zho', or 'unknown'
        """
        if not text or len(text) < 10:
            return 'unknown'

        # 统计不同字符集的出现次数
        hiragana = len(re.findall(r'[\u3040-\u309F]', text))
        katakana = len(re.findall(r'[\u30A0-\u30FF]', text))
        kanji = len(re.findall(r'[\u4E00-\u9FFF]', text))
        chinese_punctuation = len(re.findall(r'[\u3000-\u303F\uFF00-\uFFEF]', text))
        ascii_alpha = len(re.findall(r'[a-zA-Z]', text))

        total_chars = len(text)

        # 日文检测：平假名或片假名占比较高
        japanese_chars = hiragana + katakana
        if japanese_chars / total_chars > 0.1:
            return 'jpn'

        # 中文检测：汉字多但没有假名
        if kanji / total_chars > 0.3 and japanese_chars == 0:
            return 'zho'

        # 英文检测：ASCII字母占主导
        if ascii_alpha / total_chars > 0.5:
            return 'eng'

        return 'unknown'

    def infer_from_filename(self, filename: str) -> Dict[str, Any]:
        """
        从文件名推断元数据

        常见格式:
        - [作者] 书名
        - 书名 - 作者
        - (系列) 书名
        """
        metadata = {
            'title': None,
            'authors': [],
            'language': 'unknown'
        }

        # 移除扩展名
        name = Path(filename).stem

        # 检测语言
        metadata['language'] = self.detect_language_from_content(name)

        # 尝试提取作者和书名
        # 格式1: [作者] 书名
        match = re.match(r'\[([^\]]+)\]\s*(.+)', name)
        if match:
            metadata['authors'] = [match.group(1).strip()]
            metadata['title'] = match.group(2).strip()
            return metadata

        # 格式2: 书名 - 作者
        if ' - ' in name:
            parts = name.split(' - ', 1)
            metadata['title'] = parts[0].strip()
            metadata['authors'] = [parts[1].strip()]
            return metadata

        # 默认：整个文件名作为标题
        metadata['title'] = name

        return metadata

    def get_metadata(self, file_path: str, format_type: str) -> Dict[str, Any]:
        """
        获取电子书元数据（优先从文件内部，失败则从文件名推断）

        Args:
            file_path: 文件路径
            format_type: 格式类型 (epub, mobi, azw3)

        Returns:
            元数据字典
        """
        metadata = None

        # 尝试从文件内部解析
        if format_type.lower() == 'epub':
            metadata = self.parse_epub(file_path)
        elif format_type.lower() in ('mobi', 'azw3'):
            metadata = self.parse_mobi(file_path)

        # 如果解析失败或关键信息缺失，从文件名推断
        if not metadata or not metadata.get('title'):
            filename_meta = self.infer_from_filename(file_path)
            if not metadata:
                metadata = filename_meta
            else:
                # 补充缺失的信息
                if not metadata.get('title'):
                    metadata['title'] = filename_meta.get('title')
                if not metadata.get('authors'):
                    metadata['authors'] = filename_meta.get('authors', [])
                if not metadata.get('language') or metadata['language'] == 'unknown':
                    metadata['language'] = filename_meta.get('language', 'unknown')

        return metadata


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    parser = MetadataParser()

    # 测试语言检测
    test_texts = [
        ("これは日本語のテストです。", "jpn"),
        ("This is an English test.", "eng"),
        ("这是中文测试。", "zho"),
    ]

    for text, expected in test_texts:
        detected = parser.detect_language_from_content(text)
        print(f"Text: {text[:20]}... -> Detected: {detected} (Expected: {expected})")
