from src.files_api.database.local import create_pdf_table
import sqlite3

def get_db():
    """Database dependency."""
    conn = sqlite3.connect('pdfs.db')
    try:
        create_pdf_table(conn)  # Ensure table exists
        yield conn
    finally:
        conn.close()