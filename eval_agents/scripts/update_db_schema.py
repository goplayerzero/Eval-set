"""
Update database schema to add validation and commit_id columns to the repositories table.
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv

# Add parent directory to path to import from core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import database utilities
from core.utils import get_db_connection, DEFAULT_DB_NAME

def update_schema(db_name=DEFAULT_DB_NAME):
    """Add validation and commit_id columns to the repositories table if they don't exist."""
    print(f"Updating database schema for {db_name}...")
    
    conn = get_db_connection(db_name)
    cursor = conn.cursor()
    
    # Check if validation_results column exists
    cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'repositories' AND column_name = 'validation_results'
    """)
    
    if not cursor.fetchone():
        print("Adding validation_results column...")
        cursor.execute("""
        ALTER TABLE repositories 
        ADD COLUMN validation_results BOOLEAN DEFAULT NULL
        """)
    
    # Check if validation_explanation column exists
    cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'repositories' AND column_name = 'validation_explanation'
    """)
    
    if not cursor.fetchone():
        print("Adding validation_explanation column...")
        cursor.execute("""
        ALTER TABLE repositories 
        ADD COLUMN validation_explanation TEXT DEFAULT NULL
        """)
    
    # Check if commit_id column exists
    cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = 'repositories' AND column_name = 'commit_id'
    """)
    
    if not cursor.fetchone():
        print("Adding commit_id column...")
        cursor.execute("""
        ALTER TABLE repositories 
        ADD COLUMN commit_id TEXT DEFAULT NULL
        """)
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print("Database schema updated successfully!")

if __name__ == "__main__":
    load_dotenv()
    update_schema()
