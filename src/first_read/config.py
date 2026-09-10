"""Environment-backed application configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    google_cloud_project: str
    google_cloud_location: str
    gcs_bucket: str
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_database: str
    clickhouse_secure: bool

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        required = (
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GCS_BUCKET",
            "CLICKHOUSE_HOST",
            "CLICKHOUSE_PORT",
            "CLICKHOUSE_USER",
            "CLICKHOUSE_PASSWORD",
            "CLICKHOUSE_DATABASE",
        )
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() != "TRUE":
            missing.append("GOOGLE_GENAI_USE_VERTEXAI=TRUE")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            google_cloud_project=os.environ["GOOGLE_CLOUD_PROJECT"],
            google_cloud_location=os.environ["GOOGLE_CLOUD_LOCATION"],
            gcs_bucket=os.environ["GCS_BUCKET"],
            clickhouse_host=os.environ["CLICKHOUSE_HOST"],
            clickhouse_port=int(os.environ["CLICKHOUSE_PORT"]),
            clickhouse_user=os.environ["CLICKHOUSE_USER"],
            clickhouse_password=os.environ["CLICKHOUSE_PASSWORD"],
            clickhouse_database=os.environ["CLICKHOUSE_DATABASE"],
            clickhouse_secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower()
            == "true",
        )
