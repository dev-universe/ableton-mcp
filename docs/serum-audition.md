# Render and compare Serum sounds

`render_serum_audio` runs the actual Serum 2.1.5 processor in the
[local VST3 probe](serum-preset-interoperability.md#run-the-processor-probe).
Rebuild the probe from the current checkout for the audition options.
The tool returns a 48 kHz stereo 24-bit PCM WAV and audio measurements. It
uses the verified patch given by `state_id`, or Init when the ID is omitted.

For a synth audition, pass MIDI notes with `pitch` (0–127), `start` and
`length` in seconds, optional `velocity` (0–1), and optional `channel`
(0–15). Overlapping notes, sub-block offsets, and a tempo of 20–300 BPM are
supported. Without notes it plays MIDI 48. For Serum 2 FX, pass an absolute
`audition.audio_path` for a local mono or stereo PCM WAV. Other supported
input rates are resampled to 48 kHz on macOS. Without an input file, the
processor receives a short built-in stereo test signal. You may also send
MIDI notes to FX patches that respond to MIDI.

Set `audition.seconds` up to 30 seconds. When omitted, duration includes a
tail after note-off or the end of the FX input, within the render limit.
Use identical audition settings for before-and-after comparisons. Provide
`reference_state_id` to measure and render the previous patch in the same
call. The result includes both WAV paths and numeric deltas.

Measurements include peak and RMS in dBFS, output clipping and silence,
stereo side energy fraction, spectral centroid, and energy fractions in
five frequency bands. Synth attack measures the 10–90% RMS rise during the
first note or chord using 5 ms windows. Tail RMS starts at the final MIDI
note-off or the end of the FX input. These measurements establish acoustic
changes such as increased low-band energy or a slower attack. Listen to the
WAV when a request depends on perceptual qualities the metrics cannot score.

WAVs default to `~/Library/Application Support/AbletonMCP/Serum/Auditions`.
Set `ABLETON_MCP_SERUM_AUDITIONS` to choose another directory.
`audio_sha256` identifies the float processor output before PCM conversion.

In the Serum 2.1.5 verification, raising envelope 1 attack from 0.01 to
0.27 seconds raised the measured attack from 65 to 275 ms on the same
three-note phrase. On a resampled stereo drum WAV, distortion followed by
EQ changed the RMS by −2.615 dB relative to the empty rack without clipping.
