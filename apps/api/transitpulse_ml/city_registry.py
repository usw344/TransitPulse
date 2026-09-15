"""The cities adopted into the cross-city approximation program.

Adding a city is meant to be a declaration, not a code change: register the
publisher's own download URL and licence here, fetch it with
``scripts/fetch_gtfs_sources.py``, and the shared normalizer in
``gtfs_normalizer`` produces the same schema for it as for every other city.

Two rules apply to everything in this file.

**Only the agency's own published dataset is authoritative.**  Aggregators may
be used to discover a feed but never become the source of record, because their
re-publication can lag, re-encode or silently repair the original.

**Licences are recorded, not assumed.**  ``licence`` names the terms the
publisher states and ``licence_url`` points at them, so a redistribution
question can be answered from the register rather than from memory.  Verified
status lives in ``docs/data_sources.md``; this module carries the machine-
readable half of the same register.

The archive checksum and retrieval time are deliberately *not* here — they
belong to a specific download, so they are read from the fetch manifest at
build time.  Hard-coding them would let the registry drift into claiming
provenance for bytes it never saw.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CityFeed:
    """One adopted agency's static GTFS feed."""

    source_id: str
    city: str
    #: Agency name as this project refers to it; the feed's own ``agency.txt``
    #: value is captured separately at extraction time so a mismatch is visible.
    agency: str
    download_url: str
    #: Human-facing dataset or developer page, for a reviewer to check.
    dataset_page: str
    licence: str
    licence_url: str
    country: str
    #: Basename used for the archive on disk.
    archive_name: str
    #: Set False only for a feed proven to lack usable direction_id values.
    has_direction_id: bool = True


CITY_FEEDS: tuple[CityFeed, ...] = (
    CityFeed(
        source_id="edmonton-ets",
        city="Edmonton",
        agency="Edmonton Transit Service",
        download_url="https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip",
        dataset_page="https://data.edmonton.ca/",
        licence="Open Government Licence — City of Edmonton",
        licence_url="https://data.edmonton.ca/stories/s/City-of-Edmonton-Open-Data-Terms-of-Use/msh8-if28/",
        country="CA",
        archive_name="edmonton_ETS_GTFS.zip",
    ),
    CityFeed(
        source_id="calgary-ct",
        city="Calgary",
        agency="Calgary Transit",
        download_url="https://data.calgary.ca/download/npk7-z3bj/application%2Fx-zip-compressed",
        dataset_page="https://data.calgary.ca/Transportation-Transit/Calgary-Transit-Scheduling-Data/npk7-z3bj",
        licence="Open Calgary Terms of Use",
        licence_url="https://data.calgary.ca/d/Open-Data-Terms/u45n-7awa",
        country="CA",
        archive_name="calgary_CT_GTFS.zip",
    ),
    CityFeed(
        source_id="vancouver-translink",
        city="Vancouver",
        agency="TransLink (South Coast British Columbia Transportation Authority)",
        download_url="https://gtfs-static.translink.ca/gtfs/google_transit.zip",
        dataset_page="https://www.translink.ca/about-us/doing-business-with-translink/app-developer-resources/gtfs/gtfs-data",
        licence="TransLink Open Data / Developer Terms of Use",
        licence_url="https://www.translink.ca/about-us/doing-business-with-translink/app-developer-resources",
        country="CA",
        archive_name="vancouver_TRANSLINK_GTFS.zip",
    ),
    CityFeed(
        source_id="montreal-stm",
        city="Montreal",
        agency="Société de transport de Montréal (STM)",
        download_url="https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip",
        dataset_page="https://www.stm.info/en/about/developers",
        licence="STM Developers — open data licence",
        licence_url="https://www.stm.info/en/about/developers",
        country="CA",
        archive_name="montreal_STM_GTFS.zip",
    ),
    CityFeed(
        source_id="winnipeg-transit",
        city="Winnipeg",
        agency="Winnipeg Transit",
        download_url="https://gtfs.winnipegtransit.com/google_transit.zip",
        dataset_page="https://api.winnipegtransit.com/",
        licence="City of Winnipeg Open Data Licence",
        licence_url="https://data.winnipeg.ca/open-data-licence",
        country="CA",
        archive_name="winnipeg_WT_GTFS.zip",
    ),
    CityFeed(
        source_id="portland-trimet",
        city="Portland",
        agency="TriMet",
        download_url="https://developer.trimet.org/schedule/gtfs.zip",
        dataset_page="https://developer.trimet.org/GTFS.shtml",
        licence="TriMet Developer Terms / open data",
        licence_url="https://developer.trimet.org/terms_of_use.shtml",
        country="US",
        archive_name="portland_TRIMET_GTFS.zip",
    ),
    CityFeed(
        source_id="minneapolis-metrotransit",
        city="Minneapolis",
        agency="Metro Transit (Minnesota)",
        download_url="https://svc.metrotransit.org/mtgtfs/gtfs.zip",
        dataset_page="https://www.metrotransit.org/developers",
        licence="Metro Transit / MetCouncil open data",
        licence_url="https://www.metrotransit.org/developers",
        country="US",
        archive_name="minneapolis_METRO_GTFS.zip",
    ),
    CityFeed(
        source_id="seattle-kcmetro",
        city="Seattle",
        agency="King County Metro Transit",
        download_url="https://metro.kingcounty.gov/GTFS/google_transit.zip",
        dataset_page="https://kingcounty.gov/en/dept/metro/rider-tools/mobile-and-web-apps",
        licence="King County open data",
        licence_url="https://kingcounty.gov/en/legal/open-data-license",
        country="US",
        archive_name="seattle_KCMETRO_GTFS.zip",
    ),
)


def feed_by_source_id(source_id: str) -> CityFeed:
    for feed in CITY_FEEDS:
        if feed.source_id == source_id:
            return feed
    raise KeyError(f"no registered city feed with source_id {source_id!r}")
