"""Incremental terminal text normalization and bounded, line-aware UTF-8 tails."""

import codecs
from collections import deque


class OutputAccumulator:
    def __init__(self, settings):
        self.settings = settings
        self.decoder = codecs.getincrementaldecoder(settings.output_encoding)(errors='replace')
        self.transformed = codecs.lookup(settings.output_encoding).name != 'utf-8'
        self.lossy = False
        self.reasons = set()
        self.observed_bytes = 0
        self._newlines = 0
        self._last_newline = True
        self._segments = deque()
        self.buffer_bytes = 0
        self.partial_line = False
        self._escape = ''
        self._cr = False
        self._finished = False

    @property
    def text(self):
        return b''.join(self._segments).decode('utf-8')

    @property
    def observed_lines(self):
        return self._newlines + int(not self._last_newline)

    @property
    def start_line(self):
        return self.observed_lines - len(self._segments) + 1 if self._segments else 0

    @property
    def truncated(self):
        return bool(self.reasons)

    def feed(self, data):
        return self._decode(data, final=False)

    def finish(self):
        if self._finished:
            return ''
        self._finished = True
        return self._decode(b'', final=True)

    def _decode(self, data, *, final):
        strict = codecs.getincrementaldecoder(self.settings.output_encoding)(errors='strict')
        strict.setstate(self.decoder.getstate())
        try:
            strict.decode(data, final=final)
        except UnicodeError:
            self.lossy = self.transformed = True
        decoded = self.decoder.decode(data, final=final)
        text = self._normalize(decoded, final=final)
        self._append(text)
        return text

    def _normalize(self, decoded, *, final):
        result = []
        for char in decoded:
            if self._cr:
                result.append('\n')
                self._cr = False
                if char == '\n':
                    continue
            if self._escape:
                state = self._escape
                if state == 'esc':
                    self._escape = 'csi' if char == '[' else 'osc' if char == ']' else ''
                elif state == 'csi' and '@' <= char <= '~':
                    self._escape = ''
                elif state == 'osc' and char == '\x07' or state == 'osc_end' and char == '\\':
                    self._escape = ''
                elif state in ('osc', 'osc_end'):
                    self._escape = 'osc_end' if char == '\x1b' else 'osc'
                continue
            if char == '\x1b':
                self._escape = 'esc'
                self.transformed = True
            elif char == '\r':
                self._cr = self.transformed = True
            elif (ord(char) < 32 and char not in ('\n', '\t')) or 127 <= ord(char) < 160:
                result.append(f'\\x{ord(char):02x}')
                self.transformed = True
            else:
                result.append(char)
        if final and self._cr:
            result.append('\n')
            self._cr = False
        return ''.join(result)

    def _append(self, text):
        if not text:
            return
        self.observed_bytes += len(text.encode('utf-8'))
        self._newlines += text.count('\n')
        self._last_newline = text.endswith('\n')
        for piece in text.splitlines(keepends=True):
            data = piece.encode('utf-8')
            if self._segments and not self._segments[-1].endswith(b'\n'):
                self._segments[-1] += data
            else:
                self._segments.append(data)
            self.buffer_bytes += len(data)
        while len(self._segments) > self.settings.max_output_lines:
            self.buffer_bytes -= len(self._segments.popleft())
            self.partial_line = False
            self.reasons.add('lines')
        while self.buffer_bytes > self.settings.max_output_bytes:
            self.reasons.add('bytes')
            if len(self._segments) > 1:
                self.buffer_bytes -= len(self._segments.popleft())
                self.partial_line = False
            else:
                tail = self._segments.pop()[-self.settings.max_output_bytes:]
                tail = tail.decode('utf-8', errors='ignore').encode('utf-8')
                self._segments.append(tail)
                self.buffer_bytes = len(tail)
                self.partial_line = True
