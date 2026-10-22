"""Bounded Unicode normalization with original byte coordinates, never edit distance."""

import hashlib
import os
import re
import unicodedata

from langchain_core.tools import ToolException

from .write import WRITE_CHANGED
from .control import check_tool_stop
from .progress import ToolProgress


EDIT_NOT_FOUND = 'edit oldText was not found. Read the file again and include more exact surrounding text.'
EDIT_NOT_UNIQUE = 'edit oldText is not unique. Include enough surrounding text to identify one location.'
EDIT_OVERLAP = 'edit replacements overlap. Merge the affected blocks into one replacement or choose disjoint text.'
EDIT_NORMALIZATION_LIMIT = ('edit fuzzy matching exceeds the configured normalization or line buffer limit. '
                            'Use exact oldText or split this call into smaller edits; no edits were committed.')
EDIT_STOPPED = 'edit stopped by the user before file commit; no edits were committed to the target file.'
_VARIANTS = str.maketrans({**{chr(n): "'" for n in range(0x2018, 0x201C)},
    **{chr(n): '"' for n in range(0x201C, 0x2020)},
    **{chr(n): '-' for n in (*range(0x2010, 0x2016), 0x2212)},
    **{chr(n): ' ' for n in (0xA0, *range(0x2002, 0x200B), 0x202F, 0x205F, 0x3000)}})


def file_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def require_file_stamp(info, expected, *, same_source=True):
    """Keep full same-source stamps; cross-source times differ on Windows."""
    current = file_stamp(info)
    changed = current != expected if same_source else current[:3] != expected[:3]
    if changed:
        raise ToolException(WRITE_CHANGED)
    return current


def normalize_text(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(unicodedata.normalize('NFKC', line).rstrip().translate(_VARIANTS)
                     for line in text.split('\n'))


def _units(body, offset):
    """A composition cluster/expanded source character is an indivisible unit."""
    unit, start, end = '', offset, offset
    for char in body:
        decomposed = unicodedata.normalize('NFKD', char)
        if unit and not unicodedata.combining(decomposed[0]):
            normalized = unicodedata.normalize('NFKC', unit)
            combined = normalized + unicodedata.normalize('NFKC', char)
            # Some ccc=0 characters compose too (Hangul and Indic vowel signs).
            if unicodedata.normalize('NFKC', combined) == combined:
                yield normalized.translate(_VARIANTS), start, end
                unit, start = '', end
        unit += char
        end += len(char.encode('utf-8'))
    if unit:
        yield unicodedata.normalize('NFKC', unit).translate(_VARIANTS), start, end


def _source_lines(source, limit, chunk_bytes, *, control=None, progress=None):
    pending = b''
    progress = progress or ToolProgress(None)
    processed = 0
    check_tool_stop(control, EDIT_STOPPED)
    while chunk := source.read(min(chunk_bytes, limit + 1)):
        check_tool_stop(control, EDIT_STOPPED)
        processed += len(chunk)
        progress.report(phase='scan_normalized', completed=processed, unit='bytes')
        parts = (pending + chunk).splitlines(keepends=True)
        pending = b''
        for index, part in enumerate(parts):
            check_tool_stop(control, EDIT_STOPPED)
            if len(part) > limit:
                raise ToolException(EDIT_NORMALIZATION_LIMIT)
            if index == len(parts) - 1 and (not part.endswith(b'\n') or part.endswith(b'\r')):
                pending = part
            else:
                yield part
        check_tool_stop(control, EDIT_STOPPED)
    check_tool_stop(control, EDIT_STOPPED)
    if pending:
        yield pending


def scan_normalized_matches(target, old_parts, snapshot, settings, chunk_bytes, *, control=None, progress=None):
    check_tool_stop(control, EDIT_STOPPED)
    progress = progress or ToolProgress(None)
    progress.report(phase='scan_normalized', completed=0, unit='bytes')
    budget = settings.max_normalization_buffer_bytes
    if sum(map(len, old_parts)) > budget:
        raise ToolException(EDIT_NORMALIZATION_LIMIT)
    patterns = [normalize_text(part.decode('utf-8')) for part in old_parts]
    if any(not pattern for pattern in patterns):
        raise ToolException(EDIT_NOT_FOUND)
    # Charge UTF-8 text plus fixed coordinate slots; Python container overhead is bounded too.
    if sum(len(pattern.encode('utf-8')) for pattern in patterns) > budget:
        raise ToolException(EDIT_NORMALIZATION_LIMIT)
    overlap = max(map(len, patterns)) - 1
    locations = [None] * len(patterns)
    tail, spans = '', []
    digest = hashlib.sha256()
    with target.open('rb') as source:
        require_file_stamp(os.fstat(source.fileno()), snapshot['stamp'])
        offset = 0
        for raw in _source_lines(source, min(settings.max_affected_line_bytes, budget), chunk_bytes, control=control, progress=progress):
            check_tool_stop(control, EDIT_STOPPED)
            digest.update(raw)
            beginning = 3 if offset == 0 and raw.startswith(b'\xef\xbb\xbf') else 0
            ending = re.search(b'\r\n$|[\r\n]$', raw)
            body_end = ending.start() if ending else len(raw)
            body = raw[beginning:body_end].decode('utf-8').rstrip()
            text_parts, coordinates = [], []
            for normalized, start, end in _units(body, offset + beginning):
                text_parts.append(normalized)
                coordinates.extend((start, end, i == 0, i == len(normalized) - 1, None)
                                   for i in range(len(normalized)))
                if len(coordinates) * 24 + len(raw) > budget:
                    raise ToolException(EDIT_NORMALIZATION_LIMIT)
            if ending:
                text_parts.append('\n')
                coordinates.append((offset + ending.start(), offset + len(raw), True, True, ending.group()))
            text = ''.join(text_parts)
            window, mapped = tail + text, spans + coordinates
            if len(window.encode('utf-8')) + len(mapped) * 24 + len(raw) > budget:
                raise ToolException(EDIT_NORMALIZATION_LIMIT)
            for index, pattern in enumerate(patterns):
                found = window.find(pattern)
                while found >= 0:
                    end = found + len(pattern)
                    if end > len(tail) and mapped[found][2] and mapped[end - 1][3]:
                        if locations[index] is not None:
                            raise ToolException(EDIT_NOT_UNIQUE)
                        newline = next((span[4] for span in mapped[found:end] if span[4]), None)
                        locations[index] = (mapped[found][0], mapped[end - 1][1], newline)
                    found = window.find(pattern, found + 1)
            tail = window[-overlap:] if overlap else ''
            spans = mapped[-overlap:] if overlap else []
            offset += len(raw)
            check_tool_stop(control, EDIT_STOPPED)
        if offset != snapshot['size'] or digest.digest() != snapshot['digest']:
            raise ToolException(WRITE_CHANGED)
        require_file_stamp(os.fstat(source.fileno()), snapshot['stamp'])
    if any(location is None for location in locations):
        raise ToolException(EDIT_NOT_FOUND)
    check_tool_stop(control, EDIT_STOPPED)
    return locations


def prepare_edit_plan(target, old_parts, new_parts, scan, settings, chunk_bytes, *, control=None, progress=None):
    check_tool_stop(control, EDIT_STOPPED)
    progress = progress or ToolProgress(None)
    progress.report(phase='match_plan')
    locations, digest, size, stamp, mode, newline = scan
    snapshot = {'digest': digest, 'size': size, 'stamp': stamp, 'mode': mode}
    fuzzy = any(start is None for start in locations)
    if fuzzy:
        ranges = scan_normalized_matches(target, old_parts, snapshot, settings, chunk_bytes, control=control, progress=progress)
    else:
        ranges = []
        for start, old in zip(locations, old_parts):
            ending = re.search(b'\r\n|[\r\n]', old)
            ranges.append((start, start + len(old), ending.group() if ending else None))
    matches = []
    for (start, end, local_newline), replacement in zip(ranges, new_parts):
        replacement = replacement.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        replacement = replacement.replace(b'\n', local_newline or newline or b'\n')
        matches.append((start, end, replacement))
    matches.sort()
    if any(a[1] > b[0] for a, b in zip(matches, matches[1:])):
        raise ToolException(EDIT_OVERLAP)
    check_tool_stop(control, EDIT_STOPPED)
    return {'matches': matches, 'snapshot': snapshot, 'used_fuzzy': fuzzy}
