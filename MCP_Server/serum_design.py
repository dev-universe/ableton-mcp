"""Authoring guidance for the MCP client that interprets a user's sound brief."""

SERUM_DESIGN_WORKFLOW = """Create or revise a real Serum preset when the user asks for one.

1. Translate the brief into a sound source, articulation, modulation, and effects as needed.
   Use variant instrument for a playable MIDI patch and fx for processing incoming audio.
   Start at Init for a new sound. For a revision, use the previous state_id, or import the
   named .SerumPreset and use its verified state_id. Inspect the relevant paths first.
2. Use list_serum_assets for installed oscillator sources. Use embedded_assets for an
   original PCM16 WAV when the brief needs a self-contained asset. Use edit_serum_state
   for native values and ordered effect chains. Read the tool's verified values. If a field
   is omitted or changed, inspect and correct it before treating that change as applied.
3. Prepare a repeatable audition suited to the brief. For a sub or bass, test lower MIDI
   notes. For a pad or keys, test sustained notes or a chord. For Serum FX use the requested
   local input when supplied. Render the patch with render_serum_audio. For a revision, use
   the same stimulus and the previous state as reference. Compare only metrics relevant to
   the request, such as attack, low-band fraction, stereo side energy, or tail level.
4. Refine any unmet, testable traits and rerender. Keep changes that improve the brief and
   preserve the other requested traits. If a quality judgment needs listening, use available
   audio playback or provide the WAV and report exactly what you measured. Do not claim that
   a metric alone proves a subjective tone, genre, or similarity to a recording.
5. Save the chosen verified state using save_serum_preset with a new name. Keep its state_id
   for follow-up revisions. When the user asks to place it in Live, use export_serum_live_preset
   and load_serum_live_preset for the requested track and format, then inspect get_track_info.
   Return the preset and audition paths and briefly describe the result in the user's language.
"""

SERUM_CONTROL_HINTS = """Native state names with zero-based indices.

- For oscillator A use Oscillator0, B uses Oscillator1, and C uses Oscillator2. In plainParams,
  kParamEnable is 0 or 1, kParamVolume is linear gain from 0–1, kParamPan is −50 to 50,
  kParamOctave is −4 to 4, kParamPitch is −12 to 12 semitones, and kParamFine is −100 to
  100 cents. The oscillator level knob displays the square root of gain as a percentage.
  kParamUnison is 1–16 voices and kParamDetune is 0–1. Use oscillator_assets with 1-based
  oscillator indices and an exact relative path from list_serum_assets. Each oscillator
  engine also has its own parameter map.
- Env0.plainParams is envelope 1. kParamAttack, kParamHold, kParamDecay, and kParamRelease
  are seconds. kParamSustain is 0–1.
- Filter 1 is VoiceFilter0.plainParams. kParamEnable enables it. kParamType accepts names
  such as MgL24. kParamFreq is logarithmic 0–1, about 120 Hz at 0.34 and 425 Hz at 0.5.
  kParamReso and kParamWet are 0–100. Check RoutingSlot0 and RoutingSlot1 for oscillator A
  and B. plainParams.kParamRoutingDest accepts kRoutingDestDirect or kRoutingDestFilter.
- LFO0 is LFO 1. Use kParamBeatSync=0 and kParamRate in Hz for an unsynced rate. Inspect
  curveData before editing its shape. Macro0.plainParams.kParamValue and
  Global0.plainParams.kParamModWheel are 0–100.
- An LFO 1 modulation of oscillator A level uses a new ModSlot with source [6, 0],
  destModuleTypeString Oscillator, destModuleID 0, destModuleParamID 1, destModuleParamName
  kParamVolume, and plainParams containing kParamAmount. Mod wheel uses source [1, 0].
  For LFOs 1–10 add the zero-based index to 6, and for macros 1–8 add it to 25.
  Inspect the occupied slots first.
- effect_chains takes a bus and an ordered list of modules. Passing a bus replaces that
  bus's chain. To adjust a module and keep the chain order, edit only its matching FX entry.
  Delay kParamTimeL and kParamTimeR are seconds when kParamBeatSync=0. kParamFeedback and
  kParamWet are percentages. Reverb kParamPreDelay is seconds, and kParamSize, kParamWidth
  and kParamWet are percentages. Equalizer kParamFreq1 and kParamFreq2 are Hz and the
  corresponding gain parameters are dB. kParamType1 and kParamType2 accept 1 for bell
  and 2 for cut, low-cut on band 1 and high-cut on band 2. Omitted type uses the shelf
  default. Distortion kParamDrive controls drive.
"""
