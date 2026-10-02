#!/usr/bin/env python3
"""Check zmx alternate-screen restore, process persistence, UTF-8 and resize.

Uses a private socket directory and synthetic TUI; no existing sessions or Pi.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty


def producer(state_path):
    tty.setraw(0)
    def state():
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))
        temporary = Path(f"{state_path}.{time.monotonic_ns()}")
        temporary.write_text(json.dumps({"pid": os.getpid(), "rows": rows, "cols": cols}))
        temporary.replace(state_path)
    signal.signal(signal.SIGWINCH, lambda *_: state())
    state()
    os.write(1, b"\x1b[?1049h\x1b[2J\x1b[?25l")
    def draw(text):
        data = ("\x1b[?2026h\x1b[4;5H\x1b[38;2;40;190;90m" + text +
                "\x1b[0m\x1b[6;5HUnicode: 日本語 λ\x1b[?2026l").encode()
        # Deliberately split CSI sequences and multi-byte UTF-8 characters.
        for start in range(0, len(data), 2):
            os.write(1, data[start:start + 2])
    draw("INITIAL-SCREEN")
    pending = b""
    while True:
        pending += os.read(0, 4096)
        while b"\n" in pending:
            command, pending = pending.split(b"\n", 1)
            draw(command.decode())


def check(binary):
    with tempfile.TemporaryDirectory(prefix="zmx-persistence-") as directory:
        state_path = Path(directory) / "producer.json"
        env = {**os.environ, "ZMX_DIR": str(Path(directory) / "sockets"),
               "TERM": "xterm-256color"}
        env.pop("ZMX_SESSION", None)
        env.pop("TMUX", None)
        clients, masters = [], []
        def attach(create=False):
            master, slave = pty.openpty()
            masters.append(master)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
            command = [binary, "attach", "test"]
            if create:
                command += [sys.executable, str(Path(__file__).resolve()),
                            "--producer-state", str(state_path)]
            client = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env)
            clients.append(client)
            os.close(slave)
            return client, master
        def read_until(master, *markers):
            output = b""
            deadline = time.monotonic() + 5
            while not all(marker in output for marker in markers):
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Screen restore missing {markers!r}; output={output[-500:]!r}")
                if select.select([master], [], [], .05)[0]:
                    output += os.read(master, 65536)
            return output
        try:
            first, master = attach(create=True)
            read_until(master, b"INITIAL-SCREEN", "日本語 λ".encode())
            initial = json.loads(state_path.read_text())
            assert (initial["cols"], initial["rows"]) == (120, 30), initial
            os.write(master, b"\x1c")
            first.wait(timeout=5)
            assert first.returncode == 0
            os.kill(initial["pid"], 0)
            subprocess.run([binary, "send", "test", "DETACHED-SCREEN\n"],
                           env=env, check=True, capture_output=True, timeout=5)
            time.sleep(.1)
            second, master = attach()
            output = read_until(master, b"DETACHED-SCREEN", "日本語 λ".encode())
            assert b"\x1b[" in output, "No terminal control sequences restored"
            assert json.loads(state_path.read_text())["pid"] == initial["pid"]
            # Prove the reattached client is exchanging live input/output before
            # exercising resize, rather than relying only on its snapshot.
            os.write(master, b"REATTACHED-LIVE\n")
            read_until(master, b"REATTACHED-LIVE")
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 100, 0, 0))
            os.kill(second.pid, signal.SIGWINCH)
            deadline = time.monotonic() + 5
            while True:
                try:
                    resized = json.loads(state_path.read_text())
                except json.JSONDecodeError:
                    continue
                if (resized["cols"], resized["rows"]) == (100, 25):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Resize did not reach producer: {resized}")
                time.sleep(.02)
            assert resized["pid"] == initial["pid"]
            print(json.dumps({"attach_detach_reattach": "passed",
                              "alternate_screen_updated_while_detached": "restored",
                              "fragmented_csi_and_utf8": "passed",
                              "same_process": True, "resize": "100x25"}))
        finally:
            subprocess.run([binary, "kill", "test", "--force"], env=env,
                           capture_output=True, timeout=5)
            for client in clients:
                if client.poll() is None:
                    client.terminate()
                client.wait(timeout=3)
            for master in masters:
                os.close(master)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?")
    parser.add_argument("--producer-state")
    args = parser.parse_args()
    if args.producer_state:
        producer(args.producer_state)
    elif args.binary:
        check(args.binary)
    else:
        parser.error("zmx binary required")
