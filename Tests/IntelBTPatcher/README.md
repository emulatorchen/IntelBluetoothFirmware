# Interrupt-pipe capture regression test

Run from the repository root on a host with Python 3 and Clang++/ASan/UBSan:

```sh
python3 Tests/IntelBTPatcher/test_interrupt_pipe_capture.py
```

The harness extracts the actual production `captureIntelInterruptPipe` function
and compiles it against small host-side USB descriptor stubs. It uses temporary
files outside the checkout and does not load a kext or touch Bluetooth state.

An embedded copy of the pre-fix helper is the negative control: after capturing
Bluetooth `8087:0AAA`, the Intel USB hub `8087:5787` incorrectly overwrites its
pipe. That expected failure must occur before the production checks run.

The 16 production checks cover Bluetooth-first and hub-first enumeration, hub
hot-plug, other Intel product IDs, device-class-zero compatibility, other vendors,
non-interrupt endpoints, missing descriptors, and Bluetooth reinitialization.
Keeping other non-hub Intel devices eligible is intentional: this guard fixes hub
takeover without introducing a product-ID whitelist. It does not implement full
Bluetooth-interface classification or per-controller pipe tracking.

These are selection tests, not proof of kernel ABI, asynchronous callback routing,
concurrency safety or hardware compatibility. In particular, a host-side test
using another product ID is not a hardware test of that controller. The PR's
hardware evidence and exact-revision limitations are documented separately in its
body; no hardware claim should be inferred from these stubs.
