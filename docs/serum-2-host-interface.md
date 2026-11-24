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

## Processor and state probes

A VST3 host probe at 48 kHz called both processors for eight blocks of 256
samples. Serum 2 received a VST3 note-on event. Serum 2 FX received a stereo
sine wave. Both processors returned finite, nonzero output on arm64 and under
Rosetta. The probe checked the default patch in each variant.

For each new processor, the probe read its state through `IComponent::getState`,
restored the same bytes with `setState`, and read the state again. The initial
and final bytes matched for Serum 2 (1,258 bytes) and Serum 2 FX (1,244 bytes)
on both architectures. This exercise covers the public stream interface and
the tested default states. [Steinberg VST3 processor reference](https://steinbergmedia.github.io/vst3_doc/vstinterfaces/classSteinberg_1_1Vst_1_1IAudioProcessor.html).

The two captured VST3 states begin with the null-terminated `XferJson` marker.
An unsigned 64-bit little-endian length precedes the JSON metadata. Two 32-bit
little-endian words follow the metadata. The first matches the decompressed
length, and the second is `2` in both fixtures. Zstandard and an independent
CBOR decoder read the remainder as a frame containing a map with 164 entries
in each fixture. Each captured chunk also matched its Rosetta counterpart
byte for byte. The on-disk preset format and compatibility across releases remain unverified.

The runtime probe found the same virtual method addresses for both variants
within each architecture after subtracting the loaded image base. REA traced
the arm64 processor and state methods to distinct internal functions. For
the processor function at `0x63d60`, REA reported 104 call sites without a
resolved destination. The value-flow view also reached its configured limit.

| VST3 method | arm64 image offset | x86_64 image offset |
| --- | --- | --- |
| `IAudioProcessor::process` | `0x75c28` | `0x88730` |
| `IComponent::setState` | `0x610cc` | `0x6c010` |
| `IComponent::getState` | `0x63830` | `0x6e800` |
| `IEditController::getParameterInfo` | `0x10081c` | `0x128f10` |

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
runtime evidence from the VST3 host probe. The entry-point analysis covered the host factory and lifecycle functions.

## Native function coverage

LLVM disassembly and Mach-O function-start lists were captured for all six
slices. Ghidra 12.1.4 completed auto-analysis for each slice. The x86_64 VST3
import needed a second run after the first 180-second timeout. The totals
below count distinct function entry addresses reported by Ghidra across the
runs for each slice. Mach-O starts are a separate count from the binary's
`LC_FUNCTION_STARTS` command.

| Format | Architecture | Mach-O starts | Ghidra entries | Ghidra pseudocode | Ghidra unavailable |
| --- | --- | ---: | ---: | ---: | ---: |
| VST3 | arm64 | 27,017 | 27,995 | 27,980 | 15 |
| VST3 | x86_64 | 27,122 | 27,748 | 27,722 | 26 |
| AU | arm64 | 510 | 736 | 736 | 0 |
| AU | x86_64 | 523 | 703 | 703 | 0 |
| AAX | arm64 | 965 | 1,135 | 1,134 | 1 |
| AAX | x86_64 | 966 | 1,092 | 1,091 | 1 |

Craft Anything reported 27,974 arm64 and 30,498 x86_64 VST3 function
candidates. Its lists include every Mach-O function start in both VST3
slices. It produced pseudocode for the 41 VST3 entries in the Ghidra unavailable
column. Ghidra and Craft Anything use different discovery and
decompilation rules, including inferred function boundaries.

The pseudocode and disassembly provide static evidence about the analyzed
builds. The complete semantics of the DSP, editor, and preset operations
remain unverified.

## Live browser behavior

Live 12.4.15 returned the exact names `Serum 2` and `Serum 2 FX` under both
`plugins/VST3/Xfer Records` and `plugins/AUv2/Xfer Records`. Each item had a
Live browser URI, with `is_loadable=true` and `is_device=false`. Use
`is_loadable` to identify entries that Live can load.
