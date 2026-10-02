#!/usr/bin/env python3
"""Reproduce the old final-text endpoint gap without running or timing Pi."""
from pathlib import Path

from interactive_workload import Terminal
from terminal_screen import Screen


def main():
    work = Path('/tmp/pi-endpoint-fixture')
    border = '─' * 100
    active = ('\x1b[?2026h\x1b[2J\x1b[H' + '\r\n'.join([
        'VISIBLE_CASE_FINAL', '── ⠋ Working ' + '─' * 87, ' ' * 100,
        border, str(work.resolve()), '0.7%/128k'.ljust(96) + 'mock']) + '\x1b[?2026l').encode()
    terminal = object.__new__(Terminal)
    terminal.work, terminal.screen, terminal.output = work, Screen(), active
    terminal.completed = lambda label: True  # Exact stop reply already persisted.
    terminal.screen.feed(active)
    terminal.frames = terminal.screen.frames
    assert terminal.visible('VISIBLE_CASE_FINAL')
    assert not terminal.idle_completion('CASE', 0)
    # agent_end can change only the top editor border. The incomplete repaint
    # must not pass, and the old Working bytes remain in raw terminal history.
    clear = ('\x1b[?2026h\x1b[2;1H\x1b[2K' + border).encode()
    terminal.screen.feed(clear)
    assert not terminal.idle_completion('CASE', 0)
    terminal.screen.feed(b'\x1b[?2026l')
    terminal.frames = terminal.screen.frames
    terminal.output += clear + b'\x1b[?2026l'
    assert b'Working' in terminal.output
    assert terminal.idle_completion('CASE', 0)
    assert not terminal.idle_completion('OTHER', 0)
    assert not terminal.idle_completion('CASE', terminal.frames)
    # Spinner-only status is also active: no regex on the word Working is used.
    spinner = active.replace(b'Working ', b'        ')
    screen = Screen()
    for byte in spinner:
        screen.feed(bytes([byte]))  # Split UTF-8 and escape sequences arbitrarily.
    assert not screen.idle_editor(work)
    assert 'VISIBLE_CASE_FINAL' in '\n'.join(screen.completed)
    # Pi 1.0's default fullscreen renderer enters a distinct alternate buffer.
    # A primary-screen snapshot must not survive entry or exit as a completed
    # frame; idle completion still requires the active buffer's cleared border.
    alt = Screen()
    alt.feed(b'PRIMARY_SCREEN\x1b[?2026l')
    primary_cells = [line[:] for line in alt.cells]
    alt.feed(b'\x1b[?1049h\x1b[?7l')
    assert alt.alternate and alt.alternate_entries == 1 and not alt.completed
    assert not any('PRIMARY_SCREEN' in ''.join(line) for line in alt.cells)
    for byte in active:
        alt.feed(bytes([byte]))
    assert not alt.idle_editor(work)
    alt.feed(clear)
    assert not alt.idle_editor(work)
    alt.feed(b'\x1b[?2026l')
    assert alt.idle_editor(work)
    assert 'VISIBLE_CASE_FINAL' in '\n'.join(alt.completed)
    alt.feed(b'\x1b[?1049l')
    assert not alt.alternate and alt.cells == primary_cells and not alt.completed
    alt.feed(b'\x1b[?1049h')
    alt.resize(24, 80)
    alt.feed(b'\x1b[?1049l\x1b[?2026l')
    assert len(alt.cells) == 24 and all(len(line) == 80 for line in alt.cells)
    assert alt.completed[0] == 'PRIMARY_SCREEN'
    assert not alt.idle_editor(work)
    try:
        alt.feed(b'\x1b[?1047h')
    except ValueError:
        pass
    else:
        raise AssertionError('Unsupported screen switches must fail explicitly')
    print('PASS: persisted stop/final marker while Working remains is rejected; incomplete and spinner-only frames rejected; completed one-row idle repaint accepted despite Working in scrollback')
    print('PASS: fullscreen alternate buffer clear/restore, stale frame rejection, split-byte parsing, active/idle border checks, resize and unsupported control rejection')


if __name__ == '__main__':
    main()
