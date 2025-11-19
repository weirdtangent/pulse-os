# [0.27.0](https://github.com/weirdtangent/pulse-os/compare/v0.26.2...v0.27.0) (2025-11-19)


### Features

* add Home Assistant support to Pulse assistant ([2f42a1f](https://github.com/weirdtangent/pulse-os/commit/2f42a1f81c6032f0a25bc5a513e25f003ee59504))

## [0.26.2](https://github.com/weirdtangent/pulse-os/compare/v0.26.1...v0.26.2) (2025-11-19)


### Bug Fixes

* safe-reboot.sh: add missing fi ([0084676](https://github.com/weirdtangent/pulse-os/commit/0084676ac12fe97c2316b548eb2363b767bb9b06))

## [0.26.1](https://github.com/weirdtangent/pulse-os/compare/v0.26.0...v0.26.1) (2025-11-19)

# [0.26.0](https://github.com/weirdtangent/pulse-os/compare/v0.25.0...v0.26.0) (2025-11-19)


### Features

* validate each Wyoming endpoint with Describe request ([b75d7ff](https://github.com/weirdtangent/pulse-os/commit/b75d7ffbc39b1530d55172278bd36b7578cad5b7))

# [0.25.0](https://github.com/weirdtangent/pulse-os/compare/v0.24.1...v0.25.0) (2025-11-19)


### Features

* verify_conf script to validate pulse.conf connectivity ([855f217](https://github.com/weirdtangent/pulse-os/commit/855f217e08dca34d4d47339ad72888a3183226a7))

## [0.24.1](https://github.com/weirdtangent/pulse-os/compare/v0.24.0...v0.24.1) (2025-11-19)

# [0.24.0](https://github.com/weirdtangent/pulse-os/compare/v0.23.2...v0.24.0) (2025-11-19)


### Bug Fixes

* another shot at parsing bash blocks ([fe69bc2](https://github.com/weirdtangent/pulse-os/commit/fe69bc2f3ac1655903363ef2c676f5a8ce65483f))
* ensure thump sample is copied to target path ([58b4d8c](https://github.com/weirdtangent/pulse-os/commit/58b4d8ce6e0d46dec7cef19a77300ce09ae1922d))
* improve sync-pulse-conf.py to handle NEW markers and legacy replacements ([a30ff03](https://github.com/weirdtangent/pulse-os/commit/a30ff03acc27ff21a222af0e955bf6134691bc9b))


### Features

* add sync-pulse-conf.py to setup.sh, at the top of the run ([296940b](https://github.com/weirdtangent/pulse-os/commit/296940b40e3eeb8474df35744062dc7ce9d3ae5a))
* add volume feedback thump-thump ([4408566](https://github.com/weirdtangent/pulse-os/commit/440856668b914e23bb196683820310946faae10a))

## [0.23.2](https://github.com/weirdtangent/pulse-os/compare/v0.23.1...v0.23.2) (2025-11-19)


### Bug Fixes

* improve pulse.conf.sample and sync-pulse-conf.py ([6ffa4ef](https://github.com/weirdtangent/pulse-os/commit/6ffa4ef624c194ee47c884db94d9423eb1677725))

## [0.23.1](https://github.com/weirdtangent/pulse-os/compare/v0.23.0...v0.23.1) (2025-11-19)


### Bug Fixes

* remove XDG_RUNTIME_DIR from pulse-kiosk-mqtt.service ([ead61fd](https://github.com/weirdtangent/pulse-os/commit/ead61fd66421e8ddaafb1ecb4d199ed7d78c36ac))

# [0.23.0](https://github.com/weirdtangent/pulse-os/compare/v0.22.2...v0.23.0) (2025-11-19)


### Bug Fixes

* (or undo fix) for expanding the UID ([ef694d6](https://github.com/weirdtangent/pulse-os/commit/ef694d69bbb4a03d3188eeaadcbbf42ed4bc520e))
* add bt-autoconnect.sh to setup script ([c0528a3](https://github.com/weirdtangent/pulse-os/commit/c0528a3a71404e56f77d7915c4908276232a0390))
* add logging to pulse.audio for easier debugging ([e5d8c48](https://github.com/weirdtangent/pulse-os/commit/e5d8c48236877ded9f32397e78f0e5db676df2a0))
* add missing set -a and set +a to pulse.conf in wrappers ([9e765bd](https://github.com/weirdtangent/pulse-os/commit/9e765bd7db4d356971c9c6df5a1d489487766473))
* add voice assistant Python dependencies to setup script ([524cdc2](https://github.com/weirdtangent/pulse-os/commit/524cdc26819f99c2b445b0746187e8e1d1e6ecb3))
* ensure XDG_RUNTIME_DIR is set in pulse.audio ([1efbf1a](https://github.com/weirdtangent/pulse-os/commit/1efbf1ad468ec56418c87db2e416fe2b7d4d739b))
* fix pulse-assistant.py import problems with our own audioop module ([b5a6590](https://github.com/weirdtangent/pulse-os/commit/b5a6590d1f0fe479c4c868984baef08dc6586c9f))
* fix SemVer config ([412d3a9](https://github.com/weirdtangent/pulse-os/commit/412d3a98576a2cbde813dd7c7ce29a0ad1a53a51))
* python3-audioop does not exist in Debian, use python3 meta-package instead ([15aa838](https://github.com/weirdtangent/pulse-os/commit/15aa8387f66d23e8ffd2493c72a3129e3b25ea27))
* reformatting happiness ([67d6fcd](https://github.com/weirdtangent/pulse-os/commit/67d6fcd47f62982c517d9df173c8d8215299f164))
* set XDG_RUNTIME_DIR in kiosk-mqtt-wrapper.sh ([615d166](https://github.com/weirdtangent/pulse-os/commit/615d16689927e4fd06f71a99cc0d015f5c3f28a8))
* XDG_RUNTIME_DIR in bt-autoconnect.sh and pulse-backlight-sun.service ([b3ef445](https://github.com/weirdtangent/pulse-os/commit/b3ef4455397cd87155f1abeb73e948cffe2274eb))


### Features

* add bluetooth-speakers.md and improve setup script ([d4a6f63](https://github.com/weirdtangent/pulse-os/commit/d4a6f6366473cf593058e51dc93e0fdc55d8547a))
* add safe reboot guard to prevent infinite reboot loops ([4245a59](https://github.com/weirdtangent/pulse-os/commit/4245a5951c50e0aa4132578d40b72df1e9d96b14))
* add voice assistant support, attempt 1 ([5560f95](https://github.com/weirdtangent/pulse-os/commit/5560f95c77b96fe179e99f615cefc1318e25c957))

## [0.22.2](https://github.com/weirdtangent/pulse-os/compare/v0.22.1...v0.22.2) (2025-11-18)


### Bug Fixes

* improve section header detection in sync-pulse-conf.py ([035e491](https://github.com/weirdtangent/pulse-os/commit/035e49107735aab6603a4de69023cbd5d55ff0a0))

## [0.22.1](https://github.com/weirdtangent/pulse-os/compare/v0.22.0...v0.22.1) (2025-11-18)

# [0.22.0](https://github.com/weirdtangent/pulse-os/compare/v0.21.1...v0.22.0) (2025-11-18)


### Bug Fixes

* drop to more reasonable volume restoration time ([7fde61d](https://github.com/weirdtangent/pulse-os/commit/7fde61dace8418df2f2170416a79adaebf998305))


### Features

* add audio volume control in MQTT ([eb4df59](https://github.com/weirdtangent/pulse-os/commit/eb4df59df5ecc16c1c4082f7d0bfc868e9de06b9))
* add screen brightness control to kiosk-mqtt-listener ([cd63d44](https://github.com/weirdtangent/pulse-os/commit/cd63d44365856fcda84a2032dd312eb9b4e122a6))

## [0.21.1](https://github.com/weirdtangent/pulse-os/compare/v0.21.0...v0.21.1) (2025-11-18)


### Bug Fixes

* wait longer for volume restoration to avoid "Connected" announcement ([11c1bc2](https://github.com/weirdtangent/pulse-os/commit/11c1bc23bdc1eefca0082d20477c3d0b87aa3a45))

# [0.21.0](https://github.com/weirdtangent/pulse-os/compare/v0.20.0...v0.21.0) (2025-11-18)


### Features

* improve bluetooth speaker connection stability and volume management ([b08cf0c](https://github.com/weirdtangent/pulse-os/commit/b08cf0c3822a4f8dba70fb7a0d94cbdd90aa2983))

# [0.20.0](https://github.com/weirdtangent/pulse-os/compare/v0.19.1...v0.20.0) (2025-11-18)


### Features

* add bluetooth mute on shutdown (and restore volume after connection) ([a9ae5ef](https://github.com/weirdtangent/pulse-os/commit/a9ae5ef81954f3e1e54371b4c209e13da89d3f97))

## [0.19.1](https://github.com/weirdtangent/pulse-os/compare/v0.19.0...v0.19.1) (2025-11-18)


### Bug Fixes

* ignore generated remote-log.conf file ([3c24fa0](https://github.com/weirdtangent/pulse-os/commit/3c24fa07ace62fb9d12286d04bcca2eb617d83f5))

# [0.19.0](https://github.com/weirdtangent/pulse-os/compare/v0.18.1...v0.19.0) (2025-11-18)


### Features

* add keepalive to bt-autoconnect and improve troubleshooting docs ([a771a34](https://github.com/weirdtangent/pulse-os/commit/a771a349913999a70a3deff0ddc733fc0caa5554))

## [0.18.1](https://github.com/weirdtangent/pulse-os/compare/v0.18.0...v0.18.1) (2025-11-18)


### Bug Fixes

* update to use correct default Wyoming server ports ([ad41f30](https://github.com/weirdtangent/pulse-os/commit/ad41f30129e8e28597b3b00e83f12eeaf33b7750))

# [0.18.0](https://github.com/weirdtangent/pulse-os/compare/v0.17.3...v0.18.0) (2025-11-18)


### Features

* add voice assistant configuration, first steps ([fbe5ff9](https://github.com/weirdtangent/pulse-os/commit/fbe5ff98c049ec25fec7dd17ab6771d5262987e5))

## [0.17.3](https://github.com/weirdtangent/pulse-os/compare/v0.17.2...v0.17.3) (2025-11-18)


### Bug Fixes

* add missing semicolon to remote-log.conf.template ([2dcc684](https://github.com/weirdtangent/pulse-os/commit/2dcc684c359620ed25974355d546aac01be42a36))

## [0.17.2](https://github.com/weirdtangent/pulse-os/compare/v0.17.1...v0.17.2) (2025-11-18)


### Bug Fixes

* filter out syslog-ng stats from remote logging ([3beb501](https://github.com/weirdtangent/pulse-os/commit/3beb501aa493058c2d024ea0d150a1877644490d))

## [0.17.1](https://github.com/weirdtangent/pulse-os/compare/v0.17.0...v0.17.1) (2025-11-18)


### Bug Fixes

* use simpler title for setup summary notification ([51b3e85](https://github.com/weirdtangent/pulse-os/commit/51b3e85769630c1ab207fd95f7b73176d6c34894))

# [0.17.0](https://github.com/weirdtangent/pulse-os/compare/v0.16.0...v0.17.0) (2025-11-18)


### Features

* simplify summary formatting ([8aefe97](https://github.com/weirdtangent/pulse-os/commit/8aefe970a2f46ca8f9cfbb995f142f5511d083e4))

# [0.16.0](https://github.com/weirdtangent/pulse-os/compare/v0.15.0...v0.16.0) (2025-11-18)


### Features

* add sample HA automation for setup summary ([9b167d2](https://github.com/weirdtangent/pulse-os/commit/9b167d2597774ef14e30ff9609992ec3416d650f))

# [0.15.0](https://github.com/weirdtangent/pulse-os/compare/v0.14.2...v0.15.0) (2025-11-18)


### Features

* add kv_block function to print_feature_summary ([6a39e14](https://github.com/weirdtangent/pulse-os/commit/6a39e147a0718243107969d731e3bf2670086d1d))

## [0.14.2](https://github.com/weirdtangent/pulse-os/compare/v0.14.1...v0.14.2) (2025-11-17)


### Bug Fixes

* use -s to send the entire summary as a single payload ([ac6130c](https://github.com/weirdtangent/pulse-os/commit/ac6130ced6397607f6f5acf01d47d13acf35d139))

## [0.14.1](https://github.com/weirdtangent/pulse-os/compare/v0.14.0...v0.14.1) (2025-11-17)


### Bug Fixes

* use -m to send the entire message as a single payload ([0924c92](https://github.com/weirdtangent/pulse-os/commit/0924c92739967998aa7c6231157a7b7e6de9e733))

# [0.14.0](https://github.com/weirdtangent/pulse-os/compare/v0.13.0...v0.14.0) (2025-11-17)


### Features

* add summary to MQTT (if using) and log ([339f58c](https://github.com/weirdtangent/pulse-os/commit/339f58cb46d36c140da473200cc8e6cb5b23f350))

# [0.13.0](https://github.com/weirdtangent/pulse-os/compare/v0.12.0...v0.13.0) (2025-11-17)


### Features

* include better summary of config in setup.sh ([5e32374](https://github.com/weirdtangent/pulse-os/commit/5e32374edc608140ad04e36b4c1a8a94f6cfd5bd))

# [0.12.0](https://github.com/weirdtangent/pulse-os/compare/v0.11.0...v0.12.0) (2025-11-17)


### Features

* add global tap handler to pulse-photo-card; force hard load of Home ([ca67ce8](https://github.com/weirdtangent/pulse-os/commit/ca67ce86581395be444e0e6f3f0f15011c7b97a2))

# [0.11.0](https://github.com/weirdtangent/pulse-os/compare/v0.10.0...v0.11.0) (2025-11-17)


### Bug Fixes

* don't prefix entity IDs with "pulse_" ([5e68d30](https://github.com/weirdtangent/pulse-os/commit/5e68d30ec69e54ae2cf467426bedfa0539b84912))
* include hostname in entity IDs (duh) ([e323ba4](https://github.com/weirdtangent/pulse-os/commit/e323ba4c6c809c773d8ecfb92efb8fc1c2e21fe0))


### Features

* add latest version to Mqtt ([d22e069](https://github.com/weirdtangent/pulse-os/commit/d22e069f03adba203f8cb7e530d3ac4dd874b40b))

# [0.10.0](https://github.com/weirdtangent/pulse-os/compare/v0.9.0...v0.10.0) (2025-11-17)


### Features

* add reference docs for Home Assistant photo frame, MQTT buttons, and troubleshooting ([64b7ed1](https://github.com/weirdtangent/pulse-os/commit/64b7ed1b6c920e1805d95de55d8d3f6774237788))

# [0.9.0](https://github.com/weirdtangent/pulse-os/compare/v0.8.0...v0.9.0) (2025-11-16)


### Features

* add diagnostic telemetry sensors for Pulse ([0967096](https://github.com/weirdtangent/pulse-os/commit/0967096b050679369660fd25a2dcf9d9cb8ade80))

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
