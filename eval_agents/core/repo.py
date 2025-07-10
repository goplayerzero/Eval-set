"""
Repository Management Module

This module provides functionality for managing repositories for testing,
including selecting untested repositories and running parallel tests on them.
"""

import os
import sys
import json
import argparse
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from eval_agents.core.utils import get_db_connection, DEFAULT_DB_NAME
from eval_agents.core.parallel import ParallelTestRunner

import logging

# Load environment variables
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Default to 10 parallel repositories
DEFAULT_MAX_PARALLEL = 10


def get_untested_validated_repos(limit: int = 10, db_name: str = DEFAULT_DB_NAME) -> List[Dict[str, Any]]:
    """
Get repositories that have been validated but not tested yet.

Args:
    limit: Maximum number of repositories to return
    db_name: Name of the database to use

Returns:
    List of dictionaries containing repository information
    """
    try:
        conn = get_db_connection(db_name)
        cursor = conn.cursor()
        
        # Get repositories that have been validated but not tested
        cursor.execute(
            """SELECT id, repo_url, language 
               FROM repositories 
               WHERE validation_results = TRUE 
               AND (test_results IS NULL OR test_results = FALSE) 
               LIMIT %s""",
            (limit,)
        )
        
        repos = [{
            "id": row[0],
            "repo_url": row[1],
            "language": row[2]
        } for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return repos
    except Exception as e:
        logger.info(f"Error getting untested validated repositories: {str(e)}")
        return []


def run_parallel_tests_on_top_repos(max_parallel: int = DEFAULT_MAX_PARALLEL,
                                   num_repos: int = 10,
                                   ssh_host: str = None,
                                   ssh_user: str = None,
                                   ssh_key_path: str = None,
                                   ssh_port: str = None,
                                   work_dir: str = "/tmp/repo_tests",
                                   db_name: str = DEFAULT_DB_NAME) -> List[Dict[str, Any]]:
    """
Run parallel tests on the top untested validated repositories.

Args:
    max_parallel: Maximum number of parallel tests to run
    num_repos: Number of repositories to test
    ssh_host: SSH hostname for Playerzero Ubuntu server
    ssh_user: SSH username for Playerzero Ubuntu server
    ssh_key_path: Path to SSH private key for Playerzero Ubuntu server
    ssh_port: SSH port for Playerzero Ubuntu server
    work_dir: Working directory for mounting into containers
    db_name: Name of the database to use

Returns:
    List of dictionaries with repository processing results
    """
    # Get SSH connection details from environment variables if not provided
    ssh_host = ssh_host or os.getenv("PLAYERZERO_SSH_HOST")
    ssh_user = ssh_user or os.getenv("PLAYERZERO_SSH_USER")
    ssh_key_path = ssh_key_path or os.getenv("SSH_KEY_PATH")
    ssh_port = ssh_port or os.getenv("PLAYERZERO_SSH_PORT")
    
    # Get untested validated repositories
    repos = get_untested_validated_repos(limit=num_repos, db_name=db_name)
    
    if not repos:
        logger.info("No untested validated repositories found")
        return []
    
    logger.info(f"Found {len(repos)} untested validated repositories")
    for repo in repos:
        logger.info(f"  - {repo['repo_url']} ({repo['language']})")
    
    # Initialize the ParallelTestRunner
    runner = ParallelTestRunner(
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        ssh_port=ssh_port,
        work_dir=work_dir,
        max_parallel=max_parallel
    )
    
    # Process repositories in parallel
    return runner.process_repos_parallel([repo["repo_url"] for repo in repos])


def main():
    """
Main function for command-line execution.
    """
    parser = argparse.ArgumentParser(description="Run tests on untested validated repositories")
    parser.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL, 
                        help="Maximum number of parallel processes")
    parser.add_argument("--num-repos", type=int, default=10, 
                        help="Number of repositories to test")
    parser.add_argument("--ssh-host", help="SSH hostname for Playerzero Ubuntu server")
    parser.add_argument("--ssh-user", help="SSH username for Playerzero Ubuntu server")
    parser.add_argument("--ssh-key", help="SSH private key path for Playerzero Ubuntu server")
    parser.add_argument("--ssh-port", help="SSH port for Playerzero Ubuntu server")
    parser.add_argument("--work-dir", default="/tmp/repo_tests", 
                        help="Working directory for mounting into containers")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, 
                        help="Database name to use")
    
    args = parser.parse_args()
    
    # Run parallel tests on untested validated repositories
    results = run_parallel_tests_on_top_repos(
        max_parallel=args.max_parallel,
        num_repos=args.num_repos,
        ssh_host=args.ssh_host,
        ssh_user=args.ssh_user,
        ssh_key_path=args.ssh_key,
        ssh_port=args.ssh_port,
        work_dir=args.work_dir,
        db_name=args.db_name
    )
    
    # Print results
    logger.info(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()