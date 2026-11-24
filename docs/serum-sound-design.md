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
