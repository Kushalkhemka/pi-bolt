#!/usr/bin/env python3
"""Verify the installed launcher attaches, detaches, reattaches and resizes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import signal
import struct
import subprocess
import termios
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("launcher", type=Path)
    args = parser.parse_args()
    launcher = args.launcher.resolve()
    binary = str(launcher.parent / "tmux")
    name = "check-" + uuid.uuid4().hex[:12]
    socket = "pi-persist-" + name
    clients = []
    fds = []
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)

    def command(*words, check=True):
        return subprocess.run([binary, "-L", socket, *words], check=check,
                              capture_output=True, text=True, timeout=5).stdout.strip()

    def attach():
        master, slave = pty.openpty()
        fds.append(master)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        client = subprocess.Popen([str(launcher), name], stdin=slave,
                                  stdout=slave, stderr=slave, env=env)
        os.close(slave)
        clients.append(client)
        deadline = time.monotonic() + 5
        while command("list-clients", "-F", "#{client_pid}") != str(client.pid):
            if client.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Launcher failed to attach")
            time.sleep(.05)
        return client, master

    try:
        # A harmless live process stands in for Pi; no production server or
        # model provider is involved. The launcher must reuse this session.
        command("-f", str(launcher.parent / "pi-tmux.conf"), "new-session",
                "-d", "-s", "main", "-x", "120", "-y", "30", "sleep 60")
        identity = command("display-message", "-p", "#{pane_id} #{pane_pid}")
        first, _ = attach()
        command("detach-client", "-s", "main")
        first.wait(timeout=5)
        assert first.returncode == 0, "Detach did not exit cleanly"
        assert identity == command("display-message", "-p", "#{pane_id} #{pane_pid}")
        second, master = attach()
        assert identity == command("display-message", "-p", "#{pane_id} #{pane_pid}")
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 100, 0, 0))
        os.kill(second.pid, signal.SIGWINCH)
        deadline = time.monotonic() + 5
        while command("display-message", "-p", "#{pane_width}x#{pane_height}") != "100x25":
            if time.monotonic() > deadline:
                raise RuntimeError("Client resize did not reach pane")
            time.sleep(.05)
        assert identity == command("display-message", "-p", "#{pane_id} #{pane_pid}")
        print(json.dumps({"version": command("display-message", "-p", "#{version}"),
                          "attach_detach_reattach_resize": "passed",
                          "same_pane_and_process": True}))
    finally:
        command("kill-server", check=False)
        for client in clients:
            try:
                client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait(timeout=3)
        for fd in fds:
            os.close(fd)


if __name__ == "__main__":
    main()
