"""
CloneAgent

Clones GitHub repositories into isolated Docker containers on the Playerzero Ubuntu server.
Provides a clean environment for each repository to prevent dependency conflicts.
Supports parallel cloning of multiple repositories for efficient testing.
"""

import os
import sys
import time
import subprocess
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from typing import Dict, List, Tuple, Any, Optional, Union
from dotenv import load_dotenv

# Add parent directory to path for imports
from eval_agents.core.utils import (
    get_validated_repos,
    DEFAULT_DB_NAME,
    run_cmd,
    update_repo_commit_id,
)

import logging

# Load environment variables (optional .env)
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Container configuration
BASE_IMAGE = "python:3.13-alpine"
WORKSPACE_DIR = "/workspace"
REPO_DIR = f"{WORKSPACE_DIR}/repo"

# SSH configuration for Playerzero Ubuntu server
PLAYERZERO_SSH_HOST = os.getenv("PLAYERZERO_SSH_HOST", "playerzero.example.com")
PLAYERZERO_SSH_USER = os.getenv("PLAYERZERO_SSH_USER", "ubuntu")
PLAYERZERO_SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", os.path.expanduser("~/.ssh/id_rsa"))
PLAYERZERO_SSH_PORT = os.getenv("PLAYERZERO_SSH_PORT", "22")

# Default to 10 parallel repositories
DEFAULT_MAX_PARALLEL = 10

class CloneAgent:
    """Agent that clones GitHub repositories in isolated Docker containers on the Playerzero Ubuntu server.
    
    Provides a clean, isolated environment for each repository to prevent
    dependency conflicts and ensure consistent testing conditions.
    Supports parallel cloning of multiple repositories for efficient testing.
    """
    
    def __init__(self, 
                 ssh_host: str = PLAYERZERO_SSH_HOST,
                 ssh_user: str = PLAYERZERO_SSH_USER,
                 ssh_key_path: str = PLAYERZERO_SSH_KEY_PATH,
                 ssh_port: str = PLAYERZERO_SSH_PORT,
                 work_dir: str = "/tmp/repo_tests"):
        """Initialize the CloneAgent for connecting to the Playerzero Ubuntu server.
        
        Args:
            ssh_host: Hostname of the Playerzero Ubuntu server
            ssh_user: Username for SSH connection
            ssh_key_path: Path to SSH private key
            ssh_port: SSH port
            work_dir: Remote directory to mount into containers
        """
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.ssh_port = ssh_port
        self.work_dir = work_dir
        
        # Verify SSH connection and Docker availability
        self._verify_connection()
    
    def _verify_connection(self) -> None:
        """Verify SSH connection and Docker availability on the Playerzero Ubuntu server.
        Falls back to local Docker if SSH connection fails.
        """
        self.use_remote = True
        self.use_local_docker = False
        
        # Test SSH connection using agent forwarding
        ssh_cmd = ["ssh"]
        
        # Add key if provided
        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            ssh_cmd.extend(["-i", self.ssh_key_path])
        
        # Add other SSH options
        ssh_cmd.extend([
            "-p", self.ssh_port,
            "-o", "StrictHostKeyChecking=no",
            "-A",  # Enable SSH agent forwarding
            f"{self.ssh_user}@{self.ssh_host}",
            "echo 'SSH connection successful'"
        ])
        
        logger.info(f"Testing SSH connection to {self.ssh_user}@{self.ssh_host}...")
        stdout, stderr, exit_code = run_cmd(ssh_cmd)
        
        if exit_code != 0:
            logger.info(f"SSH connection failed: {stderr}")
            logger.info("Falling back to local Docker")
            self.use_remote = False
            self.use_local_docker = True
            
            # Check local Docker availability
            docker_cmd = ["docker", "--version"]
            stdout, stderr, exit_code = run_cmd(docker_cmd)
            
            if exit_code != 0:
                raise RuntimeError(f"Local Docker not available: {stderr}. Please install Docker or fix SSH connection.")
            
            logger.info(f"Connected to Docker: {stdout.strip()}")
            return
        
        logger.info("SSH connection successful!")
        
        # Check Docker availability on remote server
        docker_cmd = ["ssh"]
        
        # Add key if provided and valid
        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            docker_cmd.extend(["-i", self.ssh_key_path])
        
        # Add other SSH options
        docker_cmd.extend([
            "-p", self.ssh_port,
            "-o", "StrictHostKeyChecking=no",
            "-A",  # Enable SSH agent forwarding
            f"{self.ssh_user}@{self.ssh_host}",
            "docker --version"
        ])
        
        stdout, stderr, exit_code = run_cmd(docker_cmd)
        
        if exit_code != 0:
            logger.info(f"Docker not available on remote server: {stderr}")
            logger.info("Falling back to local Docker")
            self.use_remote = False
            self.use_local_docker = True
            
            # Check local Docker availability
            docker_cmd = ["docker", "--version"]
            stdout, stderr, exit_code = run_cmd(docker_cmd)
            
            if exit_code != 0:
                raise RuntimeError(f"Local Docker not available: {stderr}. Please install Docker or fix SSH connection.")
            
            logger.info(f"Connected to Docker: {stdout.strip()}")
            return
        
        logger.info(f"Connected to Playerzero Ubuntu Docker: {stdout.strip()}")
        logger.info(f"Using SSH connection to {self.ssh_user}@{self.ssh_host}:{self.ssh_port}")
        logger.info(f"Working directory: {self.work_dir}")
        
        # Create work directory if it doesn't exist
        cmd = [
            "ssh", 
            "-i", self.ssh_key_path,
            "-p", self.ssh_port,
            f"{self.ssh_user}@{self.ssh_host}",
            f"mkdir -p {self.work_dir}"
        ]
        
        run_cmd(cmd)
    
    def _run_ssh_command(self, remote_cmd: str) -> Tuple[int, str, str]:
        """Run a command on the Playerzero Ubuntu server via SSH
        
        Args:
            remote_cmd: Command to run on the remote server
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        cmd = ["ssh"]
        
        # Add key if provided and valid
        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            cmd.extend(["-i", self.ssh_key_path])
        
        # Add other SSH options
        cmd.extend([
            "-p", self.ssh_port,
            "-o", "StrictHostKeyChecking=no",
            "-A",  # Enable SSH agent forwarding
            f"{self.ssh_user}@{self.ssh_host}",
            remote_cmd
        ])
        
        stdout, stderr, exit_code = run_cmd(cmd)
        return exit_code, stdout, stderr
    
    def clone_repo(self, repo_url: str) -> Tuple[bool, str, str, str]:
        """Clone a repository into a Docker container on the Playerzero Ubuntu server or locally.
        
        Args:
            repo_url: URL of the GitHub repository to clone
            
        Returns:
            Tuple of (success, container_id, output, commit_id)
        """
        # Extract repo name from URL for container name
        repo_name = repo_url.split('/')[-1].replace('.git', '')
        sanitized_repo_name = re.sub(r'[^a-zA-Z0-9_-]', '', repo_name)
        container_name = f"repo_test_{sanitized_repo_name}_{int(time.time())}"
        
        if self.use_remote:
            logger.info(f"Cloning {repo_url} into container {container_name} on Playerzero Ubuntu server")
            
            # Create container on remote server
            remote_cmd = (
                f"docker run -d --name {container_name} "
                f"-v {self.work_dir}:{WORKSPACE_DIR} "
                f"--memory=2g "
                f"--workdir={WORKSPACE_DIR} "
                f"{BASE_IMAGE} sleep infinity"
            )
            
            exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            
            if exit_code != 0:
                error_msg = f"Failed to create container: {stderr}"
                return False, "", error_msg, ""
            
            container_id = stdout.strip()
            
            # Install git in container
            remote_cmd = f"docker exec {container_name} apk add --no-cache git"
            exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to install git: {stderr}"
                return False, container_id, error_msg, ""
            
            # Create repo directory
            remote_cmd = f"docker exec {container_name} mkdir -p {REPO_DIR}"
            exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to create repo directory: {stderr}"
                return False, container_id, error_msg, ""
            
            # Clone repository
            remote_cmd = f"docker exec --workdir={REPO_DIR} {container_name} git clone {repo_url} ."
            exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to clone repository: {stderr}"
                return False, container_id, error_msg, ""
            
            # Get commit ID
            remote_cmd = f"docker exec --workdir={REPO_DIR} {container_name} git rev-parse HEAD"
            exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to get commit ID: {stderr}"
                return False, container_id, error_msg, ""
            
            commit_id = stdout.strip()
        else:
            # Use local Docker
            logger.info(f"Cloning {repo_url} into container {container_name} locally")
            
            # Create container locally
            cmd = [
                "docker", "run", "-d", 
                "--name", container_name,
                "--memory=2g",
                "--workdir", WORKSPACE_DIR,
                BASE_IMAGE, "sleep", "infinity"
            ]
            
            stdout, stderr, exit_code = run_cmd(cmd)
            
            if exit_code != 0:
                error_msg = f"Failed to create container: {stderr}"
                return False, "", error_msg, ""
            
            container_id = stdout.strip()
            
            # Install git in container
            cmd = ["docker", "exec", container_name, "apk", "add", "--no-cache", "git"]
            stdout, stderr, exit_code = run_cmd(cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to install git: {stderr}"
                return False, container_id, error_msg, ""
            
            # Create repo directory
            cmd = ["docker", "exec", container_name, "mkdir", "-p", REPO_DIR]
            stdout, stderr, exit_code = run_cmd(cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to create repo directory: {stderr}"
                return False, container_id, error_msg, ""
            
            # Clone repository
            cmd = ["docker", "exec", "--workdir", REPO_DIR, container_name, "git", "clone", repo_url, "."]
            stdout, stderr, exit_code = run_cmd(cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to clone repository: {stderr}"
                return False, container_id, error_msg, ""
            
            # Get commit ID
            cmd = ["docker", "exec", "--workdir", REPO_DIR, container_name, "git", "rev-parse", "HEAD"]
            stdout, stderr, exit_code = run_cmd(cmd)
            
            if exit_code != 0:
                self._cleanup_container(container_name)
                error_msg = f"Failed to get commit ID: {stderr}"
                return False, container_id, error_msg, ""
            
            commit_id = stdout.strip()
        
        return True, container_id, f"Successfully cloned {repo_url}", commit_id
    
    def _cleanup_container(self, container_name: str) -> None:
        """Clean up a container on the Playerzero Ubuntu server or locally
        
        Args:
            container_name: Name of the container to clean up
        """
        try:
            if self.use_remote:
                # Stop the container on remote server
                remote_cmd = f"docker stop {container_name}"
                self._run_ssh_command(remote_cmd)
                
                # Remove the container from remote server
                remote_cmd = f"docker rm {container_name}"
                self._run_ssh_command(remote_cmd)
            else:
                # Stop the container locally
                cmd = ["docker", "stop", container_name]
                run_cmd(cmd)
                
                # Remove the container locally
                cmd = ["docker", "rm", container_name]
                run_cmd(cmd)
        except Exception as e:
            logger.info(f"Error cleaning up container {container_name}: {str(e)}")
    
    def get_repo_structure(self, container_name: str) -> Dict[str, Any]:
        """Analyze repository structure to identify key components on the Playerzero Ubuntu server or locally.
            
        Args:
            container_name: Name of the container with the cloned repository
                
        Returns:
            Dictionary with repository structure information
        """
        structure = {
            "has_setup_py": False,
            "has_requirements_txt": False,
            "has_pyproject_toml": False,
            "has_tests_dir": False,
            "has_pytest_ini": False,
            "has_conftest_py": False,
            "has_tox_ini": False,
            "python_files": 0,
            "test_files": 0,
        }
        
        try:
            # Check for key files
            for file_path in ["setup.py", "requirements.txt", "pyproject.toml", "pytest.ini", "conftest.py", "tox.ini"]:
                if self.use_remote:
                    remote_cmd = f"docker exec {container_name} test -f {REPO_DIR}/{file_path} && echo 'exists' || echo 'not found'"
                    exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
                else:
                    cmd = ["docker", "exec", container_name, "sh", "-c", f"test -f {REPO_DIR}/{file_path} && echo 'exists' || echo 'not found'"]
                    stdout, stderr, exit_code = run_cmd(cmd)
                
                if stdout.strip() == "exists":
                    key = f"has_{file_path.replace('.', '_')}"
                    if key in structure:
                        structure[key] = True
            
            # Check for tests directory
            if self.use_remote:
                remote_cmd = f"docker exec {container_name} test -d {REPO_DIR}/tests && echo 'exists' || echo 'not found'"
                exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            else:
                cmd = ["docker", "exec", container_name, "sh", "-c", f"test -d {REPO_DIR}/tests && echo 'exists' || echo 'not found'"]
                stdout, stderr, exit_code = run_cmd(cmd)
                
            structure["has_tests_dir"] = (stdout.strip() == "exists")
            
            # Count Python files
            if self.use_remote:
                remote_cmd = f"docker exec {container_name} find {REPO_DIR} -name '*.py' | wc -l"
                exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            else:
                cmd = ["docker", "exec", container_name, "sh", "-c", f"find {REPO_DIR} -name '*.py' | wc -l"]
                stdout, stderr, exit_code = run_cmd(cmd)
                
            structure["python_files"] = int(stdout.strip()) if stdout.strip().isdigit() else 0
            
            # Count test files
            if self.use_remote:
                remote_cmd = f"docker exec {container_name} find {REPO_DIR} -name 'test_*.py' -o -name '*_test.py' | wc -l"
                exit_code, stdout, stderr = self._run_ssh_command(remote_cmd)
            else:
                cmd = ["docker", "exec", container_name, "sh", "-c", f"find {REPO_DIR} -name 'test_*.py' -o -name '*_test.py' | wc -l"]
                stdout, stderr, exit_code = run_cmd(cmd)
                
            structure["test_files"] = int(stdout.strip()) if stdout.strip().isdigit() else 0
        except Exception as e:
            logger.info(f"Error analyzing repository structure: {str(e)}")
            
        return structure
    
    def process_repo(self, repo_url: str, keep_container: bool = False) -> Dict[str, Any]:
        """
        Process a repository on the Playerzero Ubuntu server or locally.
        
        Args:
            repo_url: URL of the GitHub repository to process
            keep_container: Whether to keep the container after processing
            
        Returns:
            Dictionary with repository information
        """
        # Verify SSH connection before processing
        self._verify_connection()
        
        # Clone the repository
        success, container_id, output, commit_id = self.clone_repo(repo_url)
        
        if not success:
            return {
                "repo_url": repo_url,
                "success": False,
                "output": output,
                "commit_id": "",
                "structure": {},
                "remote_execution": self.use_remote
            }
        
        # Extract container name from ID
        container_name = container_id.strip()
        
        # Analyze repository structure
        structure = self.get_repo_structure(container_name)
        
        # Clean up container if not keeping it
        if not keep_container:
            self._cleanup_container(container_name)
        
        return {
            "repo_url": repo_url,
            "success": True,
            "output": output,
            "commit_id": commit_id,
            "structure": structure,
            "container_name": container_name if keep_container else "",
            "remote_execution": self.use_remote
        }
    
    def process_repos_parallel(self, repo_urls: List[str], max_parallel: int = DEFAULT_MAX_PARALLEL) -> List[Dict[str, Any]]:
        """Process multiple repositories in parallel on the Playerzero Ubuntu server or locally.
        
        Args:
            repo_urls: List of repository URLs to process
            max_parallel: Maximum number of parallel processes
            
        Returns:
            List of dictionaries with repository processing results
        """
        # Verify SSH connection before processing
        self._verify_connection()
        
        location = "Playerzero Ubuntu server" if self.use_remote else "local Docker"
        logger.info(f"Processing {len(repo_urls)} repositories in parallel (max {max_parallel}) using {location}")
        
        results = []
        
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            future_to_url = {executor.submit(self.process_repo, url): url for url in repo_urls}
            
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"Processed {url}: {'Success' if result['success'] else 'Failed'}")
                except Exception as e:
                    logger.info(f"Error processing {url}: {str(e)}")
                    results.append({
                        "repo_url": url,
                        "success": False,
                        "output": f"Exception: {str(e)}",
                        "commit_id": "",
                        "structure": {},
                        "remote_execution": self.use_remote
                    })
        
        return results
        
    def process_validated_repos(self, db_name: str = DEFAULT_DB_NAME, max_parallel: int = DEFAULT_MAX_PARALLEL) -> List[Dict[str, Any]]:
        """Process repositories that have been validated but not yet tested on the Playerzero Ubuntu server or locally.
        
        Args:
            db_name: Name of the database to use
            max_parallel: Maximum number of parallel processes
            
        Returns:
            List of dictionaries with repository processing results
        """
        # Get validated repositories from the database
        repos = get_validated_repos(db_name)
        
        if not repos:
            logger.info("No validated repositories found in the database")
            return []
        
        logger.info(f"Found {len(repos)} validated repositories to process")
        
        # Process repositories in parallel
        return self.process_repos_parallel([repo["repo_url"] for repo in repos], max_parallel)

# ---------------------------------------------
# CLI helper (python -m agents.clone_agent)
# ---------------------------------------------
def main():
    """Main function for command-line execution."""
    parser = argparse.ArgumentParser(description="Clone and analyze GitHub repositories in Docker containers on Playerzero Ubuntu server")
    parser.add_argument("--repo", help="GitHub repository URL to clone")
    parser.add_argument("--work-dir", default="/tmp/repo_tests", help="Working directory for mounting into containers")
    parser.add_argument("--ssh-host", default=PLAYERZERO_SSH_HOST, help="SSH hostname for Playerzero Ubuntu server")
    parser.add_argument("--ssh-user", default=PLAYERZERO_SSH_USER, help="SSH username for Playerzero Ubuntu server")
    parser.add_argument("--ssh-key", default=PLAYERZERO_SSH_KEY_PATH, help="SSH private key path for Playerzero Ubuntu server")
    parser.add_argument("--ssh-port", default=PLAYERZERO_SSH_PORT, help="SSH port for Playerzero Ubuntu server")
    parser.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL, help="Maximum number of parallel processes")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, help="Database name to use")
    
    args = parser.parse_args()
    
    agent = CloneAgent(
        ssh_host=args.ssh_host,
        ssh_user=args.ssh_user,
        ssh_key_path=args.ssh_key,
        ssh_port=args.ssh_port,
        work_dir=args.work_dir
    )
    
    if args.repo:
        # Process a single repository
        result = agent.process_repo(args.repo)
        logger.info(json.dumps(result, indent=2))
    else:
        # Process all validated repositories
        results = agent.process_validated_repos(args.db_name, args.max_parallel)
        logger.info(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
