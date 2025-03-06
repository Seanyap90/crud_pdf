import os
import sqlite3
import pytest
from files_api.database.local import init_db, add_invoice

TEST_DB = "test_files.db"
TEST_PDF = "sample.pdf"
TEST_PDF_CONTENT = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF"

@pytest.fixture
def db():
    init_db(TEST_DB)
    yield TEST_DB
    os.remove(TEST_DB)

@pytest.fixture
def pdf_file():
    with open(TEST_PDF, 'wb') as f:
        f.write(TEST_PDF_CONTENT)
    yield TEST_PDF
    os.remove(TEST_PDF)

def test_init_db(db):
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
    assert cursor.fetchone() is not None
    conn.close()

def test_add_file(db, pdf_file):
    filepath = "uploads/sample.pdf"
    
    add_invoice(TEST_PDF, filepath, db)
    
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    cursor.execute('SELECT filename, filepath FROM files WHERE filename = ?', (TEST_PDF,))
    result = cursor.fetchone()
    conn.close()
    
    assert result == (TEST_PDF, filepath)
