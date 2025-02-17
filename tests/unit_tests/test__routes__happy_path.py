from fastapi import status
from fastapi.testclient import TestClient
import sqlite3
import os
import boto3
from tests.fixtures.db_client import TEST_DB
from tests.consts import TEST_BUCKET_NAME
from tests.fixtures.rag_fixtures import TEST_QUEUE
from files_api.database.local import init_db, add_file
from files_api.msg_queue import QueueFactory

# Constants for testing
TEST_FILE_PATH = "test.txt"
TEST_FILE_CONTENT = b"Hello, world!"
TEST_FILE_CONTENT_TYPE = "text/plain"
TEST_PDF_PATH = "test.pdf"
TEST_PDF_CONTENT = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF"
TEST_PDF_CONTENT_TYPE = "application/pdf"

async def test_upload_pdf_with_db_and_s3_notification(client: TestClient, mocked_aws, shared_queue):
    test_file_path = TEST_PDF_PATH
    test_pdf_content = TEST_PDF_CONTENT

    # Initialize queue handler
    queue_handler = QueueFactory.get_queue_handler()

    # Verify empty DB initially
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM files')
    print("Before upload:", cursor.fetchall())
    conn.close()

    # Upload file
    response = client.put(
        f"/v1/files/{test_file_path}",
        files={"file_content": ("test.pdf", test_pdf_content, "application/pdf")},
    )

    assert response.status_code == status.HTTP_201_CREATED

    # Initialize DB
    init_db(TEST_DB)
    add_file(test_file_path, test_file_path, TEST_DB)

    # Verify DB entry
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute('SELECT filename, filepath FROM files WHERE filepath = ?', (test_file_path,))
    result = cursor.fetchone()
    print("After upload:", cursor.fetchall())
    conn.close()
    assert result is not None
    assert result[1] == test_file_path

    # Add task to queue
    await TEST_QUEUE.add_task({
        "task_type": "process_pdf",
        "file_info": {
            "filepath": test_file_path
        }
    })

    # Verify S3 notification
    s3_client = boto3.client('s3')
    config = s3_client.get_bucket_notification_configuration(Bucket=TEST_BUCKET_NAME)
    assert 'QueueConfigurations' in config
    assert config['QueueConfigurations'][0]['Events'] == ['s3:ObjectCreated:Put']

    # Verify queue message
    task = await TEST_QUEUE.get_task()
    print("Queue task:", task)
    assert task is not None
    assert task["task_type"] == "process_pdf"
    assert task["file_info"]["filepath"] == test_file_path

    # Put task back for worker test
    if task:
        await shared_queue.add_task(task)

    # Cleanup
    os.remove(TEST_DB)

def test__upload_file_with_db(client: TestClient):
    """Test file upload creates an entry in the database."""
    # Initialize DB
    
    init_db(TEST_DB)
    
    # Upload PDF
    test_file_path = "docs/test.pdf"
    response = client.put(
        f"/v1/files/{test_file_path}",
        files={"file_content": (TEST_PDF_PATH, TEST_PDF_CONTENT, TEST_PDF_CONTENT_TYPE)},
    )

    assert response.status_code == status.HTTP_201_CREATED

    # Verify DB entry
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute('SELECT filename, filepath FROM files WHERE filepath = ?', (test_file_path,))
    result = cursor.fetchone()
    conn.close()

    assert result is not None
    assert result[1] == test_file_path

    # Cleanup
    os.remove(TEST_DB)


def test__upload_file__happy_path(client: TestClient):
    # create a file
    test_file_path = "some/nested/file.txt"
    test_file_content = b"some content"
    test_file_content_type = "text/plain"

    response = client.put(
        f"/v1/files/{test_file_path}",
        files={"file_content": (test_file_path, test_file_content, test_file_content_type)},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {
        "file_path": test_file_path,
        "message": f"New file uploaded at path: /{test_file_path}",
    }

    # update an existing file
    updated_content = b"updated content"
    response = client.put(
        f"/v1/files/{test_file_path}",
        files={"file_content": (test_file_path, updated_content, test_file_content_type)},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "file_path": test_file_path,
        "message": f"Existing file updated at path: /{test_file_path}",
    }


def test_list_files_with_pagination(client: TestClient):
    # Upload files
    for i in range(15):
        client.put(
            f"/v1/files/file{i}.txt",
            files={"file_content": (f"file{i}.txt", TEST_FILE_CONTENT, TEST_FILE_CONTENT_TYPE)},
        )
    # List files with page size 10
    response = client.get("/v1/files?page_size=10")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["files"]) == 10
    assert "next_page_token" in data


def test_get_file_metadata(client: TestClient):
    # Upload a file
    client.put(
        f"/v1/files/{TEST_FILE_PATH}",
        files={"file_content": (TEST_FILE_PATH, TEST_FILE_CONTENT, TEST_FILE_CONTENT_TYPE)},
    )
    # Get file metadata
    response = client.head(f"/v1/files/{TEST_FILE_PATH}")
    assert response.status_code == status.HTTP_200_OK
    headers = response.headers
    assert headers["Content-Type"] == TEST_FILE_CONTENT_TYPE
    assert headers["Content-Length"] == str(len(TEST_FILE_CONTENT))
    assert "Last-Modified" in headers


def test_get_file(client: TestClient):
    # Upload a file
    client.put(
        f"/v1/files/{TEST_FILE_PATH}",
        files={"file_content": (TEST_FILE_PATH, TEST_FILE_CONTENT, TEST_FILE_CONTENT_TYPE)},
    )
    # Get file
    response = client.get(f"/v1/files/{TEST_FILE_PATH}")
    assert response.status_code == status.HTTP_200_OK
    assert response.content == TEST_FILE_CONTENT


def test_delete_file(client: TestClient):
    # Upload a file
    client.put(
        f"/v1/files/{TEST_FILE_PATH}",
        files={"file_content": (TEST_FILE_PATH, TEST_FILE_CONTENT, TEST_FILE_CONTENT_TYPE)},
    )

    # Delete file
    response = client.delete(f"/v1/files/{TEST_FILE_PATH}")
    assert response.status_code == status.HTTP_204_NO_CONTENT

    # the file should not be found if it was deleted
    response = client.get(f"/v1/files/{TEST_FILE_PATH}")
    assert response.status_code == status.HTTP_404_NOT_FOUND