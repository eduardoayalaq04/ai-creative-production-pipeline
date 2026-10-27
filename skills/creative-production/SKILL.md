---
name: creative-production
description: Direct approval-gated AI image/video ad production from a marketing brief, preserving continuity and reviewing media before delivery.
---

# Approval-gated AI creative production

## Use when

A marketing brief needs a character-led ad, product demonstration, UGC-style creative, or multi-scene video. This is a portable agent playbook; provider calls and semantic QA require separate tools. The bundled CLI demonstrates only workflow state using mock records.

## Inputs

- Audience, product/service, offer, business objective, distribution format, CTA destination.
- Approved script or permission to draft one.
- Rights-cleared reference images and product information.
- Voice, accent, wardrobe, setting, and continuity requirements.
- Budget and explicit permission before paid generation.

## Procedure

1. Draft script and storyboard. Describe shot, source image, dialogue, action, voice, and duration per scene. Obtain approval before paid production.
2. Generate only the identity/style anchor first. Review realism, likeness, clothing, props, and setting; present the actual file and stop for approval.
3. Derive stills from the approved anchor, not an outdated reference. If the opening needs a special angle or pose, create a separate still. Repeat continuity-critical accessories in every prompt.
4. Review derived stills before video generation. Reject only failing compositions and preserve successful paid outputs.
5. Animate approved stills with one continuous take per clip unless otherwise directed. Keep the exact same voice description across scenes. Prompts may be English while spoken dialogue remains Spanish.
6. Capture provider task IDs and statuses. Download temporary results promptly. If a request times out after submission, reconcile the existing task before creating a new paid one.
7. Review duration, frames, dialogue, voice, product state, and CTA gestures. A provider's `completed` status does not equal creative approval.
8. Make versioned replacements only for failing assets. If a prop or gesture cannot be fixed through motion prompts, correct the source still first.
9. Trim to actual speech, assemble, and verify the final transcript and ending. Add complex text/UI in editing rather than relying on generated typography.
10. Deliver the files through the agreed channel and record costs by unique provider task ID, avoiding double counting.

## Stop conditions

- An approval gate is pending.
- Budget is exhausted or permission for paid work is missing.
- The reference or identity cannot be verified.
- Prior task state is ambiguous; reconcile instead of resubmitting.
- Asset publishing rights are unresolved.

## Quality checks

See `docs/quality-rubric.md` for continuity, dialogue, gesture, and edit criteria. Never present a mock record as rendered media, an automated check as human approval, or an unmeasured benefit as a proven business result.
