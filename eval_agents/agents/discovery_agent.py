"""
DiscoveryAgent

Searches GitHub for repositories in a specified language and adds them to a database
for later processing. Optimized to find repositories with test directories and integration tests.

Usage example:
-------------
>>> from agents.discovery_agent import DiscoveryAgent
>>> agent = DiscoveryAgent(language="python")
>>> repos = agent.discover_repos(limit=10)
>>> logger.info(repos)  # List of repository URLs
"""

import os
import sys
import time
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv
import logging

# Load environment variables from .env file (if present)
load_dotenv()

# Configure module-level logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Import database utilities
from eval_agents.core.utils import (
    add_repo_to_db,
    is_repo_in_db,
    init_db,
    DEFAULT_DB_NAME,
)

# ---------------------------------
# Helper functions
# ---------------------------------

def _github_request(path: str, params: Optional[Dict] = None, max_retries: int = 3) -> requests.Response:
    """Make an authenticated request to the GitHub API.
    
    Handles rate limiting by waiting when necessary and implements retry logic
    for transient failures.
    
    Args:
        path: API endpoint path
        params: Query parameters
        max_retries: Maximum number of retry attempts
        
    Returns:
        Response object from the GitHub API
        
    Raises:
        requests.HTTPError: If the request fails after all retries
    """
    headers = {"Accept": "application/vnd.github+json"}

    # Increased rate limits by using a GitHub token
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com{path}"
    
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            
            # Check for rate limiting
            if resp.status_code == 403 and 'rate limit exceeded' in resp.text.lower():
                reset_time = int(resp.headers.get('X-RateLimit-Reset', 0))
                wait_time = max(reset_time - time.time(), 0) + 3  # Add 3 seconds buffer
                
                if wait_time > 60 and attempt < max_retries - 1:  # Don't wait on last attempt
                    logger.info(f"Rate limit exceeded. Waiting {wait_time:.0f} seconds...")
                    time.sleep(min(wait_time, 60))  # Wait at most 60 seconds
                    continue
            
            resp.raise_for_status()
            return resp
            
        except requests.HTTPError as exc:
            if attempt < max_retries - 1 and resp.status_code in (429, 500, 502, 503, 504):
                # Retry on rate limit or server errors
                wait_time = 2 ** attempt  # Exponential backoff
                logger.info(f"Request failed with {resp.status_code}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            logger.info(f"GitHub API error for {url}: {resp.text}", file=sys.stderr)
            raise exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"Connection error. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            raise exc
    
    # This should not be reached, but just in case
    raise RuntimeError(f"Failed to get response from {url} after {max_retries} attempts")


@dataclass
class DiscoveryAgent:
    """Agent that discovers GitHub repositories and adds them to a database."""
    
    language: str = "python"  # Set the language to search for
    db_name: str = DEFAULT_DB_NAME
    min_stars: int = 5  # Minimum number of stars to filter by
    custom_query: Optional[str] = None  # Custom query to add to the search
    
    def __post_init__(self):
        """Initialize the database if it doesn't exist."""
        init_db(self.db_name)
    
    def discover_repos(self, limit: int = 10, integration_tests: bool = True) -> List[str]:
        """Discover GitHub repositories and add them to the database.
        
        Args:
            limit: Maximum number of repositories to discover
            integration_tests: Prioritize repositories with integration tests
            
        Returns:
            List of repository URLs that were added to the database
        """
        logger.info(f"Searching for {self.language} repositories...")
        
        if integration_tests:
            logger.info("Prioritizing repositories with integration tests")
            
        # Search for repositories
        repos = self._search_repositories(limit=limit, integration_tests=integration_tests)
        
        # Add repositories to database
        added_repos = []
        for repo in repos:
            repo_url = repo["html_url"]
            if add_repo_to_db(repo_url, self.language, self.db_name):
                logger.info(f"Added repository: {repo_url}")
                added_repos.append(repo_url)
        
        logger.info(f"Added {len(added_repos)} new repositories to the database")
        return added_repos
    
    def _search_repositories(self, limit: int = 10, integration_tests: bool = True) -> List[Dict]:
        """Search for repositories on GitHub with optimized filtering for integration tests.
        
        Args:
            limit: Maximum number of repositories to return
            integration_tests: If True, prioritize repositories likely to have integration tests
            
        Returns:
            List of repository data dictionaries from GitHub API
        """
        # Base query parameters - language-specific but structure is language-agnostic
        base_query = f"language:{self.language} stars:>={self.min_stars} archived:false fork:false"
        
        # Add custom query if specified
        if self.custom_query:
            base_query = f"{base_query} {self.custom_query}"
            logger.info(f"Using custom query: {self.custom_query}")
        
        if not integration_tests:
            # Standard search without integration test filtering
            return self._execute_search_with_pagination(base_query, limit)
        
        # Enhanced search for repositories with integration tests
        logger.info("Using enhanced search for integration tests")
        
        # Define search strategies targeting test directories and integration tests
        # These are more focused on finding repos with actual test directories
        integration_queries = [
            # Test directories - very common pattern
            f"{base_query} path:test",
            f"{base_query} path:tests",
            
            # Integration test directories and files
            f"{base_query} path:integration",
            f"{base_query} integration test in:file",
            f"{base_query} integration_test in:file",
            
            # Common test frameworks
            f"{base_query} pytest in:file",
            f"{base_query} unittest in:file",
            
            # CI configurations with test references
            f"{base_query} test in:file path:.github/workflows",
            f"{base_query} test in:file path:.travis.yml",
            
            # Docker configurations often used for integration tests
            f"{base_query} docker-compose in:file test in:file",
        ]
        
        # Add language-specific queries
        language_queries = self._get_language_specific_queries()
        integration_queries.extend([f"{base_query} {q}" for q in language_queries])
        
        # Track repositories and their search match count
        repo_matches = {}
        repo_data = {}
        
        # Calculate how many results we need per query to reach our limit
        # This is a heuristic - we'll get more results than needed and filter later
        results_per_query = min(100, max(25, limit // len(integration_queries) * 2))
        
        # Execute each search strategy
        for i, query in enumerate(integration_queries):
            try:
                logger.info(f"Search query {i+1}/{len(integration_queries)}")
                repos = self._execute_search_with_pagination(query, results_per_query)
                
                # Track match frequency for each repository
                for repo in repos:
                    repo_url = repo["html_url"]
                    repo_matches[repo_url] = repo_matches.get(repo_url, 0) + 1
                    repo_data[repo_url] = repo
                
                logger.info(f"Found {len(repos)} repositories with query {i+1}")
                
                # If we have enough matches, we can stop early
                if len(repo_matches) >= limit * 3:  # Get 3x more than needed for better ranking
                    logger.info(f"Found enough repositories ({len(repo_matches)}), stopping search")
                    break
                    
            except Exception as e:
                logger.info(f"Error in search query {i+1}: {str(e)}")
        
        # Sort repositories by match frequency (descending)
        # This prioritizes repos that matched multiple integration test patterns
        sorted_repos = sorted(
            [(url, count) for url, count in repo_matches.items()],
            key=lambda x: x[1],
            reverse=True
        )
        
        # Get the top repositories based on match frequency
        top_repos = [repo_data[url] for url, _ in sorted_repos[:limit]]
        
        # If we didn't find enough repos with integration tests, fall back to regular search
        if len(top_repos) < limit:
            remaining = limit - len(top_repos)
            logger.info(f"Found only {len(top_repos)} repositories with integration tests, falling back to standard search for {remaining} more")
            
            try:
                fallback_repos = self._execute_search_with_pagination(base_query, remaining)
                
                # Filter out repos we already found
                existing_urls = {repo["html_url"] for repo in top_repos}
                new_repos = [repo for repo in fallback_repos if repo["html_url"] not in existing_urls]
                
                top_repos.extend(new_repos[:remaining])
                logger.info(f"Added {len(new_repos[:remaining])} repositories from fallback search")
            except Exception as e:
                logger.info(f"Error in fallback search: {str(e)}")
        
        return top_repos
    
    def _execute_search(self, query: str, limit: int) -> List[Dict]:
        """Execute a GitHub search query.
        
        Args:
            query: The search query string
            limit: Maximum number of results to return
            
        Returns:
            List of repository data dictionaries
        """
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": min(100, limit)}
        resp = _github_request("/search/repositories", params=params).json()
        return resp.get("items", [])[:limit]
    
    def _execute_search_with_pagination(self, query: str, limit: int) -> List[Dict]:
        """Execute a GitHub search query with pagination support.
        
        Args:
            query: The search query string
            limit: Maximum number of results to return
            
        Returns:
            List of repository data dictionaries
        """
        all_repos = []
        page = 1
        per_page = min(100, limit)  # GitHub API max is 100 per page
        
        while len(all_repos) < limit:
            params = {
                "q": query, 
                "sort": "stars", 
                "order": "desc", 
                "per_page": per_page,
                "page": page
            }
            
            try:
                resp = _github_request("/search/repositories", params=params)
                repos = resp.json().get("items", [])
                
                # If no more results, break
                if not repos:
                    break
                    
                all_repos.extend(repos)
                
                # Check if there are more pages
                if 'Link' not in resp.headers or 'rel="next"' not in resp.headers['Link']:
                    break
                    
                # Move to next page
                page += 1
                
                # Add a small delay to avoid hitting rate limits
                time.sleep(0.5)
                
            except Exception as e:
                logger.info(f"Error in paginated search (page {page}): {str(e)}")
                break
        
        return all_repos[:limit]
    
    def _get_language_specific_queries(self) -> List[str]:
        """Get language-specific test pattern queries.
        
        Returns:
            List of language-specific search terms
        """
        patterns = {
            "python": [
                "pytest",
                "unittest",
                "conftest.py",
                "requirements-test.txt",
            ],
            "javascript": [
                "jest",
                "mocha",
                "cypress",
                "test:integration",
            ],
            "java": [
                "@Test",
                "JUnit",
                "TestNG",
                "maven-surefire-plugin",
            ],
            "go": [
                "testing.T",
                "_test.go",
                "testify",
            ],
            "ruby": [
                "rspec",
                "minitest",
                "test_helper",
            ],
        }
        
        return patterns.get(self.language.lower(), [])


# ---------------------------------------------
# CLI helper (python -m agents.discovery_agent)
# ---------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover GitHub repositories.")
    parser.add_argument("--lang", default="python", help="Programming language to search for")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, help="Name of the PostgreSQL database")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of repositories to discover")
    parser.add_argument("--min-stars", type=int, default=20, help="Minimum number of stars")
    parser.add_argument("--integration-tests", action="store_true", default=True, 
                        help="Prioritize repositories with integration tests")
    parser.add_argument("--no-integration-tests", action="store_false", dest="integration_tests",
                        help="Don't prioritize repositories with integration tests")
    parser.add_argument("--custom-query", type=str, default=None,
                        help="Custom query to add to the search")
    args = parser.parse_args()
    
    # Create agent
    agent = DiscoveryAgent(
        language=args.lang,
        db_name=args.db_name,
        min_stars=args.min_stars,
        custom_query=args.custom_query
    )
    
    # Discover repositories
    repos = agent.discover_repos(limit=args.limit, integration_tests=args.integration_tests)
    
    # Print results
    logger.info(f"\nDiscovered {len(repos)} new repositories:")
    for repo in repos:
        logger.info(f"- {repo}")
