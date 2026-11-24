# Serum 2 preset interoperation

These checks used Serum 2 and Serum 2 FX 2.1.5 on macOS. The probes loaded
the user's installed VST3 bundle on arm64. The editor ran in a separate
Cocoa VST3 host.

## Change the processor state

A new processor exposes its state with `IComponent::getState`. The probe
changed specific fields in that state, then applied it with
`IComponent::setState`. It read the resulting state and rendered the new
instance to confirm that the changes took effect.

- **Serum 2.** Oscillator A loaded `Analog/Basic Shapes.wav`. A route from
  LFO 1 to oscillator A volume survived readback with its amount and shape.
- **Serum 2 FX.** A synthetic Distortion and Equalizer chain survived
  readback in both orders. Changing the order changed the audio with the
  same 48 kHz input.

## Save and reopen in the editor

The editor wrote a full `.SerumPreset` file for each variant. Both used
version 11 and their own product names, `Serum2` and `Serum2FX`. The FX
editor saved the Equalizer before the Distortion.

Separately, the probe assembled a `.SerumPreset` from an observed
processor state. It used the corresponding product, version 11, preset
metadata, and a fresh checksum. Each file opened through **Menu → Load
Preset** in a newly initialized editor. The instrument retained its
wavetable, route, and curve. The generated FX file placed the Distortion
before the Equalizer. Fresh headless renders of the editor readbacks
matched the corresponding reference renders byte for byte, with
96,000 stereo frames at 48 kHz in each comparison.

The processor state and preset both use an `XferJson` header, JSON metadata,
a length and codec pair, and a Zstandard-compressed CBOR map in the tested
build. The hash is MD5 of the compressed frame. Processor state uses
`component: "processor"`, and the native file uses
`fileType: "SerumPreset"` and preset metadata. The processor rejected both
factory and generated native preset files through `setState`. The editor
accepted both generated files through its file loader. The successful
conversion covers these test patches, build, and asset references. See the
[Xfer preset save procedure](https://xferrecords.com/web-manual/serum-2/saving-changes)
and [Xfer preset load procedure](https://xferrecords.com/web-manual/serum-2/loading-a-serum-preset).

## Run the processor probe

Use the public [Steinberg VST 3 SDK](https://github.com/steinbergmedia/vst3sdk).
Set `SERUM_TEST_SDK` to the directory containing `pluginterfaces`. Compile
and run the probe from the repository root. Use a scratch path for each
output.

```sh
xcrun clang++ -std=c++17 -Wall -Wextra -I "$SERUM_TEST_SDK" \
	-framework CoreFoundation tools/serum_host.cpp -o /tmp/serum-host
plug=/Library/Audio/Plug-Ins/VST3/Serum2.vst3
test_dir=$(mktemp -d)
# A dash selects the plug-in's initial state.
"/tmp/serum-host" "$plug" instrument - "$test_dir/init.state" "$test_dir/init.f32"
"/tmp/serum-host" "$plug" instrument "$test_dir/init.state" "$test_dir/copy.state" "$test_dir/copy.f32"
cmp "$test_dir/init.state" "$test_dir/copy.state"
```

The audio output is interleaved little-endian float32 at 48 kHz, with two
channels and 96,000 frames. The instrument input is a one-second MIDI note
with one second for the release. The FX input is a stereo three-tone
signal followed by silence. Pass a dash for the audio path to read or
normalize processor state without rendering.
