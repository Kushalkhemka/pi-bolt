# The native hybrid

Pi-Bolt compiles the pinned Pi bundle's supported JavaScript functions ahead of time and ships their machine code in a matched `.aot` sidecar. The executable maps that image and loads its prelinked module graph at startup. This avoids compiling the same covered functions on each launch.

JavaScriptCore remains in the executable. Dynamically loaded code, extensions, workers and unsupported paths can use its interpreter and JIT. The default is therefore a hybrid, not a globally JIT-disabled runtime. The launcher fixes the validated engine flags, mapped-image path and two GC workers, then passes through normal Pi arguments.

`pi-bolt-tier10000` additionally permits covered native functions to tier into JIT after 10,000 calls. This can improve sustained-session wall time but adds compilation CPU and memory; keep it an explicit option.

The M5 build uses M5-specific compiler settings and requires macOS 27+. Only the base M5 has been validated. The same executable/image pair must be installed together; images from another compiler are incompatible. Installer and doctor verify complete hashes, while each launch performs size checks to avoid hashing 132 MB repeatedly. Same-size post-install corruption needs a doctor check.

A fixed virtual address reservation and fallback on missing/incompatible images remain engine constraints. The release is unsigned and broader provider/concurrency soak is pending. Typed compiler integration and whole-program optimization are still areas for compiler work. Successful Pi workload measurements do not establish that AOT beats JIT for every JavaScript workload.
