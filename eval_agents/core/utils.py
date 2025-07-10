"""
This module provides shared functionality used across multiple agents:
- Database operations (PostgreSQL)
- Command execution wrappers
- Path and file utilities
"""

import os
import subprocess
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from typing import Any, Dict, List, Optional, Tuple, Union
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Default database configuration
DEFAULT_DB_NAME = "repos_db"
DEFAULT_DB_USER = os.getenv("POSTGRES_USER", "postgres")
DEFAULT_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
DEFAULT_DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DEFAULT_DB_PORT = os.getenv("POSTGRES_PORT", "5432")


# ---------------------------------
# Database utilities
# ---------------------------------

def get_db_connection(db_name: str = DEFAULT_DB_NAME):
    """Get a connection to the PostgreSQL database.
    
    Args:
        db_name: Name of the database to connect to
        
    Returns:
        PostgreSQL database connection
    """
    # Build connection parameters
    conn_params = {
        "dbname": db_name,
        "user": DEFAULT_DB_USER,
        "host": DEFAULT_DB_HOST,
        "port": DEFAULT_DB_PORT
    }
    
    # Only add password if it's not empty
    if DEFAULT_DB_PASSWORD:
        conn_params["password"] = DEFAULT_DB_PASSWORD
    
    try:
        conn = psycopg2.connect(**conn_params)
        return conn
    except psycopg2.OperationalError:
        # Database might not exist yet, connect to default postgres database
        postgres_params = conn_params.copy()
        postgres_params["dbname"] = "postgres"
        
        conn = psycopg2.connect(**postgres_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (db_name,))
        exists = cursor.fetchone()
        
        if not exists:
            # Create database
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        
        cursor.close()
        conn.close()
        
        # Connect to the newly created database
        return psycopg2.connect(**conn_params)

def init_db(db_name: str = DEFAULT_DB_NAME) -> None:
    """Initialize the PostgreSQL database with required tables if they don't exist.
    
    Args:
        db_name: Name of the database to create or connect to
    """
    os.makedirs("data/repos", exist_ok=True)
    conn = get_db_connection(db_name)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS repositories (
        id SERIAL PRIMARY KEY,
        repo_url TEXT UNIQUE NOT NULL,
        language TEXT NOT NULL,
        test_results BOOLEAN DEFAULT FALSE,
        validation_results BOOLEAN DEFAULT NULL,
        validation_explanation TEXT DEFAULT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    cursor.close()
    conn.close()


def is_repo_in_db(repo_url: str, db_name: str = DEFAULT_DB_NAME) -> bool:
    """Check if a repository already exists in the database.
    
    Args:
        repo_url: The URL of the repository
        db_name: Name of the database
        
    Returns:
        True if the repository exists, False otherwise
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT COUNT(*) FROM repositories WHERE repo_url = %s", 
            (repo_url,)
        )
        count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        return count > 0
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return False


def add_repo_to_db(repo_url: str, language: str, db_name: str = DEFAULT_DB_NAME) -> bool:
    """Add a repository to the database.
    
    Args:
        repo_url: The URL of the repository
        language: The primary programming language of the repository
        db_name: Name of the database
        
    Returns:
        True if the repository was added, False if it already exists
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "INSERT INTO repositories (repo_url, language) VALUES (%s, %s)",
                (repo_url, language)
            )
            conn.commit()
            success = cursor.rowcount > 0
        except psycopg2.errors.UniqueViolation:
            # Repository already exists
            conn.rollback()
            success = False
        
        cursor.close()
        conn.close()
        return success
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return add_repo_to_db(repo_url, language, db_name)


def update_test_results(db_name: str, repo_url: str, results: Dict[str, Any]) -> bool:
    """Update the test results for a repository.
    
    Args:
        db_name: Name of the database
        repo_url: The URL of the repository
        results: Dictionary with test results in the format:
            {
                "Repo": {"remoteUrl": str, "languages": List[str]},
                "IntegrationTest": {"fileContent": str},
                "IntegrationTestRun": {
                    "commitId": str,
                    "result": {"stdout": str, "stderr": str, "returnCode": int},
                    "pass": bool
                }
            }
        
    Returns:
        True if the update was successful, False otherwise
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        # Extract values from results dictionary
        test_passed = results.get("IntegrationTestRun", {}).get("pass", False)
        
        # Combine stdout and stderr for storage
        result_data = results.get("IntegrationTestRun", {}).get("result", {})
        stdout = result_data.get("stdout", "")
        stderr = result_data.get("stderr", "")
        test_output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        
        # Check if test_output column exists
        cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'test_output'
        """)
        
        if not cursor.fetchone():
            # Add test_output column if it doesn't exist
            cursor.execute("""
            ALTER TABLE repositories 
            ADD COLUMN test_output TEXT DEFAULT NULL
            """)
            conn.commit()
            
        # Check if test_details column exists
        cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'test_details'
        """)
        
        if not cursor.fetchone():
            # Add test_details column if it doesn't exist
            cursor.execute("""
            ALTER TABLE repositories 
            ADD COLUMN test_details JSONB DEFAULT NULL
            """)
            conn.commit()
        
        # Update test results, output and details
        import json
        cursor.execute(
            "UPDATE repositories SET test_results = %s, test_output = %s, test_details = %s WHERE repo_url = %s",
            (test_passed, test_output, json.dumps(results), repo_url)
        )
        
        success = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        
        return success
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return False


def get_untested_repos(language: str = None, limit: int = 10, db_name: str = DEFAULT_DB_NAME) -> List[Tuple[str, str]]:
    """Get repositories that haven't been tested yet.
    
    Args:
        language: Optional filter by programming language
        limit: Maximum number of repositories to return
        db_name: Name of the database
        
    Returns:
        List of tuples containing (repo_url, language)
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        if language:
            cursor.execute(
                "SELECT repo_url, language FROM repositories WHERE test_results = FALSE AND language = %s LIMIT %s", 
                (language, limit)
            )
        else:
            cursor.execute(
                "SELECT repo_url, language FROM repositories WHERE test_results = FALSE LIMIT %s", 
                (limit,)
            )
        
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return results
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return []


def get_unvalidated_repos(limit: int = 10, db_name: str = DEFAULT_DB_NAME) -> List[str]:
    """Get repositories that haven't been validated yet.
    
    Args:
        limit: Maximum number of repositories to return
        db_name: Name of the database
        
    Returns:
        List of repository URLs
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT repo_url FROM repositories WHERE validation_results IS NULL LIMIT %s",
            (limit,)
        )
        
        repos = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return repos
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return []


def update_validation_results(repo_url: str, is_valid: bool, explanation: str, db_name: str = DEFAULT_DB_NAME) -> bool:
    """Update the validation results for a repository.
    
    Args:
        repo_url: URL of the repository
        is_valid: Whether the repository is valid
        explanation: Explanation of the validation result
        db_name: Name of the database
        
    Returns:
        True if the update was successful, False otherwise
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        cursor.execute(
            """UPDATE repositories 
               SET validation_results = %s, validation_explanation = %s 
               WHERE repo_url = %s""",
            (is_valid, explanation, repo_url)
        )
        
        success = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        
        return success
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return False


def get_validated_repos(db_name: str = DEFAULT_DB_NAME, limit: int = 10) -> List[str]:
    """Get repositories that have passed validation but haven't been tested yet.
    
    Args:
        db_name: Name of the database
        limit: Maximum number of repositories to return
        
    Returns:
        List of repository URLs that passed validation but haven't been tested
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        cursor.execute(
            """SELECT repo_url FROM repositories 
               WHERE validation_results = TRUE AND test_results = FALSE 
               LIMIT %s""",
            (limit,)
        )
        
        repos = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return repos
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return []


def update_repo_commit_id(repo_url: str, commit_id: str, db_name: str = DEFAULT_DB_NAME) -> bool:
    """Update the commit ID for a repository.
    
    Args:
        repo_url: URL of the repository
        commit_id: Git commit ID/hash of the cloned repository
        db_name: Name of the database
        
    Returns:
        True if the update was successful, False otherwise
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        # Check if commit_id column exists
        cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'commit_id'
        """)
        
        if not cursor.fetchone():
            # Add commit_id column if it doesn't exist
            cursor.execute("""
            ALTER TABLE repositories 
            ADD COLUMN commit_id TEXT DEFAULT NULL
            """)
            conn.commit()
        
        cursor.execute(
            """UPDATE repositories 
               SET commit_id = %s 
               WHERE repo_url = %s""",
            (commit_id, repo_url)
        )
        
        success = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        
        return success
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return False


def update_repo_test_status(repo_url: str, test_results: str, db_name: str = DEFAULT_DB_NAME) -> bool:
    """Update the test status and results for a repository.
    
    Args:
        repo_url: URL of the repository
        test_results: JSON string containing test results
        db_name: Name of the database
        
    Returns:
        True if the update was successful, False otherwise
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        # Check if test_results_json column exists
        cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'repositories' AND column_name = 'test_results_json'
        """)
        
        if not cursor.fetchone():
            # Add test_results_json column if it doesn't exist
            cursor.execute("""
            ALTER TABLE repositories 
            ADD COLUMN test_results_json TEXT DEFAULT NULL
            """)
            conn.commit()
        
        cursor.execute(
            """UPDATE repositories 
               SET test_results = TRUE, test_results_json = %s 
               WHERE repo_url = %s""",
            (test_results, repo_url)
        )
        
        success = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        
        return success
    except psycopg2.errors.UndefinedTable:
        # Table doesn't exist yet
        init_db(db_name)
        return False


# ---------------------------------
# Command execution
# ---------------------------------

def run_cmd(
    cmd: Union[str, List[str]], 
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
) -> Tuple[str, str, int]:
    """Run a shell command and return its output.
    
    Args:
        cmd: Command to run (string or list of arguments)
        cwd: Working directory for the command
        env: Environment variables to set
        timeout: Timeout in seconds
        
    Returns:
        Tuple of (stdout, stderr, return_code)
    """
    if isinstance(cmd, str):
        shell = True
    else:
        shell = False
        
    # Merge environment with current environment
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=shell,
        cwd=cwd,
        env=merged_env,
        text=True,
    )
    
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return_code = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return_code = -1
        stderr += "\nCommand timed out"
    
    return stdout, stderr, return_code