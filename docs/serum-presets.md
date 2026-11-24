# Save and load Serum presets

The preset tools support Serum 2 and Serum 2 FX 2.1.5 on macOS. Start with a
verified `state_id` from `edit_serum_state` or `import_serum_preset`.

## Native Serum files

- `save_serum_preset` accepts `variant`, `state_id` and `details`. The details
  include a required `name` and optional `author`, `description`, `comment`,
  and `tags`. The tool saves a new `.SerumPreset` and returns its absolute path.
- `import_serum_preset` takes the matching `variant` and absolute `path`. It
  publishes the processor readback as an editable state. `normalized_sections`
  reports any native sections changed or omitted by the processor, including
  editor preferences. `source_product_version`, `source_schema_version`, and
  `migrated` identify the input and whether Serum upgraded its processor state.
  Some presets activate clips on load. `preserved_preset_clips` lists those
  indices when MCP carries them forward after the processor omits them from
  its readback. The processor readback itself remains the basis for
  `normalized_sections`.
- Native files default to `~/Library/Application Support/AbletonMCP/Serum/Presets`.
  Set `ABLETON_MCP_SERUM_PRESETS` to use another directory. Files with the same
  name are preserved. Choose a new name for a revision.

To include original audio inside the preset, add an item to
`edit_serum_state.edits.embedded_assets` with `oscillator`, `mode`, and the
absolute `wav_path`. Sample, granular, and spectral modes accept mono or stereo
16-bit PCM WAV at 8–192 kHz and an optional `base_note` (MIDI 60 by default).
Wavetable mode uses mono WAV in 2048-sample cycles, up to 256 cycles. Each
asset is limited to 524,288 PCM samples. Factory assets selected from the
installed library retain their relative path. Multisample SFZs also remain
library references.

## Revise a Factory preset

1. Search installed presets with `list_serum_factory_presets`. Use an optional
   `variant` and search by preset name or category, such as `Pad`. Entries
   include the product variant and an absolute path for import.
2. Call `import_serum_preset` with the entry's variant and path. The native
   Serum 2.1.5 processor migrates the Factory versions listed below.
3. Edit the returned state with `edit_serum_state` and render it with
   `render_serum_audio`. Save it with `save_serum_preset` under a new name.

| Product | Source build | Source schema |
| --- | --- | --- |
| Serum 2 | 2.0.11 | 3, 4 |
| Serum 2 | 2.0.12 | 4, 5 |
| Serum 2 | 2.0.13 | 5, 6 |
| Serum 2 | 2.0.14, 2.0.15 | 6 |
| Serum 2 FX | 2.0.13 | 5 |

The import validates the preset container and version before sending its
contents to the native processor. Unsupported older presets can be opened
and resaved through the Serum 2.1.5 editor first. Factory media remains
referenced in the installed library.

## Load through Ableton Live

1. Call `export_serum_live_preset` with `variant`, `state_id`, `name`, and
   `plugin_format` (`vst3` by default, or `au`). This writes a `.vstpreset` or
   `.aupreset` to `Serum MCP Presets` inside the Live User Library.
2. Once Live indexes the file, call `load_serum_live_preset` with the same
   variant, name, and format plus the target `track_index`. Use a MIDI track
   for the instrument, and an audio signal chain for Serum 2 FX.
3. Check the chain with `get_track_info`. `load_requested` means Live accepted
   the browser request. Avoid repeating a successful request if the new device
   is still initializing.

The default Live User Library is `~/Music/Ableton/User Library`. Set
`ABLETON_MCP_LIVE_USER_LIBRARY` to the path shown in Live's Library Settings if
it has been moved. Exporting to the User Library does not edit the active set. When AU restores
its inactive, unnamed default pitch quantizer, it serializes the default
scale as Major with the quantizer still inactive.

Build the optional AU export helper on macOS and set `ABLETON_MCP_SERUM_AU_HOST`
to the executable's absolute path, or put it in `~/.cache/ableton-mcp`.

```sh
mkdir -p ~/.cache/ableton-mcp
xcrun clang++ -std=c++17 -O2 -fobjc-arc tools/serum_au_preset.mm \
  -framework AudioToolbox -framework Foundation -o ~/.cache/ableton-mcp/serum-au-preset
```

Live 12.4.15 verification loaded a new VST3 instrument with an embedded
wavetable, a new AU instrument with an embedded sample, and new FX instances in
both formats. Presets saved back from Live retained the original embedded
frames or ordered FX parameters. Replaying the captured processor state in
the probe yielded identical audio for all four cases. Native file round trips
also covered granular and spectral assets after moving the original WAV.

References: [Serum embedded content](https://xferrecords.com/web-manual/serum-2/embedding-content-when-saving-a-preset),
[Live AU preset handling](https://help.ableton.com/hc/en-us/articles/209775145-AU-preset-handling),
and the [Steinberg VST3 preset format](https://steinbergmedia.github.io/vst3_dev_portal/pages/Technical%2BDocumentation/Locations%2BFormat/Preset%2BFormat.html).
