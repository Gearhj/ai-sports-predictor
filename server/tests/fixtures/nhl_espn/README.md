# ESPN-shaped NHL scoreboard fixtures (TEST ONLY -- invented scores)

These files model the response of ESPN's public NHL scoreboard
(`site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates=YYYYMMDD`):
`leagues`, `season`, `day` and `events`, each event with `competitions[0].competitors`
(`homeAway`, `winner`, `team.abbreviation`, `score` as a string, per-period `linescores`
of `{value, displayValue, period}`) and a `status` block (`period`, `type.name/state/completed/detail`).
The shape follows the event schema AISP's NFL and NBA ESPN collectors read; no retained ESPN
NHL response exists on this machine, so the NHL-specific details (club spellings LA/NJ/SJ/TB/UTAH,
`Final/OT` at period 4, `Final/SO` at period 5 with a period-5 linescore) are modelled, not
recorded. No request was made to build them.

Matchups and start times are the real 2026-27 NHL schedule rows (sportsdataverse
`nhl_schedule_2027.csv`, captured by Model Studio on 2026-10-10). **Every score, ESPN event
id and attendance figure is invented.** They are not results and must never be imported
anywhere but the in-memory test database.

| file | events |
|------|--------|
| `scoreboard_20261016.json` | PIT @ COL final in regulation; BOS @ ANA final in overtime (`Final/OT`, period 4) |
| `scoreboard_20261017.json` | NJ @ WSH final in a shootout (`Final/SO`, period 5); CAR @ WPG postponed; VAN @ TB in progress; SEA @ FLA final (no AISP game) |
| `scoreboard_20261020.json` | MTL @ TOR final in regulation (the paper-lifecycle fixture game) |
