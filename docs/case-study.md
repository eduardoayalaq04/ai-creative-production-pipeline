# Case study: multi-scene AI service advertisement

## Context

Simplixy's production workflow needed to turn an approved Spanish-language script into a coherent vertical video advertisement. The concept used a realistic presenter in a warm creative office, multiple camera setups, and a closing gesture toward information below the ad.

This is a qualitative account of production decisions, not a performance or ROI study. The repository excludes private conversation logs, raw provider responses, credentials, and historical media pending publishing review.

## Challenge 1: an anchor is not automatically scene one

The approved anchor established identity, clothing, and office style. The opening called for a ceiling/high-angle view; simply reusing a front-facing anchor did not meet that direction.

**Decision:** separate identity approval from scene-composition approval. Derive a dedicated opening still from the anchor and review camera position explicitly before animation.

## Challenge 2: provider success is not usable dialogue

Generated clips could complete successfully while dialogue was rushed, incomplete, or pronounced poorly. Some scene variants needed longer supported durations or clearer wording.

**Decision:** transcribe and review voiced scenes before assembly. Regenerate only the affected scene and preserve previously approved assets. Treat transcription as a diagnostic, not an infallible judge.

## Challenge 3: editing needs content-aware cut points

Clip boundaries included pauses or incidental noises. A simple concatenation left avoidable dead time, while overly aggressive trimming risked removing words.

**Decision:** inspect silence and transcript timing, choose scene-specific trims, normalize segments, assemble a preview, and re-check the exported result. A historical editing script used FFprobe, FFmpeg silence detection, per-scene trims, and a 1.2x final variant. These editing operations are not implemented in the current reference CLI.

## Engineering lesson

A resumable state machine and explicit approval boundaries make the process inspectable. The reference runner demonstrates those concepts with mock records; it does not imply the historical workflow used this exact package.

## What can be measured next

For a future instrumented live implementation: unique provider task spend, time to approved asset, reroll reasons, dialogue pass rate, and human review time. No values are reported here because a comparable benchmark has not been established.
