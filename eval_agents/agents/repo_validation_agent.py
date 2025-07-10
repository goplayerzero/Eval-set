"""RepoValidationAgent

Validates GitHub repositories by checking if they contain integration tests
and meet other requirements for smooth testing and logging.

Usage example:
-------------
>>> from agents.repo_validation_agent import RepoValidationAgent
>>> agent = RepoValidationAgent()
>>> result = agent.validate_repo("https://github.com/username/repo")
>>> logger.info(result)  # True if valid, False otherwise

Implementation notes:
-------------------
- Uses Claude API to analyze repository content
- Updates PostgreSQL database with validation results
- Checks for integration tests and other required components
- No local cloning required - analysis happens via the GitHub API and LLM
"""

import os
import sys
import json
import requests
import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import anthropic
import openai
import logging
from dotenv import load_dotenv

# Load environment variables from .env file (if present)
load_dotenv()

# Configure logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Import database utilities
from eval_agents.core.utils import update_validation_results, get_unvalidated_repos
from eval_agents.core.utils import DEFAULT_DB_NAME

# ---------------------------------
# Helper functions
# ---------------------------------

def _get_repo_structure(repo_url: str) -> Dict[str, Any]:
    """Get the file structure of a GitHub repository.
    
    Args:
        repo_url: URL of the GitHub repository
        
    Returns:
        Dictionary containing repository structure information
    """
    # Extract owner and repo name from URL
    parts = repo_url.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Invalid GitHub URL: {repo_url}")
    
    owner = parts[-2]
    repo = parts[-1]
    
    # Get repository contents using GitHub API
    headers = {}
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    
    # Get repository information
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    repo_response = requests.get(api_url, headers=headers)
    if repo_response.status_code != 200:
        raise ValueError(f"Failed to fetch repository info: {repo_response.text}")
    
    repo_info = repo_response.json()
    
    # Get file structure (recursive tree)
    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    tree_response = requests.get(tree_url, headers=headers)
    if tree_response.status_code != 200:
        raise ValueError(f"Failed to fetch repository structure: {tree_response.text}")
    
    # Combine repo info with file structure
    result = {
        "name": repo_info["name"],
        "description": repo_info["description"],
        "language": repo_info["language"],
        "files": [item["path"] for item in tree_response.json().get("tree", []) if item["type"] == "blob"],
        "url": repo_url
    }
    
    return result

def _analyze_with_openai(repo_data: Dict[str, Any]) -> Tuple[bool, str]:
    """Analyze repository with OpenAI API to check for integration tests.
    
    Args:
        repo_data: Repository structure and information
        
    Returns:
        Tuple of (is_valid, explanation)
    """
    # Get OpenAI API key
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY not found in environment variables")
    
    # Initialize OpenAI client
    client = openai.OpenAI(api_key=openai_api_key)
    
    # Create a more comprehensive system prompt for OpenAI to robustly detect integration tests without relying on hard-coded
    # filenames or directory names.
    #
    # The prompt now instructs the model to:
    #   • Look for *any* patterns that typically indicate integration testing, including CI workflows, test runners invoked
    #     via scripts, Docker-compose files, etc.
    #   • Consider non-Python repos as well (e.g. JS, Go) and account for language-specific conventions.
    #   • Return a strict JSON object with a boolean `is_valid` and a concise `explanation`.
    #   • Be conservative: mark a repo valid only if clear evidence of *integration* (cross-component / end-to-end) tests exists.
    #
    # This avoids brittle heuristics so future repos with unconventional structures are still evaluated correctly.
    system_prompt = """
You are an expert open-source repository auditor. Your task is to decide whether a given GitHub project contains
*integration tests* and is therefore suitable for automated execution in a clean container.

Definition – integration tests exercise multiple components of the application working together and usually require
more environment set-up than unit tests.  Evidence may include (but is not limited to):
  • folders or files whose names contain: integration, e2e, functional, acceptance, system, scenario, smoke
  • a generic `tests/` or `test/` directory that contains infrastructure-level fixtures (e.g. docker-compose files,
    database containers, external service mocks) rather than only isolated unit-level mocks
  • CI/CD workflows (e.g. files under `.github/workflows`, `.gitlab-ci.yml`, `azure-pipelines.yml`) that run commands
    like `pytest -m integration`, `npm run test:e2e`, `go test ./... -tags=integration`, etc.
  • Docker-compose, Kubernetes manifests or custom bash scripts invoked from CI specifically for testing
  • Makefile or package-json scripts that start services or run test suites spanning multiple components

When you judge evidence, remember that:
  • Presence of *unit tests only* (names like `test_*.py`, `*_test.go`, etc.) is NOT sufficient.
  • Documentation (`README`, `CONTRIBUTING`, `docs/`) can provide explicit instructions such as “Run \`make
    integration-test\`” – treat that as evidence.
  • Language varies: pytest, nose2, JUnit, Maven Failsafe, Gradle integrationTest task, Jest e2e suites, Cypress,
    Playwright, etc.

Return **exactly** the following JSON structure (no additional keys):
{
  "is_valid": <true|false>,
  "explanation": "<short human explanation (max 50 words)>"
}

Think step-by-step and be strict: only output `true` when at least one *clear* sign of integration testing is present.
"""

    
    # Format repository data for the prompt
    repo_info = f"""
    Repository Information:
    - Name: {repo_data.get('name', 'Unknown')}
    - Description: {repo_data.get('description', 'No description')}
    - Language: {repo_data.get('language', 'Unknown')}
    - URL: {repo_data.get('url', 'Unknown')}
    
    File Structure (up to 100 files):
    {json.dumps(repo_data.get('files', [])[:100], indent=2)}
    """
    
    try:
        # Call OpenAI API
        response = client.chat.completions.create(
            model="o3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": repo_info}
            ],
            response_format={"type": "json_object"}
        )
        
        # Parse response
        try:
            result = json.loads(response.choices[0].message.content)
            return result["is_valid"], result["explanation"]
        except (json.JSONDecodeError, KeyError) as e:
            # If JSON parsing fails, try to extract a yes/no answer from the text
            text = response.choices[0].message.content.lower()
            if "valid" in text and ("yes" in text or "true" in text):
                return True, "Repository appears valid based on OpenAI analysis"
            else:
                return False, "Repository does not meet requirements based on OpenAI analysis"
    
    except Exception as e:
        logger.info(f"Error calling OpenAI API: {str(e)}")
        return False, f"Error during validation: {str(e)}"

@dataclass
class RepoValidationAgent:
    """Agent that validates GitHub repositories for integration tests."""
    
    db_name: str = DEFAULT_DB_NAME
    
    def validate_repo(self, repo_url: str) -> Tuple[bool, str]:
        """Validate a GitHub repository.
        
        Args:
            repo_url: URL of the GitHub repository to validate
            
        Returns:
            Tuple of (is_valid, explanation)
        """
        logger.info(f"Validating repository: {repo_url}")
        
        try:
            # Get repository structure
            repo_data = _get_repo_structure(repo_url)
            
            # Analyze with OpenAI
            is_valid, explanation = _analyze_with_openai(repo_data)
            
            # Update database
            update_validation_results(repo_url, is_valid, explanation, self.db_name)
            
            logger.info(f"Validation result: {'PASS' if is_valid else 'FAIL'}")
            logger.info(f"Explanation: {explanation}")
            
            return is_valid, explanation
            
        except Exception as e:
            error_msg = f"Error validating repository: {str(e)}"
            logger.info(f"{error_msg}")
            
            # Update database with failure
            update_validation_results(repo_url, False, error_msg, self.db_name)
            
            return False, error_msg
    
    def validate_batch(self, limit: int = 10) -> List[Tuple[str, bool, str]]:
        """Validate a batch of unvalidated repositories from the database.
        
        Args:
            limit: Maximum number of repositories to validate
            
        Returns:
            List of tuples containing (repo_url, is_valid, explanation)
        """
        logger.info(f"Validating up to {limit} repositories...")
        
        # Get unvalidated repositories
        repos = get_unvalidated_repos(limit=limit, db_name=self.db_name)
        
        results = []
        for repo_url in repos:
            is_valid, explanation = self.validate_repo(repo_url)
            results.append((repo_url, is_valid, explanation))
        
        logger.info(f"Validated {len(results)} repositories")
        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate GitHub repositories for integration tests.")
    parser.add_argument("--url", help="URL of a specific repository to validate")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, help="Name of the PostgreSQL database")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of repositories to validate")
    args = parser.parse_args()
    
    # Create agent
    agent = RepoValidationAgent(db_name=args.db_name)
    
    if args.url:
        # Validate a specific repository
        is_valid, explanation = agent.validate_repo(args.url)
        logger.info(f"\nRepository validation result: {'PASS' if is_valid else 'FAIL'}")
        logger.info(f"Explanation: {explanation}")
    else:
        # Validate a batch of repositories
        results = agent.validate_batch(limit=args.limit)
        
        # Print results
        logger.info("\nValidation Results:")
        for repo_url, is_valid, explanation in results:
            status = "PASS" if is_valid else "FAIL"
            logger.info(f"- {repo_url}: {status}")
