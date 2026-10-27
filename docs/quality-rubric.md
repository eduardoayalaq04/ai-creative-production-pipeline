# Media QA rubric

This rubric is for human/agent-assisted production. The mock runner does not evaluate media semantics.

## Image acceptance

- Identity and facial traits match the approved anchor.
- Clothing, microphone, accessories, product, and location remain consistent unless a change is explicitly intended.
- Camera angle and framing match the storyboard, not merely the anchor pose.
- Hands and required props are anatomically/plausibly rendered.
- No accidental text, logos, or watermarks. Readable UI should be composited in editing where necessary.
- Dimensions and aspect ratio are checked programmatically.

## Video acceptance

- Spoken text is complete and matches approved copy; no repetition or truncated ending.
- Voice/accent remains consistent across the sequence.
- Visual motion respects the intended scene and product state.
- CTA gestures work at early, middle, and late timestamps, not only in a thumbnail.
- Visual continuity includes room layout; do not mirror the anchor arbitrarily.
- Probe duration and codecs; inspect representative frames and final seconds.

## Dialogue review

Transcribe each voiced clip with Whisper, compare against approved dialogue, and inspect suspicious segments. A transcription mismatch is not proof of faulty speech: re-check with a stronger model and listening before paying for a reroll. If speech is genuinely cut off, allow more duration or revise wording with approval.

## Final edit

Determine cut points from actual speech and silence. Remove pre-speech clicks/noises and dead air without removing opening words. Verify the assembled transcript after trimming. Apply modest speed changes only when suitable for the approved creative; preserve natural delivery. Review the final encoded file, not just its sources.

## Correction policy

Reject the smallest failing artifact. Use versioned replacements rather than overwriting approved assets. A failed hand gesture may need a corrected source still before video regeneration. Record why a reroll occurred; do not count provider completion as creative acceptance.
