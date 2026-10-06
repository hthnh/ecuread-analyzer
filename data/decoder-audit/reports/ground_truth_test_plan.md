# Ground-Truth Test Plan

This plan is for the next controlled recording session. It avoids manual road-condition labeling and focuses on synchronized external references that can validate the 0x71/0x17 byte layout.

## Key-On, Engine-Off

Record external ambient temperature, battery voltage measured by multimeter, throttle fully closed, and vehicle stationary.

Expected uses:

- Identify MAP atmospheric baseline.
- Confirm battery scaling.
- Confirm TPS closed value.
- Find the true speed byte.
- Detect unsupported ECT/EOT channels.

## Cold Start

Record ambient temperature before starting.

Expected uses:

- IAT should initially be near ambient.
- ECT or EOT should initially be near ambient.
- Battery should drop during cranking.
- Injector duration should increase during start.

## Warm-Up At Idle

Record for several minutes without throttle changes where possible.

Expected uses:

- Engine temperature should rise smoothly.
- IAT should not behave identically to engine temperature.
- MAP should remain within an idle operating band.

## Throttle Sweep With Engine Off

Slowly move the throttle from closed to open and back while stationary.

Expected uses:

- Identify TPS voltage byte.
- Identify TPS position byte.
- Derive closed and open calibration points.

## External Battery Reference

Log simultaneous multimeter readings at key-on engine-off, cranking, idle, and elevated RPM.

## Injector Validation

Where safe and technically possible, compare the candidate injector duration with an oscilloscope or logic analyzer measurement. Do not rely on firmware `/100` scaling until this is checked.

## Vehicle Speed Validation

Record externally measured speed or GPS speed and synchronize identifiable speed steps. Do not require dangerous interaction while riding.
