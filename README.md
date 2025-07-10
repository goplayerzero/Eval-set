# Repo-Runner

A modular, multi-agent system that automatically clones public GitHub repositories, analyses their structure, locates & installs dependencies, detects and executes integration tests inside reproducible Docker sandboxes, then stores richly-structured results.

A system for evaluating Python repositories using Docker containers and AI assistance.

## Architecture

```
┌──────────────────┐      ┌────────────────────┐      ┌────────────────────┐      ┌──────────────────┐
│ DiscoveryAgent   │──►──│ RepoValidationAgent │──►──│   CloneAgent        │──►──│   TestAgent       │
└──────────────────┘      └────────────────────┘      └────────────────────┘      └──────────────────┘
                                                                                          │
                                                                                          ▼
                                                                                   ┌──────────────────┐
                                                                                   │  ResultAgent     │
                                                                                   └──────────────────┘
```

* **DiscoveryAgent** — Searches the GitHub API for candidate repositories by language, stars, etc.
* **RepoValidationAgent** — Uses LLM analysis to confirm that the repository contains integration tests and meets basic quality criteria.
* **CloneAgent** — Clones the repository into a clean Docker container. Supports local Docker only (SSH variables are retained for future expansion).
* **TestAgent** — Installs project dependencies, detects integration tests, and executes them using claude code.
* **ResultAgent** — Parses raw test output, assesses validity, and serialises results to JSON.

All agents communicate solely via Python objects or JSON, allowing them to be orchestrated by higher-level workflows, schedulers, or CI pipelines.
- **TestAgent**: Manages dependency installation, test discovery, and test execution
- **ResultAgent**: Processes and analyzes raw test outputs

## Quick-start

- **integrated_workflow.py**: Main workflow script that orchestrates all agents
- **direct_test.py**: Simple script for verifying test output capture
- **test_agent_check.py**: Utility to verify TestAgent functionality

### 1. Clone and install

1. Install dependencies: `pip install -r requirements.txt`
2. Set up environment variables in `.env`:
   - `CLAUDE_API_KEY`: API key for Anthropic Claude

### 2. Environment variables

### Prerequisites

- Docker installed and running
- Python 3.8+ with required dependencies
- Claude API key (set as `ANTHROPIC_API_KEY` environment variable)
- PostgreSQL database (optional, for fetching repos)

### Running Tests

```bash
# Test specific repositories in parallel (2 concurrent workers)
python scripts/run_parallel.py --parallel 2 --repos https://github.com/user/repo1 https://github.com/user/repo2

# Pull validated but untested repos from database (5 concurrent workers)
python scripts/run_parallel.py --parallel 5 --batch-size 10

# Debug a single repository
python scripts/single_repo_debug.py --url https://github.com/user/repo
```

### 3. Command-line examples

- `ANTHROPIC_API_KEY`: Claude API key
- `DATABASE_URL`: PostgreSQL connection string (default: postgresql://localhost/eval_agents)
- `PARALLEL_LIMIT`: Default number of parallel workers (default: 3)
- `BATCH_SIZE`: Default batch size for DB fetches (default: 10)
