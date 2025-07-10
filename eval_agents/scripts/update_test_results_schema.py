#!/usr/bin/env python3
"""
Update Test Results Schema

This script updates the database schema to change the default value of test_results
from FALSE to NULL and updates existing records to follow the standard:
- NULL: Tests haven't been run yet
- TRUE: Tests ran and passed
- FALSE: Tests ran and failed

It also adds a test_status column to store additional states like 'INVALID' for tests
that couldn't be run.
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Add parent directory to path to import from core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import database utilities
from core.utils import get_db_connection, DEFAULT_DB_NAME

def update_test_results_schema(db_name=DEFAULT_DB_NAME):
    """
    Update the database schema to change test_results default and add test_status column.
    
    Args:
        db_name: Name of the database
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        print(f"Updating schema in database: {db_name}")
        
        # 1. First check if the table exists
        cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'repositories'
        )
        """)
        
        if not cursor.fetchone()[0]:
            print("Table 'repositories' does not exist. Nothing to update.")
            return
        
        # 2. Check current default value for test_results
        cursor.execute("""
        SELECT column_default 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'test_results'
        """)
        
        current_default = cursor.fetchone()
        if current_default:
            print(f"Current default for test_results: {current_default[0]}")
        
        # 3. Change the default value of test_results to NULL
        cursor.execute("""
        ALTER TABLE repositories 
        ALTER COLUMN test_results DROP DEFAULT
        """)
        
        cursor.execute("""
        ALTER TABLE repositories 
        ALTER COLUMN test_results SET DEFAULT NULL
        """)
        
        # 4. Check if test_status column exists
        cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'test_status'
        """)
        
        if not cursor.fetchone():
            # Add test_status column if it doesn't exist
            print("Adding test_status column...")
            cursor.execute("""
            ALTER TABLE repositories 
            ADD COLUMN test_status TEXT DEFAULT NULL
            """)
        else:
            print("test_status column already exists")
        
        # 5. Update existing records: set test_results to NULL where it's FALSE
        # (indicating tests haven't been run yet)
        cursor.execute("""
        UPDATE repositories 
        SET test_results = NULL 
        WHERE test_results = FALSE AND test_output IS NULL
        """)
        
        rows_updated = cursor.rowcount
        print(f"Updated {rows_updated} records: set test_results to NULL where tests haven't been run")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("Schema update completed successfully")
        
    except Exception as e:
        print(f"Error updating schema: {str(e)}")

def get_schema_info(db_name=DEFAULT_DB_NAME):
    """
    Get information about the current schema.
    
    Args:
        db_name: Name of the database
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        # Get column information
        cursor.execute("""
        SELECT column_name, data_type, column_default, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'repositories'
        ORDER BY ordinal_position
        """)
        
        columns = cursor.fetchall()
        
        print("\nColumns:")
        print("%-20s %-15s %-30s %-10s" % ("Column", "Type", "Default", "Nullable"))
        print("-" * 75)
        for col in columns:
            print("%-20s %-15s %-30s %-10s" % (col[0], col[1], str(col[2] or "None"), col[3]))
        
        # Get record counts
        cursor.execute("SELECT COUNT(*) FROM repositories")
        total_records = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM repositories WHERE test_results IS NULL")
        null_test_results = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM repositories WHERE test_results = TRUE")
        true_test_results = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM repositories WHERE test_results = FALSE")
        false_test_results = cursor.fetchone()[0]
        
        print("\nRecord counts:")
        print(f"Total records: {total_records}")
        print(f"test_results = NULL: {null_test_results}")
        print(f"test_results = TRUE: {true_test_results}")
        print(f"test_results = FALSE: {false_test_results}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error getting schema info: {str(e)}")

if __name__ == "__main__":
    load_dotenv()
    
    # Show current schema info
    print("Current schema information:")
    get_schema_info()
    
    # Update schema
    print("\nUpdating schema...")
    update_test_results_schema()
    
    # Show updated schema info
    print("\nUpdated schema information:")
    get_schema_info()
