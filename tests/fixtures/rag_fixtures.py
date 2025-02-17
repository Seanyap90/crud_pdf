import pytest
import boto3
from files_api.msg_queue import QueueFactory
from files_api.vlm.rag import Worker

TEST_QUEUE = QueueFactory.get_queue_handler()

@pytest.fixture(scope="session")
def shared_queue():
    return TEST_QUEUE