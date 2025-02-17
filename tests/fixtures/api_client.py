import pytest
from fastapi.testclient import TestClient

from files_api.main import create_app
from files_api.settings import Settings
from tests.consts import TEST_BUCKET_NAME


@pytest.fixture
def client(mocked_aws) -> TestClient:  # type: ignore # pylint: disable=unused-argument
    settings = Settings(s3_bucket_name=TEST_BUCKET_NAME)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client