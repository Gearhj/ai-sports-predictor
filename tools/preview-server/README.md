# Test-mode preview server (127.0.0.1:5199)

Copies of the files that run the AISP test-mode preview (scheduled task "AISP Preview 5199", started with the
production venv's Python). The live copies are in `C:\Users\james\aisp-preview`; keep the two in step.

- `preview_server.py`: serves the client build named in `current_dist.txt`, proxies GET `/api/*` to 8010, refuses
  every write, and adds read-only `/preview-api/*` feeds: MLB and football live, NBA/NHL live (`court_live.py`),
  multi-sport scores, CFB logos, the UFC archive (`ufc_archive.py`), insights for Beat the Close / Market Movers /
  Model Lab (`insights_db.py`, read-only SQL), and Ask the Booth (the only POST; it writes nothing).
- No credentials live in these files: the Booth key is read from the Windows registry at run time.
