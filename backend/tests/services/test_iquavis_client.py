from __future__ import annotations

from app.iquavis.client import IQUAVIS_DIRECT_API_DISABLED_MESSAGE, IQuavisClient
from app.tms_core.config import IQuavisConfig


def _client() -> IQuavisClient:
    return IQuavisClient(
        IQuavisConfig(
            enabled=True,
            base_url="http://example.invalid/iquavis-api",
            username="user",
            password="password",
            timeout_seconds=1.0,
            max_retries=1,
            retry_base_seconds=1,
            poll_interval_seconds=60,
            allowed_project_ids=[],
        )
    )


def test_extract_error_message_hides_html_error_page() -> None:
    client = _client()
    try:
        message = client._extract_error_message("<!DOCTYPE html><html><body>504 DNS look up failed</body></html>")
    finally:
        client.close()

    assert message == IQUAVIS_DIRECT_API_DISABLED_MESSAGE
