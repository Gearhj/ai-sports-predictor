# NBA fixtures — locally authored, not provider output

Every file here was written by hand in this repository. **None of it came from
SportsDataIO**, scrambled or otherwise, and no provider request has ever been
made for NBA data from this project.

The team keys, player names and numbers are fictional placeholders shaped like
the documented `Game`, `BoxScore`, `TeamGame` and `PlayerGame` objects so that
the parsers, identity crosswalk, idempotency and revision rules can be exercised
end to end before anybody is entitled to make a request.

Anything ingested from these files is written with `data_mode = 'FIXTURE'` on
every row, enforced by a CHECK constraint. A fixture row cannot become `REAL` by
being re-imported, and no MLB or NFL table is touched.
