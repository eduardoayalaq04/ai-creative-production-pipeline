# Security and publication policy

- Use only the personal GitHub account and repository credentials for this project.
- No employer code, tokens, internal documentation, corporate assets, or work-system integrations belong here.
- Never commit `.env`, signed result URLs, raw provider responses, customer briefs, private chat transcripts, or local absolute paths.
- Run outputs and historical media are excluded by default. Review rights, consent, branding, and confidential information before adding showcase assets.
- Fictional demo prompts are not customer testimonials. Label AI-generated media and simulated output honestly.
- APIMart payload builders accept HTTPS reference URLs; they do not fetch them or guarantee that URLs are safe to download. A live adapter needs URL/network restrictions and safe download limits.
- CLI approval labels are not authentication. Do not expose this local runner as a public service.
- The demo has no network transport and spends no API credits.
- Code, configuration, tests and documentation are MIT-licensed; showcase videos and previews are excluded (see `examples/MEDIA_RIGHTS.md`). Keep this repository private until the owner approves public release.
- Use a trusted output tree with no symlinks, including parent path components. Symlinks are rejected before accesses, but an adversary concurrently swapping paths is outside the threat model (TOCTOU). On systems with symlinked temporary directories, select a physical path before starting a run.
- Completed mock records are validated against their expected schema/content and canonical JSON hash; approval hashes and dependency invariants are checked. Local write access can still forge the entire state; hashes are not authentication or cryptographic signatures.
- JSON is limited to 2 MiB, depth 32 and 100,000 nodes; campaigns are limited to 256 assets. Invalid UTF-8 and cyclic structures are rejected. Limits also apply to persisted metadata, so a large campaign can exhaust the output budget before the asset-count ceiling.
- Atomic replacement prevents partial JSON during ordinary interruptions, not guaranteed directory-entry durability after power loss.
- Public GitHub ownership and commit attribution identify the portfolio owner; they are not credentials. Historical noreply addresses are public GitHub attribution, not private contact emails.
