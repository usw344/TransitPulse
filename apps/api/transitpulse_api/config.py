from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRANSITPULSE_",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg://transitpulse:transitpulse@localhost:5432/transitpulse"
    )
    gtfs_source_url: str = (
        "https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip"
    )
    gtfs_provider: str = "City of Edmonton / Edmonton Transit Service"
    gtfs_download_timeout_seconds: float = 90.0
    realtime_vehicle_positions_url: str = (
        "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb"
    )
    realtime_trip_updates_url: str = (
        "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/TripUpdate/TripUpdates.pb"
    )
    realtime_alerts_url: str = (
        "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Alert/Alerts.pb"
    )
    realtime_poll_seconds: int = 30
    realtime_stale_after_seconds: int = 120
    realtime_request_timeout_seconds: float = 20.0
    operations_on_time_seconds: int = 60
    operations_major_delay_seconds: int = 300
    operations_bunching_ratio: float = 0.5
    operations_gap_ratio: float = 1.75


settings = Settings()
