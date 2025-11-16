# [0.8.0](https://github.com/weirdtangent/pulse-os/compare/v0.7.0...v0.8.0) (2025-11-16)


### Features

* update update button name when available; more choices for version check cadence ([ad6acec](https://github.com/weirdtangent/pulse-os/commit/ad6acec1ec50acea28c0acb6503ad8a57528d086))

# [0.7.0](https://github.com/weirdtangent/pulse-os/compare/v0.6.0...v0.7.0) (2025-11-16)


### Features

* add semantic version checking; check version at startup; thread-safe MQTT publishing ([37b0740](https://github.com/weirdtangent/pulse-os/commit/37b0740b23191e145175cb552547492a81f1190d))

# [0.6.0](https://github.com/weirdtangent/pulse-os/compare/v0.5.0...v0.6.0) (2025-11-16)


### Features

* add reboot button since Update is only available with new Version on github ([42f34f7](https://github.com/weirdtangent/pulse-os/commit/42f34f76b91af2b1ef7769d943209e440b2e3821))

# [0.5.0](https://github.com/weirdtangent/pulse-os/compare/v0.4.0...v0.5.0) (2025-11-16)


### Features

* add update button availability based on version check on GitHub ([fd1eee7](https://github.com/weirdtangent/pulse-os/commit/fd1eee742b6b80997773987ae633f9c4a0283dbf))

# [0.4.0](https://github.com/weirdtangent/pulse-os/compare/v0.3.5...v0.4.0) (2025-11-16)


### Features

* updated README; add Update button to MQTT listener ([8259679](https://github.com/weirdtangent/pulse-os/commit/825967979edf5f9971fc22e8c8b2bad3260f6aa8))

## [0.3.5](https://github.com/weirdtangent/pulse-os/compare/v0.3.4...v0.3.5) (2025-11-16)


### Bug Fixes

* use stderr for logging in setup script ([cd9592f](https://github.com/weirdtangent/pulse-os/commit/cd9592fdfd43d5dd89841befe87eba7de40ace84))

## [0.3.4](https://github.com/weirdtangent/pulse-os/compare/v0.3.3...v0.3.4) (2025-11-16)


### Bug Fixes

* ensure bootloader splash is installed and configured ([350214d](https://github.com/weirdtangent/pulse-os/commit/350214d4b18dc191149e86cb1e0b7d8a3f7c635b))

## [0.3.3](https://github.com/weirdtangent/pulse-os/compare/v0.3.2...v0.3.3) (2025-11-16)


### Bug Fixes

* delay plymouth quit units until graphical target ([d6cf4ec](https://github.com/weirdtangent/pulse-os/commit/d6cf4ec6c5f613f8388117b0f2e4d738e4af0cda))

## [0.3.2](https://github.com/weirdtangent/pulse-os/compare/v0.3.1...v0.3.2) (2025-11-16)


### Bug Fixes

* update README and splash assets and enable splash in setup script ([e920bd0](https://github.com/weirdtangent/pulse-os/commit/e920bd072d17799e158431bed73a1793458c8b8b))

## [0.3.1](https://github.com/weirdtangent/pulse-os/compare/v0.3.0...v0.3.1) (2025-11-16)


### Bug Fixes

* install bootloader splash and enable splash in setup script ([b5f3864](https://github.com/weirdtangent/pulse-os/commit/b5f386451206b6fbd0433f4fbe496764669dfe50))

# [0.3.0](https://github.com/weirdtangent/pulse-os/compare/v0.2.3...v0.3.0) (2025-11-16)


### Features

* install boot splash assets ([ef1be40](https://github.com/weirdtangent/pulse-os/commit/ef1be40c9377896b102587dd46a10c4d8c79c9bf))

## [0.2.3](https://github.com/weirdtangent/pulse-os/compare/v0.2.2...v0.2.3) (2025-11-16)


### Bug Fixes

* use default entity id for home button ([552fa8a](https://github.com/weirdtangent/pulse-os/commit/552fa8abf914c12fd5b96a515b4e97b8e49873e1))

## [0.2.2](https://github.com/weirdtangent/pulse-os/compare/v0.2.1...v0.2.2) (2025-11-16)


### Bug Fixes

* component does not need availability topic ([0732e4f](https://github.com/weirdtangent/pulse-os/commit/0732e4f4202d5146246da5fe701e2b9e383ffafa))

## [0.2.1](https://github.com/weirdtangent/pulse-os/compare/v0.2.0...v0.2.1) (2025-11-16)


### Bug Fixes

* use short-form keys for button component and fix device definition ([324d5c7](https://github.com/weirdtangent/pulse-os/commit/324d5c760262e214aa777d4fb92c898e60fe0c7b))

# [0.2.0](https://github.com/weirdtangent/pulse-os/compare/v0.1.0...v0.2.0) (2025-11-16)


### Bug Fixes

* use 1.1.1.1 for ip address detection and validate ip address ([ebb6fba](https://github.com/weirdtangent/pulse-os/commit/ebb6fba805256261145a9f59ea9567074a7c4aab))
* use config topic for device definition and add origin ([6f19a1c](https://github.com/weirdtangent/pulse-os/commit/6f19a1c8e98be17bccb24bbfb7b2e4e2ac7ff7c8))
* use device topic instead of devices topic ([496eb0c](https://github.com/weirdtangent/pulse-os/commit/496eb0cecb8195f3172f4fe25b2ae8b69f58ffc4))
* use platform instead of domain for button component ([59cc4d3](https://github.com/weirdtangent/pulse-os/commit/59cc4d36c47afa28979f85bb5df35c05e248d312))


### Features

* add build workflow and VERSION file ([90a217a](https://github.com/weirdtangent/pulse-os/commit/90a217a7eed43e2e33f19d40cd1d56be4ab36c9c))
* add IP and MAC address to device info ([ede2e18](https://github.com/weirdtangent/pulse-os/commit/ede2e183687467213c1ee96be47eaa9af231e7f2))
* add release configuration for semantic-release versioning ([101b86d](https://github.com/weirdtangent/pulse-os/commit/101b86d05b89764ddd21da0234a1f0c09021c598))
