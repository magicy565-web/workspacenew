"""Bounded document text extraction in a separate process; no OCR or active content."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile

MAX_TEXT = 900 * 1024
_job = None


def memory_limit():
    global _job
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_FSIZE, (6 * MAX_TEXT + 4096, 6 * MAX_TEXT + 4096))
        return
    import ctypes
    from ctypes import wintypes
    class IO(ctypes.Structure):
        _fields_ = [(field, ctypes.c_ulonglong) for field in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class Limits(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _job = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x100
    limits.process_memory = 512 * 1024 * 1024
    if not _job or not kernel.SetInformationJobObject(_job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(_job, kernel.GetCurrentProcess()):
        raise ValueError('无法建立文档提取资源限制')


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise ValueError('文档包含不支持的 XML 声明')


def _document_xml(archive):
    entries = archive.infolist()
    if len(entries) > 2000 or sum(item.file_size for item in entries) > 32 * 1024 * 1024:
        raise ValueError('DOCX 解压体积超过限制')
    seen = set()
    for item in entries:
        name = item.filename.rstrip('/')
        path = PurePosixPath(name)
        mode = stat.S_IFMT(item.external_attr >> 16)
        if item.orig_filename != item.filename or not name or '\\' in name or ':' in name or name.startswith('/') or '..' in path.parts or '.' in name.split('/') or any(ord(char) < 32 for char in name):
            raise ValueError('DOCX 包含不安全路径')
        if name.casefold() in seen or mode not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError('DOCX 包含重复、链接、加密或不支持的条目')
        if item.file_size > max(1, item.compress_size) * 200:
            raise ValueError('DOCX 压缩比例超出限制')
        seen.add(name.casefold())
    item = archive.getinfo('word/document.xml')
    if item.file_size > 8 * 1024 * 1024:
        raise ValueError('DOCX 正文超过提取限制')
    xml = archive.read(item)
    root = ET.fromstring(xml, parser=ET.XMLParser(target=_NoDoctype()))
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    if root.tag != ns + 'document':
        raise ValueError('DOCX 正文根元素无效')
    paragraphs = []
    for paragraph in root.iter(ns + 'p'):
        parts = []
        for node in paragraph.iter():
            if node.tag == ns + 't':
                parts.append(node.text or '')
            elif node.tag == ns + 'tab':
                parts.append('\t')
            elif node.tag in (ns + 'br', ns + 'cr'):
                parts.append('\n')
        paragraphs.append(''.join(parts))
    return '\n'.join(paragraphs)


def extract(path, extension):
    warning = ''
    if extension == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            return {'text': '', 'status': 'encrypted', 'warning': '文件已加密，未提取文字'}
        pages = len(reader.pages)
        parts = []
        for index, page in enumerate(reader.pages[:100]):
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 8 * 1024 * 1024:
                raise ValueError('PDF 页面内容超过提取限制')
            parts.append(f'\n[第 {index + 1} 页]\n' + (page.extract_text() or ''))
            if sum(len(part.encode('utf-8')) for part in parts) > MAX_TEXT:
                break
        text = '\n'.join(parts)
        if not any(part.split(']\n', 1)[-1].strip() for part in parts):
            return {'text': '', 'status': 'no_text', 'warning': ('前 100 页未发现可提取文字，其余页面未处理；扫描件需要 OCR' if pages > 100 else '未发现可提取文字；扫描件需要 OCR')}
        if pages > 100:
            warning = '仅提取前 100 页，原文件完整保留'
    elif extension == '.docx':
        with zipfile.ZipFile(path) as archive:
            text = _document_xml(archive)
            if not text.strip():
                return {'text': '', 'status': 'no_text', 'warning': '文件中没有可提取文字'}
    else:
        text = Path(path).read_text(encoding='utf-8-sig')
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_TEXT:
        text = encoded[:MAX_TEXT].decode('utf-8', errors='ignore')
        warning = '提取文字达到大小上限，原文件完整保留'
    return {'text': text, 'status': 'partial' if warning else 'extracted', 'warning': warning}


if __name__ == '__main__':
    try:
        memory_limit()
        outcome = extract(sys.argv[1], sys.argv[2])
    except Exception:
        outcome = {'text': '', 'status': 'failed', 'warning': '文字提取失败或文件超出处理限制；原文件仍可下载'}
    print(json.dumps(outcome, ensure_ascii=False))
