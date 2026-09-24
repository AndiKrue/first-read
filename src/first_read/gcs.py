"""Small Google Cloud Storage upload boundary."""

from dataclasses import dataclass
from functools import lru_cache

from google.cloud import storage

from first_read.config import Settings


@dataclass(frozen=True)
class UploadedObject:
    gcs_uri: str
    signed_url: str


@lru_cache(maxsize=1)
def _client() -> storage.Client:
    settings = Settings.load()
    return storage.Client(project=settings.google_cloud_project)


def upload_bytes(data: bytes, object_name: str, content_type: str) -> UploadedObject:
    settings = Settings.load()
    blob = _client().bucket(settings.gcs_bucket).blob(object_name)
    blob.upload_from_string(data, content_type=content_type)
    return UploadedObject(
        gcs_uri=f"gs://{settings.gcs_bucket}/{object_name}",
        signed_url=f"https://storage.googleapis.com/{blob.bucket.name}/{blob.name}",
    )


def _split_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    if not gcs_uri.startswith("gs://"):
        raise ValueError("Expected a gs:// URI")
    bucket_name, separator, object_name = gcs_uri[5:].partition("/")
    if not separator or not bucket_name or not object_name:
        raise ValueError("Malformed GCS URI")
    return bucket_name, object_name


def signed_url_for_uri(gcs_uri: str) -> str:
    """Create a fresh browser URL for a stable gs:// production identifier."""
    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    blob = _client().bucket(bucket_name).blob(object_name)
    return f"https://storage.googleapis.com/{blob.bucket.name}/{blob.name}"


def download_bytes(gcs_uri: str) -> bytes:
    """Fetch a stored object, e.g. a kept panel a resumed run must assemble."""
    bucket_name, object_name = _split_gcs_uri(gcs_uri)
    return _client().bucket(bucket_name).blob(object_name).download_as_bytes()
