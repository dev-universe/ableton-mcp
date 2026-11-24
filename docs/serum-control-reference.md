# Edit Serum 2 processor controls

These tools use the installed Serum 2 or Serum 2 FX 2.1.5 on macOS. Each
edit runs in a separate VST3 process and returns an ID for the verified
state. Use that ID as the base for a later edit. See the
[processor probe build instructions](serum-preset-interoperability.md#run-the-processor-probe).
Put the probe at `~/.cache/ableton-mcp/serum-host` or set
`ABLETON_MCP_SERUM_HOST` to its absolute path.

## Read and write a control

1. Call `get_serum_control_state` with `variant="instrument"` or
   `variant="fx"`. It returns the available state sections and effect names.
2. Pass a path to inspect a section. Use zero-based indices in native names
   such as `Oscillator0` and `LFO0`.
3. Call `edit_serum_state` with the same variant and paths in `edits.values`.
   Each entry has a `path` array and a `value`. The tool returns an error
   when the processor omits or changes a requested value. Readback tolerates
   small floating-point differences. Some schema-11 defaults disappear from
   the readback, including centered oscillator pan, B/C switched off, zero
   macros and mod wheel, and the two default EQ shelves. Verified omissions
   appear under `native_defaults` on edits and as `source="native_default"`
   on individual reads. Omitting `base_state_id` starts at Init.

These controls were exercised against the installed processor. Values in
`plainParams` use Serum's state units. The units vary by control.

| Control | Tested path or field |
| --- | --- |
| Oscillator A level | `Oscillator0 → plainParams → kParamVolume` |
| Filter 1 enable and cutoff | `VoiceFilter0 → plainParams → kParamEnable` and `kParamFreq` |
| Filter 1 type | `VoiceFilter0 → plainParams → kParamType`, for example `MgL24` |
| Envelope 1 attack and release | `Env0 → plainParams → kParamAttack` and `kParamRelease` |
| LFO 1 rate and shape | `LFO0 → plainParams → kParamRate` and `LFO0 → curveData` |
| Macro 1 value | `Macro0 → plainParams → kParamValue` |
| LFO 1 to oscillator A level | `ModSlot0` with source `[6, 0]`, destination type `Oscillator`, module ID `0`, parameter ID `1`, parameter name `kParamVolume`, and `plainParams.kParamAmount` |
| Arpeggiator | `Arp0.activeClip`, `Arp0.plainParams.kParamEnabled`, and `ArpClip0.clip` |
| MIDI clip notes | `MidiClip0.clip.notes` and `MidiClip0.clip.regionEndBeats` |

Processor serialization omits macro names, clip names, and standalone clip
activation in the tested cases. The tool reports those requested fields as
errors. MIDI clip note records use `noteNum`, `timeStamp`, and
`length` with times in beats, plus `attributes`, `channel`, and
`expressionEvents`. Inspect the state before editing a clip.

## Use an asset or an FX chain

Use `list_serum_assets` to search for an installed wavetable, sample, or
multisample. Pass a returned `relative_path` in
`edits.oscillator_assets`, select oscillator 1–3, and choose
`wavetable`, `sample`, `granular`, `spectral`, or `multisample`.
Multisamples use SFZ.

Use `edits.effect_chains` to replace the module list in a named bus
(`main`, `bus_1`, or `bus_2`). Order the entries as they should process.
The response includes the verified order and values. With the initial FX
patch, ordinary audio input reaches the Main rack. Processor routing and
the numbered bus send levels control how signals reach the two other
racks.

```json
{
  "variant": "fx",
  "edits": {
    "effect_chains": [
      {
        "bus": "main",
        "modules": [
          {"module": "distortion", "parameters": {"kParamDrive": 45}},
          {"module": "equalizer", "parameters": {"kParamFreq2": 450, "kParamType2": 2}}
        ]
      }
    ]
  }
}
```
