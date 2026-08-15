# Open Data Attribution and Public-Use Contract

**Gate:** `3C-FD1`

**Rights review date:** 2026-08-14

This document freezes the canonical attribution text and deployment controls
for open data considered by ASP. The Retrosheet postal-form discrepancy remains
an explicit public-release blocker below; this document does not claim that
supplemental string is frozen. It does not publish any source archive, authorize
a provider call, or replace legal review. Terms must be revalidated against the
exact source revision and service tier before public or commercial deployment.

The FD1 controlled acquisition is complete for the five bundles below. These
identities are included so future derived artifacts can bind the correct
notice to the exact evidence revision; they are not public download links.

| Evidence bundle | Source-version ID | Frozen revision / rights result |
| --- | --- | --- |
| Retrosheet master CSV through 2025 | `64db602a-090a-4e37-b8f2-2a48d36dd7f2` | Content-addressed official archive; exact Retrosheet notice required |
| Retrosheet auxiliary identities/logs | `fef044cc-35e1-499a-8971-a8b29cd9dd7c` | Content-addressed official files; exact Retrosheet notice required |
| Retrosheet 2021-2025 events/boxes | `9a08b5fd-e5ed-4043-a984-04021584f15e` | Content-addressed annual official archives; exact Retrosheet notice required |
| Chadwick Persons Register | `4a4956de-0769-4bb0-9dc4-cc30515ba16a` | Commit `2e8e73355f9c77b963115377bd98c784cfeec10f`; ODC-By 1.0 |
| SABR Lahman | `3af682f0-ecdb-47d8-8e14-660ec5f7455c` | Version 2025; CC BY-SA 3.0 confirmed in acquired README |

The frozen warehouse-readiness contract binds attribution to these exact
registry source keys and already documented versions:

| Registry source key | Bound evidence / frozen version |
| --- | --- |
| `CHADWICK_REGISTER_ARCHIVES` | Chadwick Persons Register / `4a4956de-0769-4bb0-9dc4-cc30515ba16a` / commit `2e8e73355f9c77b963115377bd98c784cfeec10f` |
| `RETROSHEET_AUXILIARY_DOWNLOADS` | Retrosheet auxiliary identities/logs / `fef044cc-35e1-499a-8971-a8b29cd9dd7c` / through 2025 |
| `RETROSHEET_CSV_DOWNLOADS` | Retrosheet master CSV / `64db602a-090a-4e37-b8f2-2a48d36dd7f2` / through 2025 |
| `SABR_LAHMAN_BOX_DOWNLOADS` | SABR Lahman / `3af682f0-ecdb-47d8-8e14-660ec5f7455c` / Version 2025 |

NOAA LCDv2 and Open-Meteo remain documented policy candidates but contributed
no downloaded bytes in FD1. Retrosheet supplied the required weather fields
for the selected 2021-2025 game rows, so neither gap-fill condition activated.

## Non-negotiable publication rules

1. Attribution follows the evidence. If a forecast, chart, API response,
   research table, or model was built from an attributed source, the relevant
   notice must remain visible and machine-associated with that output.
2. ASP must not imply that Retrosheet, Chadwick Baseball Bureau, SABR, Sean
   Lahman, Seamheads, NOAA/NCEI, Open-Meteo, or any underlying weather service
   endorses ASP, its forecasts, or its betting analysis.
3. Raw ZIP, CSV, event, weather, and identity archives are local governed
   evidence. ASP will not expose or mirror them publicly even where an upstream
   license could permit redistribution.
4. ASP-derived forecasts are separate from source data. Attribution identifies
   the evidence used; it does not label a forecast as an official publisher
   product or transfer ASP's forecast authorship to the source.
5. A public surface must show source attribution at the point of use or through
   a conspicuous, directly reachable attribution panel. A source name hidden
   only in developer documentation is insufficient.
6. Every output must bind source version/revision, acquisition timestamp,
   payload checksum, attribution-policy version, and transformation summary.

## Retrosheet

### Governing official sources

Retrosheet's current [canonical use notice](https://www.retrosheet.org/notice.txt)
permits recipients to transfer, sell, give away, or build commercial products
from its data, but requires a prominent statement. Current
[CSV downloads](https://retrosheet.org/downloads/csvdownloads.html) and
[event files](https://www.retrosheet.org/game.htm) cover data through 2025 and
report the website updated 2026-08-09.

### Exact required attribution — preserve verbatim

The following block is copied verbatim from the official canonical notice. Do
not modernize punctuation, replace the quoted URL, add ASP inside the block, or
collapse it into a generic source list:

> The information used here was obtained free of
> charge from and is copyrighted by Retrosheet.  Interested
> parties may contact Retrosheet at "www.retrosheet.org".

**Placement:** display this block prominently on the public attribution panel
and on any downloadable Retrosheet-derived research artifact. A short inline
label may link to that panel, but the required block itself must remain readily
visible.

**Source-page discrepancy:** some current Retrosheet download pages print a
postal-address variant while the linked canonical `notice.txt` uses the
website-address text above. The applicable postal string is not reproduced in
this contract and must not be described as already preserved verbatim. Before a
future public implementation, retrieve that exact text from the applicable
official download page, preserve it with its page identity/retrieval timestamp
and checksum, and show it with the canonical block above. If the product cannot
prove and display both, public release of the affected Retrosheet-derived output
is blocked. Local download and research are not blocked because the scope of
permitted use is consistent across the pages.

**Other duties:** retain Retrosheet copyright/provenance metadata, identify the
source release/checksum, do not promise that Retrosheet guarantees accuracy,
and never suggest endorsement.

## Chadwick Baseball Bureau Persons Register

### License and revision

The official [Persons Register repository](https://github.com/chadwickbureau/register)
states that the public database is made available under the
[Open Data Commons Attribution License v1.0](https://opendatacommons.org/licenses/by/1-0/).
The revision reviewed for acquisition is commit
[`2e8e73355f9c77b963115377bd98c784cfeec10f`](https://github.com/chadwickbureau/register/commit/2e8e73355f9c77b963115377bd98c784cfeec10f).
A live official-remote query on 2026-08-14 resolved `HEAD` to
`refs/heads/master`, with both names at that commit and no release tags. The
older 2026-07-01 commit shown by a cached web index is not the acquired
revision.

ODC-By 1.0 permits commercial use, copies, adaptations, and derivative
databases, subject to its conditions. Public conveyance of a database or
derivative database must include the license or URI and retain applicable
notices. Public use of a produced work requires a notice that makes viewers
aware that its content came from the licensed database.

### ASP attribution notice

Use this notice, with both names linked when the medium supports links:

> Contains information from the Chadwick Baseball Bureau Persons Register, which is made available under the ODC Attribution License.

Link `Chadwick Baseball Bureau Persons Register` to
`https://github.com/chadwickbureau/register` and `ODC Attribution License` to
`https://opendatacommons.org/licenses/by/1-0/`.

Also disclose the pinned commit SHA and whether ASP changed, merged, or filtered
the identity records. Never imply that Chadwick confirms an ASP-created
ambiguous match. UUID history and unresolved/conflicting IDs must remain
auditable.

## SABR Lahman Baseball Database

### Included license confirmed

The official [SABR release page](https://sabr.org/lahman-database/) identifies
Version 2025 (statistics through 2025), released on the public page 2026-01-02
with CSV byte-order-mark updates on 2026-02-18. Its official
[2025 release documentation](https://sabr.box.com/shared/static/qtgh1olzcaauz5x234wqx8huixizff8l.txt)
states an internal release date of 2025-12-10 and confirms that the database is
copyright 1996-2025 by SABR following Sean Lahman's donation and licensed under
[Creative Commons Attribution-ShareAlike 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/).
The exact official README response is `readme2025.txt`, Box shared-static
locator `qtgh1olzcaauz5x234wqx8huixizff8l`, `47,982` bytes, SHA-256
`8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab`.
No stable numeric Box item ID was exposed by that response; none is inferred.

This is no longer `LICENSE_CONFIRMATION_REQUIRED` for the package's modern MLB
tables: the included license has been verified. Preserve the public-page dates,
README date, release identity, and downloaded byte checksum because the public
metadata documents more than one revision event.

### ASP attribution notice

Use the following notice for an ASP artifact that incorporates Lahman evidence:

> Includes data from the SABR Lahman Baseball Database 1871–2025, created by Sean Lahman and published by SABR, under CC BY-SA 3.0. ASP transformations are identified separately.

Link the database name to `https://sabr.org/lahman-database/` and the license to
`https://creativecommons.org/licenses/by-sa/3.0/`. Identify material
modifications. If ASP publicly conveys a modified/derivative database, the
share-alike obligation must be satisfied with the exact distribution design;
do not assume that a repository code license alone covers the data artifact.

### Seamheads / Negro Leagues separation

SABR's release page says the Negro Leagues portion is licensed from
Seamheads. The package-level README does not reproduce a separate Seamheads
grant or redistribution notice. Therefore:

- retain Negro Leagues rows in a separately identified population;
- do not mix them into the 2021-2025 MLB forecasting target;
- do not publish or commercially redistribute that subset until its embedded
  rights and attribution are reviewed independently; and
- do not represent package-level CC BY-SA verification as a separate finding
  about the underlying Seamheads source.

Public/commercial release of that subset is a hard stop until this review is
complete.

## NOAA/NCEI Local Climatological Data Version 2

### Dataset identity and citation

The source is NOAA/NCEI
[Local Climatological Data, Version 2](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC01689),
edition Version 2, dataset identifiers `NCEI DSI 2075_02`,
`gov.noaa.ncdc:C01689`, and `local-climatological-data-v2`. The official
metadata records publication on 2023-11-01, daily updates, and metadata last
modified 2025-11-25T14:30:17.

Use this bibliographic citation, filling in the exact subset and access date:

> Kantor, D.; Casey, N. W.; Menne, M. J.; Buddenberg, A. (2023). *Local Climatological Data (LCD), Version 2*, [station/year subset]. NOAA NCEI. https://doi.org/10.25921/jp3d-3v19. Accessed [date].

For each ASP weather row, also retain station ID, station coordinates, park
coordinates, distance, observation timestamp, time delta from the target game,
quality flags, station-year file URL, acquisition timestamp, and file checksum.

**FD1 execution result:** no NOAA request or station-year download was made.
The citation and row-level lineage requirements above become active only if a
future governed Retrosheet-weather gap requires NOAA evidence.

NOAA/NCEI provides the data without a warranty of accuracy, reliability, or
completeness. ASP must not imply NOAA endorsement, claim NOAA data as its own,
or present ASP-modified material as official NOAA material. Any file-specific
restriction or third-party annotation overrides the general government-data
assumption and must stop that file pending review.

## Open-Meteo

### Hosted-service boundary

Open-Meteo's official [terms](https://open-meteo.com/en/terms) permit the free
hosted API only for non-commercial use. The free tier is limited to 600 calls
per minute, 5,000 per hour, 10,000 per day, and 300,000 per month. The terms
specifically treat subscription or advertising-supported apps, commercial
products/promotional activity, and undisclosed research at commercial entities
as commercial use. Open-Meteo's
[pricing page](https://open-meteo.com/en/pricing) says a paid subscription
provides the commercial-use license and customer endpoint.

Therefore the free hosted Historical Weather API is approved only as a local,
private, non-commercial research fallback after Retrosheet and NOAA. It is not
approved for an eventual subscription, advertising-supported, promotional, or
otherwise commercial ASP product. Commercial use of free-tier-derived evidence
is a hard stop; moving to a paid plan requires a new immutable source-use
decision, not a configuration toggle.

**FD1 execution result:** no Open-Meteo request was made and no Open-Meteo
evidence entered any ASP population.

### Data license and attribution

Open-Meteo's [license page](https://open-meteo.com/en/license) offers API data
under CC BY 4.0, requiring appropriate credit, a license link, and an indication
of changes. This data license does not override the hosted free-tier
non-commercial restriction.

For permitted local research reports, use:

> Weather data by Open-Meteo.com under CC BY 4.0; model/reanalysis source and ASP transformations are identified with the result.

Link Open-Meteo to `https://open-meteo.com/` and the license to
`https://creativecommons.org/licenses/by/4.0/`. For scholarly material, the
official Historical Weather API page also supplies this citation:

> Zippenfenig, P. (2023). *Open-Meteo.com Weather API* [Computer software]. Zenodo. https://doi.org/10.5281/ZENODO.7970649.

Preserve the query, selected model, coordinates, time range, units, retrieval
timestamp, response checksum, and whether ASP interpolated, converted, or
otherwise modified the response.

## Existing local and prohibited acquisition sources

### Existing local Statcast and MLB evidence

Existing governed local archives may be processed only under their existing
local-research decision. No new automated MLB/Baseball Savant collection is
authorized. A future lawfully obtained export must enter through the manual
file seam with its own source identity, acquisition context, checksum, and
rights decision. Do not attach an open-data attribution from Retrosheet,
Chadwick, Lahman, NOAA, or Open-Meteo to make MLB/Savant evidence appear open.

### The Odds API

Existing prospective snapshots retain their provider and subscription-tier
lineage. The official [v4 API documentation](https://the-odds-api.com/liveapi/guides/v4/)
states that historical odds are a paid-plan feature. Gate 3C-FD1 is a free-data
phase: it does not acquire historical odds, infer missing lines, or represent
current/prospective snapshots as historical pregame coverage.

## Future public attribution footer contract

Any future public ASP UI or exported report that uses these sources must
implement the following contract. This is a design requirement, not evidence
that a public deployment is currently approved.

| Contract field | Requirement |
| --- | --- |
| `source_name` | Human-readable publisher/dataset name, never an ASP-only alias |
| `source_revision` | Release, commit SHA, dataset edition, or response identity |
| `source_url` | Official publisher page, not a mirror |
| `license_name` / `license_url` | Exact applicable license or use notice |
| `required_notice` | Verbatim Retrosheet block or source-specific attribution text |
| `evidence_scope` | Which output fields/families used the source |
| `transformation_notice` | Filtering, joins, unit conversion, imputation, aggregation, or model derivation |
| `public_use_status` | `APPROVED_WITH_ATTRIBUTION`, `RESEARCH_ONLY`, or `BLOCKED` |
| `no_endorsement` | Always true for third-party/government sources |
| `raw_download_exposed` | Always false |

The rendered footer/panel must behave conditionally:

- include the verbatim Retrosheet block whenever Retrosheet-derived evidence is
  present;
- include Chadwick ODC-By notice and pinned commit when its identity mappings
  are used;
- include SABR/Lahman and CC BY-SA notice for Lahman-derived evidence and flag
  any distributed derivative-database share-alike obligations;
- include the exact NOAA LCDv2 subset citation and station lineage for NOAA
  weather;
- mark Open-Meteo free-tier evidence `RESEARCH_ONLY` and suppress it from
  commercial/public output; and
- link to an evidence manifest that separates source observations from the ASP
  forecast, model version, and transformation.

## Release blocker checklist

A public artifact is blocked if any answer below is no:

- Is every source revision/checksum bound to the artifact?
- Is the required notice visible and linked to the correct official source?
- Are changes and transformations disclosed?
- Are raw archives inaccessible to public routes and object storage?
- Is Retrosheet's notice discrepancy handled with the dual-notice policy?
- Are Lahman Negro Leagues/Seamheads rows excluded or separately cleared?
- Is Open-Meteo free-tier evidence absent from commercial output?
- Is NOAA attribution present without implying endorsement?
- Are MLB/Savant automated acquisition and historical-odds fabrication still
  disabled?
