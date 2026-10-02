#!/usr/bin/env python3
"""Compare a direct PTY with an isolated tmux server under synthetic TUI output.

No API calls or agents. The outer PTY is drained, not visually rendered, so this
measures multiplexer processing and input response, not SSH or terminal GPU work.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty


def emit(args):
    tty.setraw(0)
    telemetry = None
    if args.telemetry_dir:
        telemetry_file = Path(args.telemetry_dir) / f"{os.getpid()}.bin"
        with telemetry_file.open("wb") as handle:
            handle.truncate(mmap.PAGESIZE)
        with telemetry_file.open("r+b") as handle:
            telemetry = mmap.mmap(handle.fileno(), mmap.PAGESIZE)
    while not Path(args.gate).exists():
        time.sleep(0.01)
    pending = b""
    frame = 0
    deadline = time.monotonic()
    started = deadline
    while True:
        timeout = max(0, deadline - time.monotonic())
        if select.select([0], [], [], timeout)[0]:
            try:
                pending += os.read(0, 4096)
            except BlockingIOError:
                pass
            while b"\n" in pending:
                command, pending = pending.split(b"\n", 1)
                # Reserve the last row for acknowledgements. Workload rows never
                # overwrite it, so tmux can coalesce frames without losing probes.
                ack = b"\x1b[%d;1H\x1b[2K\x1b[0m" % args.rows + command
                os.write(1, ack)
        if time.monotonic() < deadline:
            continue
        pieces = ["\x1b[?2026h"] if args.sync else []
        for row in range(1, args.rows):
            # Many style transitions, like syntax-highlighted tool output. Each
            # frame changes every cell: intentionally a demanding full repaint.
            pieces.append(f"\x1b[{row};1H\x1b[2K")
            for col in range(0, args.columns - 1, 8):
                color = (frame + row + col) % 200 + 16
                text = f"{frame % 10000:04d}code"[:min(8, args.columns - 1 - col)]
                pieces.append(f"\x1b[38;5;{color}m{text}")
        pieces.append("\x1b[0m")
        if args.sync:
            pieces.append("\x1b[?2026l")
        data = "".join(pieces).encode()
        view = memoryview(data)
        while view:
            view = view[os.write(1, view):]
        frame += 1
        if telemetry is not None:
            telemetry[:24] = struct.pack("Qdd", frame, started, time.monotonic())
        # Don't emit a catch-up burst if the output pipe blocks.
        deadline = time.monotonic() + 1 / args.fps


def run_tmux(binary, socket, *arguments, check=True):
    return subprocess.run([binary, "-S", socket, *arguments], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=5)


def measure(args, binary, use_tmux, sync, isolate=False):
    with tempfile.TemporaryDirectory(prefix="pi-tmux-output-") as directory:
        gate = str(Path(directory) / "start")
        socket = str(Path(directory) / "tmux.sock")
        background_socket = str(Path(directory) / "background.sock")
        worker = [sys.executable, str(Path(__file__).resolve()), "emit",
                  "--gate", gate, "--rows", str(args.rows),
                  "--columns", str(args.columns), "--fps", str(args.fps),
                  "--telemetry-dir", directory]
        if sync:
            worker.append("--sync")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", args.rows, args.columns, 0, 0))
        process = None
        zmx_sessions = []
        env = {**os.environ, "TERM": "xterm-256color"}
        # Avoid inherited nested-session detection on either side.
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env.pop("ZMX_SESSION", None)
        if args.backend == "zmx":
            env["ZMX_DIR"] = str(Path(directory) / "zmx")
        try:
            if use_tmux and args.backend == "zmx":
                # Create each background session from a correctly sized PTY,
                # then detach its client. The producer waits on the same gate.
                for index in range(1, args.workers):
                    name = f"worker-{index}"
                    zmx_sessions.append(name)
                    bg_master, bg_slave = pty.openpty()
                    fcntl.ioctl(bg_slave, termios.TIOCSWINSZ,
                                struct.pack("HHHH", args.rows, args.columns, 0, 0))
                    bg = subprocess.Popen([binary, "attach", name, *worker],
                                          stdin=bg_slave, stdout=bg_slave,
                                          stderr=bg_slave, env=env)
                    os.close(bg_slave)
                    try:
                        until = time.monotonic() + 5
                        while len(list(Path(directory).glob("*.bin"))) < index:
                            if bg.poll() is not None or time.monotonic() > until:
                                raise RuntimeError("zmx background producer failed to start")
                            time.sleep(.01)
                        os.write(bg_master, b"\x1c")  # zmx detach: Ctrl+backslash
                        bg.wait(timeout=5)
                        if bg.returncode:
                            raise RuntimeError("zmx detach failed")
                    finally:
                        if bg.poll() is None:
                            bg.terminate()
                            bg.wait(timeout=3)
                        os.close(bg_master)
                zmx_sessions.append("visible")
                command = [binary, "attach", "visible", *worker]
            elif use_tmux:
                run_tmux(binary, socket, "-f", "/dev/null", "new-session", "-d",
                         "-s", "load", "-x", str(args.columns), "-y", str(args.rows),
                         shlex.join(worker))
                run_tmux(binary, socket, "set-option", "-g", "status", "off")
                run_tmux(binary, socket, "set-window-option", "-g", "automatic-rename", "off")
                for index in range(1, args.workers):
                    if isolate and index == 1:
                        run_tmux(binary, background_socket, "-f", "/dev/null",
                                 "new-session", "-d", "-s", "load", "-x",
                                 str(args.columns), "-y", str(args.rows), shlex.join(worker))
                        run_tmux(binary, background_socket, "set-option", "-g", "status", "off")
                        run_tmux(binary, background_socket, "set-window-option", "-g", "automatic-rename", "off")
                    else:
                        target_socket = background_socket if isolate else socket
                        run_tmux(binary, target_socket, "new-window", "-d", "-t", "load",
                                 "-n", f"worker-{index}", shlex.join(worker))
                command = [binary, "-S", socket, "attach-session", "-t", "load"]
            else:
                command = worker
            process = subprocess.Popen(command, stdin=slave, stdout=slave,
                                       stderr=slave, env=env)
            os.close(slave)
            slave = None
            os.set_blocking(master, False)
            time.sleep(0.2)
            Path(gate).touch()
            started = time.monotonic()
            next_probe = started + 0.3
            pending = {}
            latencies = []
            received = bytearray()
            total_bytes = 0
            sent = 0
            while time.monotonic() < started + args.seconds:
                now = time.monotonic()
                if now >= next_probe and now < started + args.seconds - 0.3:
                    # The first character always changes: tmux can otherwise
                    # emit just the changed suffix of a repeated probe prefix.
                    nonce = hashlib.sha256(str(sent).encode()).hexdigest()[:15]
                    marker = f"{chr(65 + sent % 26)}{nonce}".encode()
                    os.write(master, marker + b"\n")
                    pending[marker] = time.monotonic()
                    sent += 1
                    next_probe = now + 0.2
                if not select.select([master], [], [], 0.01)[0]:
                    continue
                try:
                    chunk = os.read(master, 65536)
                except BlockingIOError:
                    continue
                except OSError as error:
                    if error.errno == errno.EIO:
                        raise RuntimeError("Worker/client exited before measurement completed") from error
                    raise
                if not chunk:
                    raise RuntimeError("Unexpected end of PTY output")
                total_bytes += len(chunk)
                received.extend(chunk)
                now = time.monotonic()
                for marker, sent_at in list(pending.items()):
                    if marker in received:
                        latencies.append((now - sent_at) * 1000)
                        del pending[marker]
                # Keep enough tail to match markers split across read boundaries.
                del received[:-32]
            elapsed = time.monotonic() - started
            counters = None
            if use_tmux and args.backend == "tmux":
                counters = run_tmux(binary, socket, "list-clients", "-F",
                                    "#{client_discarded} #{client_written}").stdout.strip()
            ordered = sorted(latencies)
            producer_frames = []
            for telemetry_file in Path(directory).glob("*.bin"):
                frame_count, producer_start, last_frame = struct.unpack(
                    "Qdd", telemetry_file.read_bytes()[:24])
                if frame_count and last_frame > producer_start:
                    producer_frames.append(frame_count / (last_frame - producer_start))
            return {"mode": "tmux-isolated-visible" if isolate else args.backend if use_tmux else "direct",
                    "synchronized_updates": sync,
                    "workers": args.workers if use_tmux else 1,
                    "elapsed_seconds": elapsed, "outer_pty_bytes": total_bytes,
                    "outer_pty_mb_per_second": total_bytes / elapsed / 1e6,
                    "probes_sent": sent, "probes_received": len(latencies),
                    "unanswered_at_end": len(pending),
                    "input_response_p50_ms": statistics.median(ordered) if ordered else None,
                    "input_response_p95_ms": ordered[min(len(ordered)-1, int(.95*len(ordered)))] if ordered else None,
                    "producer_count": len(producer_frames),
                    "producer_fps_median": statistics.median(producer_frames) if producer_frames else None,
                    "producer_fps_min": min(producer_frames) if producer_frames else None,
                    "producer_fps_total": sum(producer_frames),
                    "client_discarded_and_written": counters}
        finally:
            if use_tmux and args.backend == "zmx" and zmx_sessions:
                subprocess.run([binary, "kill", *zmx_sessions, "--force"],
                               env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=10)
            elif use_tmux:
                run_tmux(binary, socket, "kill-server", check=False)
                if isolate:
                    run_tmux(binary, background_socket, "kill-server", check=False)
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            os.close(master)
            if slave is not None:
                os.close(slave)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    producer = sub.add_parser("emit")
    producer.add_argument("--gate", required=True)
    producer.add_argument("--sync", action="store_true")
    producer.add_argument("--telemetry-dir")
    bench = sub.add_parser("bench")
    bench.add_argument("--tmux", default="tmux")
    bench.add_argument("--backend", choices=["tmux", "zmx"], default="tmux")
    bench.add_argument("--binary", help="Alternative executable (overrides --tmux)")
    bench.add_argument("--workers", type=int, default=1,
                       help="tmux windows, one visible; direct always has one worker")
    bench.add_argument("--seconds", type=float, default=5)
    bench.add_argument("--output", type=Path)
    bench.add_argument("--cases", nargs="+", default=["direct", "shared-sync", "shared-plain"],
                       choices=["direct", "shared-sync", "shared-plain", "isolated-sync"])
    for command in (producer, bench):
        command.add_argument("--rows", type=int, default=30)
        command.add_argument("--columns", type=int, default=120)
        command.add_argument("--fps", type=int, default=60)
    args = parser.parse_args()
    if not (3 <= args.rows <= 100 and 20 <= args.columns <= 300 and 1 <= args.fps <= 120):
        parser.error("Use rows 3..100, columns 20..300, and fps 1..120")
    if args.action == "emit":
        emit(args)
        return
    if not (1 <= args.workers <= 64 and 2 <= args.seconds <= 60):
        parser.error("Use workers 1..64 and seconds 2..60")
    if args.backend == "zmx" and "isolated-sync" in args.cases:
        parser.error("zmx already uses a separate daemon for every session")
    binary = shutil.which(args.binary or args.tmux)
    if binary is None:
        parser.error("tmux executable not found")
    version = subprocess.check_output([binary, "version" if args.backend == "zmx" else "-V"], text=True).strip()
    report = {"backend": args.backend, "tmux_version": version, "platform": sys.platform,
              "rows": args.rows, "columns": args.columns, "target_fps_per_worker": args.fps,
              "method": "Synthetic full-screen colored redraws; PTY drained without terminal emulation. Private sockets, no user config, one visible producer. Direct baseline has one worker. isolated-sync puts the visible worker in its own tmux server and the remaining workers in a second server. zmx uses one daemon per session. Producer FPS measures completed writes, not visually painted frames. No SSH terminal rendering, Pi, model requests, or API calls.",
              "samples": []}
    cases = {"direct": (False, True, False), "shared-sync": (True, True, False),
             "shared-plain": (True, False, False), "isolated-sync": (True, True, True)}
    for case in args.cases:
        result = measure(args, binary, *cases[case])
        report["samples"].append(result)
        print(json.dumps(result), flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
