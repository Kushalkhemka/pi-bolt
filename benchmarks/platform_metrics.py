"""Per-process memory and CPU counters for native macOS and Linux benchmarks."""
import ctypes
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path

TICK_NS = None
if sys.platform == 'darwin':
    LIBPROC = ctypes.CDLL('/usr/lib/libproc.dylib')
    LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    LIBPROC.proc_pid_rusage.restype = ctypes.c_int
    class MachTimebase(ctypes.Structure):
        _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]
    base = MachTimebase()
    ctypes.CDLL('/usr/lib/libSystem.B.dylib').mach_timebase_info(ctypes.byref(base))
    TICK_NS = base.numer / base.denom


def memory(pid):
    if sys.platform == 'darwin':
        buf = ctypes.create_string_buffer(1024)
        if LIBPROC.proc_pid_rusage(pid, 4, ctypes.byref(buf)) != 0:
            raise OSError('proc_pid_rusage failed')
        current = struct.unpack_from('Q', buf, 16 + 7 * 8)[0] / 1e6
        return {'footprint_mb': current,
                'peak_footprint_mb': max(current, struct.unpack_from('Q', buf, 16 + 28 * 8)[0] / 1e6),
                'rss_mb': struct.unpack_from('Q', buf, 16 + 6 * 8)[0] / 1e6,
                'cpu_ms': sum(struct.unpack_from('QQ', buf, 16)) * TICK_NS / 1e6}
    if sys.platform == 'linux':
        status = (Path('/proc') / str(pid) / 'status').read_text()
        counts = {line.split(':', 1)[0]: int(line.split()[1]) * 1024
                  for line in status.splitlines() if line.startswith(('VmRSS:', 'VmHWM:'))}
        # Linux comm can contain spaces or parentheses; fields start after the last ')'.
        stat = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        rss = counts['VmRSS'] / 1e6
        return {'footprint_mb': rss, 'peak_footprint_mb': counts['VmHWM'] / 1e6, 'rss_mb': rss,
                'cpu_ms': (int(stat[11]) + int(stat[12])) * 1000 / os.sysconf('SC_CLK_TCK')}
    raise RuntimeError(f'Process measurement unsupported on {sys.platform}')


def peak_rss_mb(usage):
    return usage.ru_maxrss / 1e6 if sys.platform == 'darwin' else usage.ru_maxrss * 1024 / 1e6


def hardware_metadata():
    if sys.platform == 'darwin':
        return {'hardware': subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip(),
                'macos': subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip(),
                'mach_tick_nanoseconds': TICK_NS, 'memory_metric': 'macOS phys_footprint',
                'platform': sys.platform, 'architecture': platform.machine()}
    return {'hardware': platform.machine(), 'kernel': platform.release(),
            'memory_metric': 'Linux RSS (VmRSS/VmHWM); not macOS phys_footprint',
            'platform': sys.platform, 'architecture': platform.machine()}
