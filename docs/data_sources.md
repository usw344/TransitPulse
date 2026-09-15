# TransitPulse cross-city data source register

Every source adopted for the multi-city approximation program is recorded here
before an adapter is written against it. Third-party aggregators (Transitland,
MobilityDatabase, transit enthusiast sites) may be used to *discover* a source
but must never silently become training ground truth: only the agency's own
published dataset is authoritative.

Verification status legend:

- **VERIFIED** — the URL was fetched in this repository and returned the stated
  artifact; the licence and publisher were read from official metadata.
- **RECORDED** — metadata read from the official portal, artifact not yet fetched.
- **CANDIDATE** — discovered but not yet confirmed; do not build an adapter.

Last verification pass: 2026-09-12 (six cities added; see section 3-8).
Licence and source-link check: 2026-09-12 final pass — see
[Licence and source verification](#licence-and-source-verification-2026-09-12-final-pass)
at the end of this file, which supersedes the per-city "RECORDED" licence notes
where they differ.

---

## 1. Edmonton — Edmonton Transit Service (ETS)

| Field | Value |
| --- | --- |
| City | Edmonton, Alberta, Canada |
| Agency | Edmonton Transit Service (ETS), City of Edmonton |
| Status | **VERIFIED** — in use by this application |
| Static GTFS | `https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip` |
| GTFS-RT vehicle positions | `http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb` |
| GTFS-RT trip updates | `http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/TripUpdate/TripUpdates.pb` |
| GTFS-RT alerts | `http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Alert/Alerts.pb` |
| Open data portal | `https://data.edmonton.ca/` |
| Licence | Open Government Licence — Edmonton (`https://data.edmonton.ca/stories/s/City-of-Edmonton-Open-Data-Terms-of-Use/msh8-if28/`). Royalty-free, commercial use permitted, attribution and inclusion of the terms URL required on redistribution. |
| Update frequency | Static: irregular. Realtime: polled by this application every 30 s. |
| Temporal coverage held locally | Recorded GTFS-RT history from 2026-09-07 onward (the REPLAY availability endpoint, `/api/history/availability`, reports current counts). Static feed is a single current snapshot. |
| Geographic detail | Stop-level coordinates, shapes, stop_times. |
| Route identifiers | `route_id` as published (e.g. `004`); `trips.direction_id` populated (0/1) — confirmed against the local database. |
| Join method | GTFS `route_id` → `trips` → `stop_times` → `stops`, within one `static_feed_id`. |
| Useful fields | Route geometry (shapes), stop spacing, scheduled runtime, headways, service calendars, direction_id, block_id (86,414 of 86,754 trips carry a block). |
| Limitations | **No ridership of any kind.** Vehicle positions are not passenger demand. Realtime arrival estimates are publisher predictions, not measured dwell/departure ground truth. Recorded history is sparse and discontinuous. |

## 2. Calgary — Calgary Transit

| Field | Value |
| --- | --- |
| City | Calgary, Alberta, Canada |
| Agency | Calgary Transit, The City of Calgary (Operational Services) |
| Status | **VERIFIED** — static GTFS download confirmed; no adapter written yet |
| Static GTFS dataset | `https://data.calgary.ca/Transportation-Transit/Calgary-Transit-Scheduling-Data/npk7-z3bj` |
| Static GTFS download | `https://data.calgary.ca/download/npk7-z3bj/application%2Fx-zip-compressed` → `CT_GTFS.zip`, 11,940,942 bytes, HTTP 200 (fetched 2026-09-11) |
| GTFS-RT vehicle positions | dataset `am7c-qe3u` — `https://data.calgary.ca/Transportation-Transit/Calgary-Transit-Realtime-Vehicle-Positions-GTFS-RT/am7c-qe3u` |
| GTFS-RT trip updates | dataset `gs4m-mdc2` |
| GTFS-RT service alerts | dataset `jhgn-ynqj` |
| Licence | Open Calgary Terms of Use (`https://data.calgary.ca/d/Open-Data-Terms/u45n-7awa`). Attribution: "The City of Calgary". |
| Update frequency | Static: *irregular* (per portal metadata). Realtime: refreshed every 30 s per publisher description. |
| Temporal coverage | Static: current snapshot only. No local history recorded yet. |
| Geographic detail | Standard GTFS — stop coordinates, shapes, stop_times. |
| Route identifiers | Calgary Transit route numbers; bus plus CTrain light rail (`route_type` distinguishes them). |
| Join method | Same GTFS joins as Edmonton. Cross-city joins are made on the **normalized schema**, never on raw route ids, which are not comparable between agencies. |
| Useful fields | Route geometry, stop spacing, scheduled runtime, headways, service span, route_type. |
| Limitations | **Route-level ridership is NOT published.** Confirmed against dataset metadata: `nypk-snzd` (Monthly Ridership By Year) and `n9it-gzsq` (Yearly Ridership) are **system-level only**, dimensioned by fare sub-group and fare product with **no route dimension**; `3u7d-jj5c` is CTrain *station* annual boardings, which is rail station-level and not bus route-level. Static GTFS-RT feed endpoints are Socrata dataset pages; the direct protobuf endpoint must be resolved and verified before a recorder adapter is built (a direct `api/views/.../files/...` fetch returned HTTP 403). |

---

## Ridership position for the current program

GTFS contains no ridership, and vehicle positions are not passenger demand.
**No adopted city** — all eight, not only the two documented above this line —
supplies comparable **route-level** boardings, and none was sought or used:

- Edmonton: none found in the configured sources.
- Calgary: system-level and rail-station-level only, no route dimension.
- The six agencies in section 3-8: no ridership was requested, fetched or
  adopted; the dataset carries zero ridership values (`qa_report.json.ridership`).

Per the program's own rule, the first approximation model therefore **proceeds
without ridership**. Ridership stays an optional later feature, and any future
source must have its measured quantity documented exactly — boardings,
passenger trips, unlinked trips, linked trips, APC counts and fare validations
are **not** interchangeable and must not be pooled into one column.

## Criteria for adopting a further system

Six more agencies were adopted on 2026-09-12 and are documented in section 3-8
below; the register now covers **eight** cities. Any further candidate must be
chosen for **data quality**, not to raise the city count, and needs its own row
here before an adapter is written. At minimum it must publish an official static
GTFS feed with `shapes.txt`, `stop_times.txt` and populated `direction_id`, under
a licence permitting derived analytical use.

### Multi-operator feeds

Three adopted feeds carry more than one operator, and every row is stamped with
**its own route's** `agency_id`, not the feed's first `agency.txt` entry:

- **Edmonton** — the ETS feed also carries St. Albert Transit (63 rows),
  Strathcona County Transit (44), Leduc Transit (8), Beaumont Transit (6),
  Fort Saskatchewan Transit (4) and Spruce Grove Transit (2). These are regional
  partners in the Edmonton metropolitan network. The SCENARIOS picker names the
  operator wherever it is not ETS.
- **Portland** — TriMet plus Portland Streetcar (12 rows).
- **Seattle** — King County Metro plus Sound Transit (32 rows) and
  City of Seattle (12).

Labelling these with the feed-level agency attributed 127 Edmonton-region rows,
32 Sound Transit rows and 12 Portland Streetcar rows to the wrong operator.

---

## 3-8. Cities adopted for the cross-city model (2026-09-12)

Six further agencies were adopted so that leave-one-city-out validation has
enough cities to mean anything: two systems can only ever be trained on one and
tested on the other, which is not evidence of generalization. Every entry below
was fetched by `scripts/fetch_gtfs_sources.py`, which records the URL used, the
retrieval time, the byte count and the SHA-256, and then reads the agency name,
timezone, calendar window and route-type mix back **out of the archive itself**
so a mislabelled registry entry cannot go unnoticed. The machine-readable half
of this register is `apps/api/transitpulse_ml/city_registry.py`.

**Licence status (updated 2026-09-12 final pass):** five licences were read and
confirmed (Vancouver, Montreal, Winnipeg, Portland, Seattle). Edmonton's and
Calgary's terms pages could not be fully read, and Minneapolis publishes no
licence that could be found. See
[Licence and source verification](#licence-and-source-verification-2026-09-12-final-pass).
The original note recorded at adoption follows.

**Licence status at adoption: RECORDED, not VERIFIED.** The download URL and byte count were
confirmed by fetching each archive in this repository. The licence names and URLs
below were taken from each publisher's developer/open-data page as recorded in
the registry; the licence text itself was **not** re-read at adoption. Before
any redistribution of derived data, re-read each licence and promote these to
VERIFIED. Nothing in this project redistributes the source archives: they live in
git-ignored `artifacts/gtfs_sources/` and only aggregate route-level measurements
are persisted.

### Vancouver — TransLink (South Coast British Columbia Transportation Authority)

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | CA |
| Static GTFS download | `https://gtfs-static.translink.ca/gtfs/google_transit.zip` |
| Developer / dataset page | `https://www.translink.ca/about-us/doing-business-with-translink/app-developer-resources/gtfs/gtfs-data` |
| Licence | TransLink Open Data / Developer Terms of Use (`https://www.translink.ca/about-us/doing-business-with-translink/app-developer-resources`) |
| Archive | 16,158,470 bytes |
| SHA-256 | `B3D9B732FEFBD1180C565FC6466FCED6F8FCB9DD0D2BEA3E7EA41361A3963921` |
| Retrieved | 2026-09-12T05:49:32.650497+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | TransLink |
| Feed timezone | America/Vancouver |
| Calendar window | 2026-09-07 → 2027-01-03 |
| Route types in feed | type 1: 3, type 2: 1, type 3: 233, type 4: 1, type 715: 1 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Montreal — Société de transport de Montréal (STM)

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | CA |
| Static GTFS download | `https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip` |
| Developer / dataset page | `https://www.stm.info/en/about/developers` |
| Licence | STM Developers — open data licence (`https://www.stm.info/en/about/developers`) |
| Archive | 42,928,416 bytes |
| SHA-256 | `1B8E59076EE50773E80BCA33DD5F4F95F24BD3D0EE6283FFB62337F5BF8EB0DA` |
| Retrieved | 2026-09-12T05:49:55.252807+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | Société de transport de Montréal |
| Feed timezone | America/Montreal |
| Calendar window | 2026-06-15 → 2026-10-25 |
| Route types in feed | type 1: 4, type 3: 207 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Winnipeg — Winnipeg Transit

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | CA |
| Static GTFS download | `https://gtfs.winnipegtransit.com/google_transit.zip` |
| Developer / dataset page | `https://api.winnipegtransit.com/` |
| Licence | City of Winnipeg Open Data Licence (`https://data.winnipeg.ca/open-data-licence`) |
| Archive | 4,430,153 bytes |
| SHA-256 | `DBC971C447439C3F1761F1D5DEE0BFB1DBCA4081CC0E8ED0145AC8BE0B0FD0A6` |
| Retrieved | 2026-09-12T05:49:57.992661+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | Winnipeg Transit |
| Feed timezone | America/Winnipeg |
| Calendar window | 2026-09-06 → 2026-12-12 |
| Route types in feed | type 3: 72 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Portland — TriMet

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | US |
| Static GTFS download | `https://developer.trimet.org/schedule/gtfs.zip` |
| Developer / dataset page | `https://developer.trimet.org/GTFS.shtml` |
| Licence | TriMet Developer Terms / open data (`https://developer.trimet.org/terms_of_use.shtml`) |
| Archive | 29,521,107 bytes |
| SHA-256 | `82E6B822DE008367B3D3D35BB52545807EA41B6F56623A9AA4B1162B3D54671B` |
| Retrieved | 2026-09-12T05:50:19.742524+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | Portland Streetcar |
| Feed timezone | America/Los_Angeles |
| Calendar window | 2026-08-23 → 2026-11-28 |
| Route types in feed | type 0: 8, type 2: 1, type 3: 71, type 6: 1 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Minneapolis — Metro Transit (Minnesota)

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | US |
| Static GTFS download | `https://svc.metrotransit.org/mtgtfs/gtfs.zip` |
| Developer / dataset page | `https://www.metrotransit.org/developers` |
| Licence | Metro Transit / MetCouncil open data (`https://www.metrotransit.org/developers`) |
| Archive | 19,320,738 bytes |
| SHA-256 | `DCF08D3806490CEDE537690B4C8E80A048D603C2F98304F31BCF913471198A73` |
| Retrieved | 2026-09-12T05:50:30.818131+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | Metro Transit |
| Feed timezone | America/Chicago |
| Calendar window | 2026-09-05 → 2026-10-23 |
| Route types in feed | type 0: 3, type 3: 123 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Seattle — King County Metro Transit

| Field | Value |
| --- | --- |
| Status | **RECORDED** — archive fetched and parsed; licence text not re-read at adoption |
| Country | US |
| Static GTFS download | `https://metro.kingcounty.gov/GTFS/google_transit.zip` |
| Developer / dataset page | `https://kingcounty.gov/en/dept/metro/rider-tools/mobile-and-web-apps` |
| Licence | King County open data (`https://kingcounty.gov/en/legal/open-data-license`) |
| Archive | 10,797,519 bytes |
| SHA-256 | `1F1334F7855E7CD5644FC7FFA9BAD2B71E29A9D2A6D844AB43AC14371B30E450` |
| Retrieved | 2026-09-12T05:50:36.220573+00:00 (file mtime (archive adopted, not fetched by this script)) |
| Agency declared in feed | Metro Transit |
| Feed timezone | America/Los_Angeles |
| Calendar window | 2026-08-29 → 2027-12-31 |
| Route types in feed | type 0: 2, type 3: 141, type 4: 2 |
| Ridership | **Not used.** No route-level boardings were sought or adopted for any city. |

### Known caveats on these six

- **TriMet (Portland)** and **TransLink (Vancouver)** publish multi-agency feeds.
  `agency.txt`'s first row is not necessarily the principal operator — TriMet's
  archive declares *Portland Streetcar* first — so the registry's `agency` field
  is what this project asserts and `declared_agency` is what the file says. Rows
  from these feeds describe the metropolitan system, not one legal operator.
- **King County Metro (Seattle)** declares a calendar window running to
  2027-12-31, far longer than the others; representative service dates are still
  chosen from the first ordinary week on or after the reference date.
- Route-type mixes differ: light rail, streetcar and commuter rail are present in
  several feeds. They are normalized with a `route_kind` and the modelling cohort
  admits **bus only**. Rail is never pooled with bus.
- Static snapshots only. No GTFS-Realtime is recorded for any city but Edmonton.

### Sources considered and rejected

Probed and not adopted; recorded here so the same checks are not repeated:

| Candidate | Result |
| --- | --- |
| OC Transpo (Ottawa) | `https://www.octranspo.com/files/google_transit.zip` → HTTP 404 |
| RTD (Denver) | `https://www.rtd-denver.com/files/gtfs/google_transit.zip` → HTTP 308 redirect, not followed |
| SFMTA (San Francisco) | `https://gtfs.sfmta.com/transitdata/google_transit.zip` → connection failed |
| Halifax Transit | published path → HTTP 404 |

None were pursued further: the dataset already had eight good systems, and the
programme rule is to prefer data quality over agency count.

---

## Licence and source verification (2026-09-12, final pass)

A focused check, not legal advice. Every static GTFS download URL was requested
again (HTTP HEAD with redirects followed; nothing downloaded). Each licence
page was then fetched and read. Where a page could not be read, or states no
licence, the table says so rather than guessing.

| City / publisher | Download URL | Licence or terms — what was actually read | Attribution |
| --- | --- | --- | --- |
| Edmonton — City of Edmonton / ETS | works (HTTP 200, zip) | **Open Government Licence – Edmonton.** Only the page title could be read; the terms body renders client-side and was **not re-read** this pass. | Not re-read. The original register entry records attribution plus the terms URL on redistribution. |
| Calgary — City of Calgary | works (HTTP 200, via the Socrata file endpoint) | **Current wording not confirmed.** The official terms page (`data.calgary.ca/…/u45n-7awa`) renders client-side and could not be read. The only full text found is a third-party copy (Open Definition) of the City's older *Open Data Catalogue Terms of Use*: a non-exclusive, worldwide licence to use, modify and distribute. Web search refers to the current terms as *Open Government Licence – City of Calgary*. | The older text encourages crediting The City of Calgary but does not require it. Current requirement **unconfirmed**. |
| Vancouver — TransLink | works (HTTP 200) | **TransLink GTFS terms of use** (read on TransLink's GTFS data page): limited, revocable, non-exclusive licence to use, reproduce and redistribute. TransLink may add terms or fees for commercial users who charge end users. | Legend **required**, prominently displayed: "Route and arrival data used in this product or service is provided by permission of TransLink. TransLink assumes no responsibility for the accuracy or currency of the Data used in this product or service." |
| Montreal — STM | works (HTTP 200, zip) | **Creative Commons Attribution 4.0 (CC BY 4.0)**, read at `https://www.stm.info/en/about/developers/terms-use`. Restriction: **metro** schedules may not be used to build apps based on metro timetables (trip-duration estimation is allowed). TransitPulse models **bus** routes only. | Cite the STM as source. |
| Winnipeg — Winnipeg Transit | works (HTTP 200, zip) | **Open Government Licence – Winnipeg**, read at `https://data.winnipeg.ca/open-data-licence`: copy, modify, publish, adapt and distribute for any lawful purpose. | "Contains information licensed under the Open Government Licence – Winnipeg." |
| Portland — TriMet | works (HTTP 200, zip) | **TriMet developer terms of use**, read at `https://developer.trimet.org/terms_of_use.shtml`: limited, revocable licence to use, reproduce, redistribute and display the data, provided as-is. | No attribution clause for the data. TriMet trademarks may not be used in a confusing way. |
| Minneapolis — Metro Transit | works (HTTP 200, zip) | **UNCLEAR — no licence found.** The recorded page `https://www.metrotransit.org/developers` now returns HTTP 404. The current GTFS landing page (`https://svc.metrotransit.org/`) was read and states no licence or terms, and a web search found none. | Unknown. Credit Metro Transit; do not redistribute derived data from this feed until clarified. |
| Seattle — King County Metro | works (HTTP 200, zip) | **King County Transit Data Terms of Use**, read on `https://kingcounty.gov/en/dept/metro/rider-tools/mobile-and-web-apps` (the recorded `…/legal/open-data-license` URL now returns HTTP 404): limited, revocable licence to use, reproduce and redistribute. | Legend **required**, prominently displayed: "Transit scheduling, geographic, and real-time data provided by permission of King County". |

**What TransitPulse redistributes.** Nothing from these feeds in raw form. The
source archives live in git-ignored `artifacts/gtfs_sources/` and are excluded
from milestone snapshots; the committed code and docs contain only aggregate,
derived route measurements quoted in documentation. The project is a
non-commercial portfolio piece.

**If the derived dataset (`artifacts/datasets/…`) is ever published**, first
resolve the Minneapolis licence and re-read the Edmonton and Calgary terms,
include the TransLink and King County legends and the Winnipeg statement, and
credit the other agencies as above. The README's *Data sources and credits*
section carries the required legends for the application itself.

**Basemap.** The map draws CARTO vector tiles built from OpenStreetMap data;
the in-app attribution control ("© CARTO, © OpenStreetMap contributors") is
shown whenever the basemap renders, and screenshots reused elsewhere should keep
that credit.

