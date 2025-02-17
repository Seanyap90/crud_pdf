"""Database fixtures for tests."""
import os
import pytest
from files_api.database.local import init_db

TEST_DB = "test_files.db"

@pytest.fixture(autouse=True)
def setup_db():
    init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)