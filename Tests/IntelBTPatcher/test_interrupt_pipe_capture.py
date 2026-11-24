#!/usr/bin/env python3
"""Host-side regression test of the production interrupt-pipe capture helper.

Requires Python 3, Clang++, ASan and UBSan. No kernel SDK or kext loading.
The stubs test selection only, not kernel ABI, USB callbacks or concurrency.
"""

import hashlib
import subprocess
import tempfile
from pathlib import Path

PREFIX = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
namespace StandardUSB {
struct DeviceDescriptor {
    uint16_t idVendor;
    uint16_t idProduct;
    uint8_t bDeviceClass;
};
struct EndpointDescriptor { uint8_t bmAttributes; uint8_t bEndpointAddress; };
uint8_t getEndpointType(const EndpointDescriptor *ep) { return ep->bmAttributes & 3; }
}
constexpr uint8_t kIOUSBEndpointTypeInterrupt = 3;
enum { kUSBHostHubClass = 9 };
struct IOUSBHostDevice {
    const StandardUSB::DeviceDescriptor *descriptor;
    const StandardUSB::DeviceDescriptor *getDeviceDescriptor() { return descriptor; }
};
struct CIntelBTPatcher {
    static void *_hookPipeInstance;
    static void captureIntelInterruptPipe(void *, const StandardUSB::EndpointDescriptor *, IOUSBHostDevice *);
};
void *CIntelBTPatcher::_hookPipeInstance = nullptr;
'''

# Verbatim pre-fix helper at emulatorchen/IntelBluetoothFirmware master
# 5bee21363902a09f4e0df940869a74a89bba0340 (also c39d957441f457f8a882929616b8c02c42ba0447).
BASELINE_CAPTURE = r'''
#define VENDOR_USB_INTEL 0x8087
void CIntelBTPatcher::captureIntelInterruptPipe(void *that, StandardUSB::EndpointDescriptor const *descriptor, IOUSBHostDevice *device)
{
    if (!device)
        return;
    const StandardUSB::DeviceDescriptor *deviceDescriptor = device->getDeviceDescriptor();
    if (deviceDescriptor && deviceDescriptor->idVendor == VENDOR_USB_INTEL) {
        uint8_t epType = StandardUSB::getEndpointType(descriptor);
        if (epType == kIOUSBEndpointTypeInterrupt)
            CIntelBTPatcher::_hookPipeInstance = that;
    }
}
'''

SUFFIX = r'''
#define CHECK(NAME, CONDITION) do { \
    if (!(CONDITION)) { std::fprintf(stderr, "FAIL: %s\n", NAME); return 1; } \
    std::printf("PASS: %s\n", NAME); \
} while (0)
int main(int argc, char **argv) {
    StandardUSB::DeviceDescriptor btDesc {0x8087, 0x0aaa, 0xe0};
    StandardUSB::DeviceDescriptor hubDesc {0x8087, 0x5787, 0x09};
    StandardUSB::DeviceDescriptor otherVendorDesc {0x1234, 0x0aaa, 0xe0};
    StandardUSB::DeviceDescriptor otherIntelDesc {0x8087, 0x0032, 0xe0};
    StandardUSB::DeviceDescriptor compositeDesc {0x8087, 0x0033, 0x00};
    StandardUSB::DeviceDescriptor otherHubDesc {0x1234, 0x5787, 0x09};
    IOUSBHostDevice bt {&btDesc}, hub {&hubDesc}, otherVendor {&otherVendorDesc};
    IOUSBHostDevice otherIntel {&otherIntelDesc}, composite {&compositeDesc};
    IOUSBHostDevice otherHub {&otherHubDesc}, missing {nullptr};
    StandardUSB::EndpointDescriptor interrupt {3, 0x81}, bulk {2, 0x82};
    StandardUSB::EndpointDescriptor control {0, 0}, isochronous {1, 0x83};
    int btPipe = 1, hubPipe = 2, ignoredPipe = 3, newBtPipe = 4;
    auto capture = CIntelBTPatcher::captureIntelInterruptPipe;
    auto &selected = CIntelBTPatcher::_hookPipeInstance;

    capture(&btPipe, &interrupt, &bt);
    CHECK("Bluetooth interrupt selected", selected == &btPipe);
    capture(&hubPipe, &interrupt, &hub);
    CHECK("hub hot-plug preserves Bluetooth pipe", selected == &btPipe);
    if (argc > 1 && std::strcmp(argv[1], "regression-only") == 0) return 0;

    capture(&ignoredPipe, &interrupt, &otherVendor);
    CHECK("non-Intel device ignored", selected == &btPipe);
    capture(&newBtPipe, &interrupt, &otherIntel);
    CHECK("another Intel Bluetooth PID remains supported", selected == &newBtPipe);
    capture(&btPipe, &interrupt, &composite);
    CHECK("device-class-zero Intel behavior preserved", selected == &btPipe);
    capture(&ignoredPipe, &bulk, &bt);
    CHECK("bulk endpoint ignored", selected == &btPipe);
    capture(&ignoredPipe, &control, &bt);
    CHECK("control endpoint ignored", selected == &btPipe);
    capture(&ignoredPipe, &isochronous, &bt);
    CHECK("isochronous endpoint ignored", selected == &btPipe);
    capture(&ignoredPipe, &interrupt, nullptr);
    CHECK("null device ignored", selected == &btPipe);
    capture(&ignoredPipe, nullptr, &bt);
    CHECK("null endpoint descriptor ignored", selected == &btPipe);
    capture(&ignoredPipe, &interrupt, &missing);
    CHECK("missing device descriptor ignored", selected == &btPipe);

    selected = nullptr;
    capture(&hubPipe, &interrupt, &hub);
    CHECK("hub-first enumeration does not select hub", selected == nullptr);
    capture(&btPipe, &interrupt, &bt);
    CHECK("Bluetooth selected after hub-first enumeration", selected == &btPipe);
    capture(&hubPipe, &interrupt, &hub);
    CHECK("subsequent hub init preserves Bluetooth", selected == &btPipe);
    capture(&newBtPipe, &interrupt, &bt);
    CHECK("Bluetooth pipe reinitialization allowed", selected == &newBtPipe);
    capture(&ignoredPipe, &interrupt, &otherHub);
    CHECK("non-Intel hub ignored", selected == &newBtPipe);
    return 0;
}
'''


def production_capture(source):
    text = source.read_text()
    start = text.index('#define VENDOR_USB_INTEL ')
    end = text.index('\nint CIntelBTPatcher::\nnewInitPipe(', start)
    return text[start:end]


def build_case(directory, name, capture):
    source = directory / (name + '.cpp')
    source.write_text(PREFIX + capture + SUFFIX)
    executable = directory / name
    subprocess.run([
        'clang++', '-std=c++14', '-Wall', '-Wextra', '-Werror', '-pedantic',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
        str(source), '-o', str(executable),
    ], check=True)
    return executable


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / 'IntelBTPatcher/IntelBTPatcher.cpp'
    capture = production_capture(source)
    print('Production source SHA256:', hashlib.sha256(source.read_bytes()).hexdigest(), flush=True)
    with tempfile.TemporaryDirectory(prefix='intelbt-capture-test-') as temporary:
        directory = Path(temporary)
        baseline = build_case(directory, 'baseline', BASELINE_CAPTURE)
        result = subprocess.run([str(baseline), 'regression-only'], capture_output=True, text=True)
        print(result.stdout, end='')
        print(result.stderr, end='')
        if result.returncode != 1 or 'FAIL: hub hot-plug preserves Bluetooth pipe' not in result.stderr:
            raise SystemExit('Unmodified-selector negative control did not reproduce hub takeover')
        print('Expected upstream-selector failure confirmed.', flush=True)
        patched = build_case(directory, 'production', capture)
        subprocess.run([str(patched)], check=True)
        print('All 16 production-selector checks passed with ASan/UBSan.', flush=True)


if __name__ == '__main__':
    main()
