# Describe a sound to your MCP client

Ask your MCP-capable assistant for a sound in your own words. The assistant
uses the `get_serum_design_workflow` tool or the `serum_sound_design` MCP
prompt, then edits the installed Serum engine, renders an audition, and saves
the chosen preset. The connected assistant interprets the brief. The MCP
server does not need a separate model API key.

For example, try “따뜻한 패드를 만들어줘. 두 손으로 코드를 눌렀을 때 부드럽게
올라오고, 손을 뗀 뒤에는 여운이 짧게 남게 해줘.” A follow-up such as
“방금 만든 프리셋의 어택을 조금 더 느리게 하고 다른 소리는 유지해줘” uses the
previous verified state. You can also provide the absolute path to a compatible
`.SerumPreset` as the starting point.

Include a format and track when you want the result placed into Live. For a
Serum 2 FX request, supply a local WAV if you want the chain evaluated on that
audio. The assistant can compare revisions using the same input, save separate
versions, and return the preset and WAV paths. Audition metrics help confirm
requested changes in dynamics, brightness, stereo spread, and tail length.

See [preset formats and embedded assets](serum-presets.md) and
[audition and measurement details](serum-audition.md).

## Korean brief verification

The installed Serum 2.1.5 instrument and FX were tested with Korean briefs
for sounds and follow-up changes. The assistant chose the sound design from
each brief and carried it out through a real stdio MCP session.

| Brief and revision | Measured result |
| --- | --- |
| Start from Init with a centered, slowly swelling intro pad. Narrow only the wide layer. | Two original wavetables were embedded. Oscillator B width moved from 83 to 67 and stereo side energy fell from 3.80% to 2.52%. The isolated center layer's audio was byte-identical and both envelopes stayed the same. Both versions crossed −80 dBFS about 0.98 s after note-off. |
| Keep a kick centered and add alternating eighth-note echoes at 120 BPM. Shorten only the decay of the repeats. | The direct hit stayed mono and within 0.11 dB RMS of the empty rack. The first two echoes retained their levels and the 250 ms left/right pattern remained. With a −70 dBFS measurement threshold, the final echo window moved from 1.035 s to 0.535 s, with direct onset at 0.035 s. |

The saved revisions were imported into fresh processors and rendered again
with identical output. The pad import was performed after moving the two WAV
sources out of their original paths. Live 12.4.15 also loaded the pad as VST3
and the kick effect as AU on temporary tracks. Their states captured from
Live preserved the targeted settings and produced byte-identical output when
replayed with the same stimuli in the audition host. The test tracks were
removed and the original Live set snapshot matched the baseline.
