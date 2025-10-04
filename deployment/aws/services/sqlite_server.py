#!/usr/bin/env python3
"""
Flask-based SQLite HTTP server for multi-writer database access.
Provides REST API endpoints for SQLite operations with proper locking.
"""
import sqlite3
import json
import logging
import threading
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/sqlite-server/server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Database configuration
DB_PATH = '/var/lib/sqlite-server/recycling.db'
DB_LOCK = threading.RLock()  # Reentrant lock for nested operations

class SQLiteHTTPServer:
    """SQLite HTTP server with proper concurrency control."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_database_exists()
    
    def _ensure_database_exists(self):
        """Ensure database file and directory exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Always ensure collection-specific tables exist (matching NoSQLAdapter structure)
        with DB_LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                
                # Create vendor_invoices collection table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS vendor_invoices_docs (
                        invoice_id INTEGER PRIMARY KEY,
                        document TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create gateways collection table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS gateways_docs (
                        gateway_id TEXT PRIMARY KEY,
                        document TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create devices collection table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS devices_docs (
                        device_id TEXT PRIMARY KEY,
                        document TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create measurements collection table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS measurements_docs (
                        measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        document TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create config_updates collection table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS config_updates_docs (
                        update_id TEXT PRIMARY KEY,
                        document TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info(f"Database initialized with collection-specific tables: {self.db_path}")
            finally:
                conn.close()
    
    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute SELECT query and return results."""
        with DB_LOCK:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Return rows as dictionaries
            try:
                cursor = conn.execute(query, params)
                results = [dict(row) for row in cursor.fetchall()]
                logger.debug(f"Query executed: {query} -> {len(results)} rows")
                return results
            finally:
                conn.close()
    
    def execute_command(self, command: str, params: tuple = ()) -> Dict[str, Any]:
        """Execute INSERT/UPDATE/DELETE command."""
        with DB_LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(command, params)
                conn.commit()
                result = {
                    'rowcount': cursor.rowcount,
                    'lastrowid': cursor.lastrowid
                }
                logger.debug(f"Command executed: {command} -> {result}")
                return result
            finally:
                conn.close()
    
    def execute_transaction(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute multiple operations in a transaction."""
        with DB_LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                results = []
                for op in operations:
                    op_type = op.get('type')
                    query = op.get('query')
                    params = tuple(op.get('params', []))
                    
                    if op_type == 'query':
                        conn.row_factory = sqlite3.Row
                        cursor = conn.execute(query, params)
                        results.append([dict(row) for row in cursor.fetchall()])
                    elif op_type == 'command':
                        cursor = conn.execute(query, params)
                        results.append({
                            'rowcount': cursor.rowcount,
                            'lastrowid': cursor.lastrowid
                        })
                
                conn.commit()
                logger.debug(f"Transaction executed: {len(operations)} operations")
                return {'results': results}
            except Exception as e:
                conn.rollback()
                logger.error(f"Transaction failed: {e}")
                raise
            finally:
                conn.close()

# Initialize server
server = SQLiteHTTPServer(DB_PATH)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    try:
        # Test database connectivity
        server.execute_query("SELECT 1")
        return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/query', methods=['POST'])
def handle_query():
    """Handle SELECT queries."""
    try:
        data = request.get_json()
        query = data.get('query')
        params = tuple(data.get('params', []))
        
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        results = server.execute_query(query, params)
        return jsonify({'results': results})
        
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/execute', methods=['POST'])
def handle_execute():
    """Handle INSERT/UPDATE/DELETE commands."""
    try:
        data = request.get_json()
        command = data.get('command')
        params = tuple(data.get('params', []))
        
        if not command:
            return jsonify({'error': 'Command is required'}), 400
        
        result = server.execute_command(command, params)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/transaction', methods=['POST'])
def handle_transaction():
    """Handle multiple operations in a transaction."""
    try:
        data = request.get_json()
        operations = data.get('operations', [])
        
        if not operations:
            return jsonify({'error': 'Operations are required'}), 400
        
        result = server.execute_transaction(operations)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Transaction failed: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='SQLite HTTP Server')
    parser.add_argument('--db-path', default='/mnt/efs/database/recycling.db',
                       help='Path to SQLite database file')
    parser.add_argument('--port', type=int, default=8080,
                       help='Port to run server on')
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind to')
    args = parser.parse_args()
    
    # Update global DB_PATH
    DB_PATH = args.db_path
    
    # Run Flask development server (sufficient for our single-writer use case)
    app.run(host=args.host, port=args.port, debug=False)