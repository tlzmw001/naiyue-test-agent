"""Collect bounded source regions while copying; prepare feedback before commit."""

from collections import deque
import difflib
import re


class EditFeedback:
    """The copy pass is the sole producer; no additional file reads or permissions."""

    def __init__(self, matches, settings):
        self.matches, self.settings = matches, settings
        self.previous = deque(maxlen=3)
        self.regions = []
        self.line = bytearray()
        self.line_size = self.position = self.line_start = self.source_offset = 0
        self.line_number = 1
        self.following = self.stored = self.match_index = 0
        self.pending_cr = b''
        self.incomplete = False
        self.first_change = self.first_line = None
        self.storage_limit = max(settings.max_patch_bytes, settings.max_diff_bytes, settings.max_preview_bytes)

    def _find_change(self, chunk):
        stop = self.source_offset + len(chunk)
        while self.first_change is None and self.match_index < len(self.matches):
            start, end, replacement = self.matches[self.match_index]
            if start >= stop:
                break
            a, b = max(start, self.source_offset), min(end, stop)
            if a < b:
                old = chunk[a - self.source_offset:b - self.source_offset]
                new = replacement[a - start:b - start]
                if old != new:
                    prefix = next((i for i, (x, y) in enumerate(zip(old, new)) if x != y), min(len(old), len(new)))
                    self.first_change = a + prefix
                elif b == end and len(replacement) > end - start:
                    self.first_change = end
            if end <= stop:
                self.match_index += 1
            else:
                break
        self.source_offset = stop

    def feed(self, chunk):
        self._find_change(chunk)
        data = self.pending_cr + chunk
        self.pending_cr = b'\r' if data.endswith(b'\r') else b''
        if self.pending_cr:
            data = data[:-1]
        self._parse(data)

    def _parse(self, data):
        for part in data.splitlines(keepends=True):
            self.line_size += len(part)
            if self.line is not None:
                if self.line_size > self.settings.max_affected_line_bytes:
                    self.line = None
                else:
                    self.line.extend(part)
            self.position += len(part)
            if part.endswith((b'\n', b'\r')):
                self._finish_line()

    def _store(self, entry, *, required=False):
        number, start, end, raw = entry
        if raw is None or self.stored + len(raw) > self.storage_limit:
            if required:
                self.incomplete = True
            return
        if self.regions and self.regions[-1]['end'] > start:
            return
        if self.regions and self.regions[-1]['end'] == start:
            region = self.regions[-1]
            region['old'].extend(raw)
            region['end'] = end
        else:
            self.regions.append({'line': number, 'start': start, 'end': end, 'old': bytearray(raw)})
        self.stored += len(raw)

    def _finish_line(self):
        entry = (self.line_number, self.line_start, self.position,
                 bytes(self.line) if self.line is not None else None)
        if self.first_line is None and self.first_change is not None and self.first_change < self.position:
            self.first_line = self.line_number
        affected = any(start < self.position and end > self.line_start for start, end, _ in self.matches)
        if affected:
            for previous in self.previous:
                self._store(previous)
            self._store(entry, required=True)
            self.following = 3
        elif self.following:
            self._store(entry)
            self.following -= 1
        self.previous.append(entry)
        self.line_number += 1
        self.line_start = self.position
        self.line_size, self.line = 0, bytearray()

    def finish(self):
        if self.pending_cr:
            self._parse(self.pending_cr)
            self.pending_cr = b''
        if self.line_size:
            self._finish_line()
        if self.first_line is None:
            self.first_line = self.line_number


def _bounded(text, budget):
    encoded = text.encode('utf-8')
    return encoded[:budget].decode('utf-8', errors='ignore'), len(encoded) > budget


def _unified(old, new, old_base, new_base):
    lines = difflib.unified_diff(_lf_lines(old), _lf_lines(new), n=3)
    for line in list(lines)[2:]:
        if line.startswith('@@'):
            match = re.match(r'@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@(.*)', line)
            a, count_a, b, count_b, suffix = match.groups()
            yield f'@@ -{int(a) + old_base}{count_a or ""} +{int(b) + new_base}{count_b or ""} @@{suffix}\n'
        elif line.endswith('\n'):
            yield line
        else:
            yield line + '\n\\ No newline at end of file\n'


def _lf_lines(text):
    # Unicode NEL/LS/PS are ordinary UTF-8 file bytes, not patch line delimiters.
    parts = text.split('\n')
    return [part + '\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def build_edit_feedback(collector, path, used_fuzzy):
    collector.finish()
    settings = collector.settings
    hunks, previews, delta = [], [], 0
    missing = collector.incomplete
    represented = set()
    for region in collector.regions:
        start, end = region['start'], region['end']
        edits = [(i, a, b, replacement) for i, (a, b, replacement) in enumerate(collector.matches)
                 if a < end and b > start]
        if any(a < start or b > end for _, a, b, _ in edits):
            missing = True
            continue
        old = bytes(region['old'])
        new, cursor = bytearray(), start
        for i, a, b, replacement in edits:
            new.extend(old[cursor - start:a - start])
            new.extend(replacement)
            cursor = b
            represented.add(i)
        new.extend(old[cursor - start:])
        old_text, new_text = old.decode('utf-8'), new.decode('utf-8')
        # Unified patches count LF lines. CR-only source regions cannot be advertised as an applicable patch.
        if re.search(r'\r(?!\n)', old_text + new_text):
            missing = True
        base = region['line'] - 1
        hunks.extend(_unified(old_text, new_text, base, base + delta))
        previews.append(f'line {region["line"]}:\n{new_text}')
        delta += len(_lf_lines(new_text)) - len(_lf_lines(old_text))
    missing |= len(represented) != len(collector.matches)
    body = ''.join(hunks)
    # POSIX unified headers terminate filenames at TAB; quoted names are not portable to BSD patch.
    unsupported_path = any(ord(char) < 32 or ord(char) == 127 for char in path)
    header = f'--- a/{path}\t\n+++ b/{path}\t\n'
    patch = header + body
    patch_omitted = missing or unsupported_path or len(patch.encode('utf-8')) > settings.max_patch_bytes
    diff, truncated = _bounded(body, settings.max_diff_bytes)
    preview, preview_truncated = _bounded(''.join(previews), settings.max_preview_bytes)
    artifact = {'firstChangedLine': collector.first_line, 'used_fuzzy': used_fuzzy,
                'diff': diff, 'diff_truncated': truncated or missing,
                'preview': preview, 'preview_truncated': preview_truncated or missing,
                'patch': None if patch_omitted else patch, 'patch_omitted': patch_omitted}
    notices = []
    if artifact['diff_truncated']:
        notices.append('[edit: diff incomplete or truncated by feedback budget]')
    if patch_omitted:
        notices.append('[edit: complete patch omitted because of feedback limits or unrepresentable lines/paths]')
    detail = '\n'.join([f'firstChangedLine={collector.first_line}', diff, *notices])
    return detail, artifact
