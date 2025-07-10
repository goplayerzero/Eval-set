import os
import tempfile
import uuid
from contextlib import contextmanager
from typing import Generator, Tuple

import docker

# Default Docker image used for test runners
DEFAULT_IMAGE = os.getenv("EVAL_AGENTS_DOCKER_IMAGE", "python:3.13-alpine")

# Memory/RAM limit per container – override via env if you like
MEM_LIMIT = os.getenv("EVAL_AGENTS_MEM", "2g")


class ContainerPool:
    """Simple synchronous container pool.

    For now this is *not* a true pooled implementation with a queue – it is a
    thin convenience wrapper that centralises container lifecycle logic taken
    from CloneAgent.  Orchestrator/Worker code will call :pyfunc:`acquire` which
    yields a live Docker container bound to a unique workspace directory, and
    then automatically cleans it up afterwards.

    Later we can replace this with an asyncio-aware implementation without
    touching callers.
    """

    def __init__(self):
        self.client = docker.from_env()

    @contextmanager
    def acquire(self) -> Generator[Tuple[str, str], None, None]:
        """Spin up a fresh container and yield (container_id, workdir).

        The workspace dir is a unique host tmpdir mounted into the container at
        /workspace.  Caller **must** chdir or set ``workdir`` explicitly when
        executing commands inside the container.
        """
        container = None
        workspace_dir = tempfile.mkdtemp(prefix="repo_test_")
        container_name = f"eval_agents_{uuid.uuid4().hex[:8]}"

        try:
            container = self.client.containers.run(
                DEFAULT_IMAGE,
                command="sleep infinity",
                name=container_name,
                detach=True,
                volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                mem_limit=MEM_LIMIT,
            )
            yield container.id, workspace_dir
        finally:
            # Stop & remove container if it exists
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            # Remove workspace dir
            try:
                os.system(f"rm -rf {workspace_dir}")
            except Exception:
                pass


# Singleton helper – most callers can just `from ...container_pool import pool`
pool = ContainerPool()
