# Provisioning Checklist

This document details the checklist for qualifying and provisioning a recycled Android phone for the Akshrava platform.

## 1. Hardware Qualification
- [ ] **Battery Health**: Must have >80% original capacity. Run a stress test (e.g., continuous camera use for 1 hour) to ensure it does not abruptly shut down.
- [ ] **Camera**: The main back camera must function clearly without severe scratches on the lens.
- [ ] **Thermal Limits**: Device must not exceed 45°C during normal load.
- [ ] **Storage/RAM**: At least 3GB RAM and 32GB storage.

## 2. Software Preparation
- [ ] **Factory Reset**: Perform a full factory reset.
- [ ] **OS Update**: Install the latest available security updates for the device.
- [ ] **Uninstall Bloatware**: Remove or disable any non-essential manufacturer apps to preserve battery and reduce background processes.
- [ ] **App Installation**: Install the Akshrava APK.
- [ ] **Permissions**: Grant Camera, notification, and Battery Optimization exemptions. Location is not used by this release.

## 3. Account & Connectivity
- [ ] **Data Plan**: Insert an active SIM card with a sufficient data plan (at least 2GB/day).
- [ ] **Device Token**: Generate a unique device token from the backend and configure it in the App settings.
- [ ] **Lock Screen**: Disable lock screen or set it to a simple swipe to allow immediate assistance upon waking.

## 4. Final Testing
- [ ] **Cloud Connection**: Verify the device successfully connects to the backend and receives `vision_enabled=true`.
- [ ] **Outage behaviour**: Disable data briefly and verify the phone says vision assistance is unavailable; it must not claim a local fallback.
- [ ] **Stop control**: Verify the visible Stop control stops camera assistance and the notification disappears.
