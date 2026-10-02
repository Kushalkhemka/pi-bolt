"""Small VT screen model for the controls emitted by Pi's synchronized TUI.

This tracks visible cells rather than searching terminal scrollback. Unsupported
cursor/screen operations fail explicitly, so a new TUI cannot silently weaken the
benchmark endpoint. It is not a general purpose terminal emulator.
"""
import codecs
from pathlib import Path
import re
import unicodedata

TEXT = re.compile(r'[^\x00-\x1f\x7f]+')
CSI = re.compile(r'\x1b\[([0-?]*[ -/]*)([@-~])')
OSC_END = re.compile(r'\x07|\x1b\\')


class Screen:
    def __init__(self, rows=30, columns=100):
        self.rows, self.columns = rows, columns
        self.cells = [[' '] * columns for _ in range(rows)]
        self.row = self.column = 0
        self.saved = (0, 0)
        self.wrap = True
        self.pending_wrap = self.joined = False
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.buffer = ''
        self.frames = 0
        self.completed = []
        self.alternate = False
        self.alternate_entries = 0
        self.primary = None

    def resize(self, rows, columns):
        cells = [line[:columns] + [' '] * max(0, columns - len(line)) for line in self.cells[:rows]]
        cells.extend([[' '] * columns for _ in range(max(0, rows - len(cells)))])
        self.rows, self.columns, self.cells = rows, columns, cells
        self.row, self.column = min(self.row, rows - 1), min(self.column, columns - 1)
        self.completed = []
        self.pending_wrap = False
        if self.primary is not None:
            prior, row, column, saved = self.primary
            prior = [line[:columns] + [' '] * max(0, columns - len(line)) for line in prior[:rows]]
            prior.extend([[' '] * columns for _ in range(max(0, rows - len(prior)))])
            self.primary = prior, min(row, rows - 1), min(column, columns - 1), saved

    def newline(self):
        self.row += 1
        if self.row >= self.rows:
            self.cells.pop(0)
            self.cells.append([' '] * self.columns)
            self.row = self.rows - 1
        self.pending_wrap = False

    def write(self, character):
        if character == '\r':
            self.column = 0; self.pending_wrap = False
            return
        if character == '\n':
            self.newline()
            return
        if character == '\b':
            self.column = max(0, self.column - 1); self.pending_wrap = False
            return
        if character == '\t':
            self.column = min(self.columns - 1, (self.column // 8 + 1) * 8)
            return
        if ord(character) < 32 or ord(character) == 127:
            return
        if character == '\u200d':
            self.joined = True
            return
        if unicodedata.combining(character) or character in ('\ufe0e', '\ufe0f') or 0x1f3fb <= ord(character) <= 0x1f3ff:
            return
        if self.joined:
            self.joined = False
            return
        width = 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
        if self.wrap and (self.pending_wrap or self.column + width > self.columns):
            self.column = 0; self.newline()
        self.cells[self.row][self.column] = character
        if width == 2 and self.column + 1 < self.columns:
            self.cells[self.row][self.column + 1] = ''
        self.column += width
        if self.column >= self.columns:
            self.column = self.columns - 1
            self.pending_wrap = True

    def text(self, value):
        if not (value.isascii() or value.count('─') == len(value)) or self.joined:
            for character in value:
                self.write(character)
            return
        # Most emitted terminal cells are ASCII. Copy whole spans, keeping the
        # controller's parsing cost outside Pi's critical frame path as small as
        # possible rather than dispatching one Python call per character.
        offset = 0
        while offset < len(value):
            if self.wrap and self.pending_wrap:
                self.column = 0; self.newline()
            available = self.columns - self.column
            take = min(available, len(value) - offset)
            self.cells[self.row][self.column:self.column + take] = list(value[offset:offset + take])
            self.column += take; offset += take
            if self.column >= self.columns:
                self.column = self.columns - 1; self.pending_wrap = True

    def csi(self, parameters, final):
        if final == 'u':
            return  # Kitty keyboard query/mode protocol, including CSI ? u.
        if parameters.startswith('?'):
            for mode in parameters[1:].split(';'):
                if mode == '1049' and final in ('h', 'l'):
                    if final == 'h' and not self.alternate:
                        self.primary = self.cells, self.row, self.column, self.saved
                        self.cells = [[' '] * self.columns for _ in range(self.rows)]
                        self.row = self.column = 0
                        self.alternate = True
                        self.alternate_entries += 1
                    elif final == 'l' and self.alternate:
                        self.cells, self.row, self.column, self.saved = self.primary
                        self.primary = None
                        self.alternate = False
                    self.completed = []
                    self.pending_wrap = False
                elif mode == '2026' and final == 'l':
                    self.frames += 1
                    self.completed = [''.join(line).rstrip() for line in self.cells]
                elif mode == '7' and final in ('h', 'l'):
                    self.wrap = final == 'h'
                elif mode in ('1', '25', '1000', '1002', '1003', '1004', '1006', '2004', '2026', '2031') and final in ('h', 'l'):
                    pass  # Input/cursor/reporting modes do not alter visible cells.
                elif final == 'n':
                    pass  # Terminal status query.
                else:
                    raise ValueError(f'Unsupported private terminal control: {parameters}{final}')
            return
        if final in ('m', 'n', 'c', 't', 'u', 'q'):
            return  # Styling, queries and keyboard/cursor appearance protocols.
        parts = [int(x) if x else 0 for x in parameters.split(';')]
        n = parts[0] or 1
        if final in ('H', 'f'):
            self.row = min(self.rows - 1, max(0, n - 1))
            self.column = min(self.columns - 1, max(0, (parts[1] or 1) - 1)) if len(parts) > 1 else 0
        elif final == 'A': self.row = max(0, self.row - n)
        elif final in ('B', 'e'): self.row = min(self.rows - 1, self.row + n)
        elif final in ('C', 'a'): self.column = min(self.columns - 1, self.column + n)
        elif final == 'D': self.column = max(0, self.column - n)
        elif final == 'E': self.row = min(self.rows - 1, self.row + n); self.column = 0
        elif final == 'F': self.row = max(0, self.row - n); self.column = 0
        elif final in ('G', '`'): self.column = min(self.columns - 1, max(0, n - 1))
        elif final == 'd': self.row = min(self.rows - 1, max(0, n - 1))
        elif final == 'K':
            mode = parts[0]
            start, stop = (0, self.columns) if mode == 2 else ((0, self.column + 1) if mode == 1 else (self.column, self.columns))
            self.cells[self.row][start:stop] = [' '] * (stop - start)
        elif final == 'J':
            mode = parts[0]
            if mode in (2, 3): self.cells = [[' '] * self.columns for _ in range(self.rows)]
            elif mode == 0:
                self.cells[self.row][self.column:] = [' '] * (self.columns - self.column)
                for row in range(self.row + 1, self.rows): self.cells[row] = [' '] * self.columns
            elif mode == 1:
                for row in range(self.row): self.cells[row] = [' '] * self.columns
                self.cells[self.row][:self.column + 1] = [' '] * (self.column + 1)
            else: raise ValueError(f'Unsupported erase mode: {mode}')
        elif final == 'S':
            for _ in range(min(n, self.rows)): self.cells.pop(0); self.cells.append([' '] * self.columns)
        elif final == 'T':
            for _ in range(min(n, self.rows)): self.cells.pop(); self.cells.insert(0, [' '] * self.columns)
        elif final == 's': self.saved = self.row, self.column
        else: raise ValueError(f'Unsupported terminal control: {parameters}{final}')
        self.pending_wrap = False

    def feed(self, data):
        self.buffer += self.decoder.decode(data)
        cursor = 0
        while cursor < len(self.buffer):
            if self.buffer[cursor] != '\x1b':
                match = TEXT.match(self.buffer, cursor)
                if match:
                    self.text(match.group())
                    cursor = match.end()
                    continue
                character = self.buffer[cursor]
                cursor += 1
                self.write(character)
                continue
            if len(self.buffer) - cursor < 2:
                break
            kind = self.buffer[cursor + 1]
            if kind == '[':
                match = CSI.match(self.buffer, cursor)
                if not match:
                    break
                self.csi(match.group(1), match.group(2))
                cursor = match.end()
            elif kind in (']', 'P', '_', '^'):
                match = OSC_END.search(self.buffer, cursor + 2)
                if not match:
                    break
                cursor = match.end()
            elif kind in ('7', '8', 'D', 'E', 'M'):
                if kind == '7': self.saved = self.row, self.column
                elif kind == '8': self.row, self.column = self.saved
                elif kind == 'D': self.newline()
                elif kind == 'E': self.column = 0; self.newline()
                elif self.row: self.row -= 1
                else: self.cells.pop(); self.cells.insert(0, [' '] * self.columns)
                cursor += 2
            else:
                raise ValueError(f'Unsupported escape sequence: {self.buffer[cursor:cursor + 20]!r}')
        self.buffer = self.buffer[cursor:]

    def idle_editor(self, work):
        # Source-backed layout for this isolated default editor: a plain full-width
        # top border, blank submitted input, plain bottom border, cwd and model
        # footer. WorkingStatusIndicator changes the TOP border even when the
        # indicator falls back to a spinner without the literal word Working.
        lines = self.completed
        border = '─' * self.columns
        # FooterComponent formats home-relative cwd and uses ASCII "..." when
        # this isolated temporary directory is wider than the terminal.
        resolved = work.resolve()
        home = Path.home().resolve()
        pwd = '~' + ('/' + resolved.relative_to(home).as_posix() if resolved != home else '') if resolved.is_relative_to(home) else str(resolved)
        if len(pwd) > self.columns:
            pwd = pwd[:self.columns - 3] + '...'
        for footer in range(len(lines) - 1, 3, -1):
            if lines[footer].endswith('mock') and lines[footer - 1] == pwd:
                return lines[footer - 4:footer - 1] == [border, '', border]
        return False
