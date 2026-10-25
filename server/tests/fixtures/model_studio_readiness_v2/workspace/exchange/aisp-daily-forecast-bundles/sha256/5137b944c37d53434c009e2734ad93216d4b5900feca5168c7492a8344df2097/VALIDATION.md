# ASP Sports Forecast Exchange V1 validation

1. Resolve only the exact bundleLogicalUri named by the import fixture.
2. Hash canonical bundle.json bytes and require the URI, fixture, and directory address to agree.
3. Reject duplicate JSON keys, unknown fields, invalid distributions, or invalid child content IDs.
4. Require NFL, 2026, RESEARCH_SHADOW, NOT_PROMOTED, and executionAllowed=false.
5. Verify exact canonical event/team IDs, pre-kickoff cutoffs, model manifest/binary hashes, and feature-vector hash.
6. This evidence authorizes no provider call, wager, model promotion, or official-bankroll mutation.
