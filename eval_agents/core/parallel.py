"""parallel.py

Lightweight placeholder implementation for `ParallelTestRunner` used by
`eval_agents.core.repo`.  In the current simplified workflow we run tests
sequentially, but `ParallelTestRunner` keeps the public interface stable for
future parallel execution.

If you later need real parallelism you can swap in a multiprocessing or
async-based implementation without touching callers.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

class ParallelTestRunner:
    """Trivial sequential fallback.

    Parameters are accepted for API compatibility but currently ignored.
    """

    def __init__(self, *, ssh_host: str | None = None, ssh_user: str | None = None,
                 ssh_key_path: str | None = None, ssh_port: str | None = None,
                 work_dir: str = "/tmp/repo_tests", max_parallel: int = 4):
        self.work_dir = work_dir
        self.max_parallel = max_parallel
        logger.info("ParallelTestRunner initialised (sequential fallback, max_parallel=%s)", max_parallel)

    # pylint: disable=unused-argument
    def process_repos_parallel(self, repo_urls: List[str]):
        """Process repo URLs sequentially for now.

        Returns a list of dummy result dictionaries; replace with real
        orchestration logic as needed.
        """
        results = []
        for url in repo_urls:
            logger.info("Processing repo %s (sequential placeholder)", url)
            results.append({"repo_url": url, "status": "processed"})
        return results

    # Backward-compat shim – remove after callers are updated.
    process_repos_paralsslel = process_repos_parallel  # type: ignore
