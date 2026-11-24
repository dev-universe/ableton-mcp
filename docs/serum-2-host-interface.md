# Serum 2 host interfaces in Ableton Live

These observations cover Xfer Records Serum 2 and Serum 2 FX version 2.1.5 on
macOS. Live 12.4.15 discovered both variants through VST3 and AUv2.

## Plug-in identity

The universal `Serum2.vst3` bundle contains four VST3 classes. The instrument
and effect each have a processor class and an edit controller class. The
packaged `Contents/Resources/moduleinfo.json` and a call to the VST3
`IPluginFactory` agree on the class IDs.

| Variant | Processor class ID | Controller class ID |
| --- | --- | --- |
| Serum 2 | `56534558667350736572756D20320000` | `56534558667343736572756D20320000` |
| Serum 2 FX | `56534558667351736572756D20322066` | `56534558667344736572756D20322066` |

The universal `Serum2.component` registers two Audio Units in
`Contents/Info.plist`. Both use manufacturer `XFER` and factory
`AUWrapperFactory`. The same plist names `Serum2.vst3` as the VST3 plug-in.
Apple's `auval -v aumu Xf2X XFER` and `auval -v aumf Xf2Y XFER` both passed.

| Variant | AU type | AU subtype |
| --- | --- | --- |
| Serum 2 | `aumu` | `Xf2X` |
| Serum 2 FX | `aumf` | `Xf2Y` |

The local installation also contained a universal AAX 2.0.24 bundle. The Live
12 test covered VST3 and AUv2, the formats that Live indexed for these devices.

## Buses and automation

A host probe against the public VST3 interfaces initialized both processors
and edit controllers. The arm64 and x86_64 runs returned the same records.
The x86_64 run used Rosetta.

| Variant | Audio input | Audio output | Event buses | Parameters |
| --- | --- | --- | --- | --- |
| Serum 2 | None | One stereo bus | One input and one output | 2,623 |
| Serum 2 FX | One stereo bus | One stereo bus | One input and one output | 2,623 |

Each controller reported 2,623 unique parameter IDs and 541 descriptors with
the `kCanAutomate` flag. Three descriptor rows differed between the variants,
all in the normalized defaults. Parameter IDs and counts come from the host
probe and can vary by build. Live exposes the parameters configured for each
instance through its device panel. Use Live's parameter indices and ranges
when controlling a loaded instance through MCP. [Steinberg IEditController
reference](https://steinbergmedia.github.io/vst3_doc/vstinterfaces/classSteinberg_1_1Vst_1_1IEditController.html)
and [Ableton Live manual](https://www.ableton.com/en/live-manual/12/using-plug-ins/).

## Native entry points

The bundle executables contain arm64 and x86_64 Mach-O slices. The table lists
unslid addresses from the thinned binaries. REA with Ghidra 12.1.4 produced
pseudocode for the arm64 VST3 factory, entry, and exit. Ghidra headless
decompiled the AU factory on both architectures. Craft Anything 0.6.1's
built-in lifter produced independent factory pseudocode for both formats and
architectures.

| Format and exported name | arm64 | x86_64 |
| --- | --- | --- |
| VST3 `GetPluginFactory` | `0x192c` | `0x1740` |
| VST3 `bundleEntry` | `0x538a18` | `0x5df1b0` |
| VST3 `bundleExit` | `0x538b90` | `0x5df350` |
| AU `AUWrapperFactory` | `0x116b8` | `0x127c0` |

REA reached its 3,000-operation value-flow limit in the arm64 VST3 factory.
The correspondence between the class IDs and device names also has direct
runtime evidence from the VST3 host probe. The native analysis covers the
host entry points. Internal DSP, preset encoding, and editor state remain
outside the verified coverage.

## Live browser behavior

Live 12.4.15 returned the exact names `Serum 2` and `Serum 2 FX` under both
`plugins/VST3/Xfer Records` and `plugins/AUv2/Xfer Records`. Each item had a
Live browser URI, with `is_loadable=true` and `is_device=false`. Use
`is_loadable` to identify entries that Live can load.
