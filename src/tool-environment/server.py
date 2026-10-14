import os
import resource
import subprocess
import sys
import threading
import traceback

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

# No app-level auth
# Lambda MicroVMs terminates and authenticates every inbound
# request with its own short-lived JWE token (X-aws-proxy-auth) before it
# reaches this container.
app = FastAPI(title="Firecracker Agent Tool Sandbox API")

# Lifecycle hooks are served on a separate port from application traffic, via
# the MicroVM image's `hooks.port` config. Lambda calls these directly, they
# are not reachable through the public MicroVM endpoint.
HOOKS_PATH_PREFIX = "/aws/lambda-microvms/runtime/v1"
hooks_app = FastAPI(title="MicroVM Lifecycle Hooks")


@hooks_app.post(f"{HOOKS_PATH_PREFIX}/ready")
@hooks_app.post(f"{HOOKS_PATH_PREFIX}/run")
@hooks_app.post(f"{HOOKS_PATH_PREFIX}/resume")
@hooks_app.post(f"{HOOKS_PATH_PREFIX}/suspend")
@hooks_app.post(f"{HOOKS_PATH_PREFIX}/terminate")
async def lifecycle_hook():
    return {}


class CodeExecutionRequest(BaseModel):
    code: str
    timeout: int = 30  # Default strict execution window


class ExecutionResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    error: str = ""


# Bounds one submitted script's own resource usage, independent of whatever
# the MicroVM itself is allocated, so a single tool call can't starve this
# server process of memory/processes/file descriptors for the rest of the
# session. RLIMIT_AS is conservative because CPython (and libraries like
# numpy) can reserve large virtual address space without using it
MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_CPU_SECONDS = 60
MAX_PROCESSES = 32
MAX_OPEN_FILES = 256
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024


def _limit_subprocess_resources() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))
    resource.setrlimit(
        resource.RLIMIT_FSIZE, (MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_BYTES)
    )


@app.post("/execute/python", response_model=ExecutionResult)
async def execute_python_code(request: CodeExecutionRequest):
    """
    Executes raw python strings submitted by an external agent orchestrator.
    Runs inside the isolated Guest Kernel space under a non-root sandbox user.
    """
    execution_env = os.environ.copy()
    execution_env["PYTHONUNBUFFERED"] = "1"
    # /workspace persists and is writable across the whole session
    # (suspend/resume)
    execution_env["PYTHONSAFEPATH"] = "1"

    try:
        process = subprocess.run(
            [sys.executable, "-c", request.code],
            capture_output=True,
            text=True,
            timeout=request.timeout,
            cwd="/workspace",
            env=execution_env,
            preexec_fn=_limit_subprocess_resources,
            check=False,
        )
        return ExecutionResult(
            stdout=process.stdout, stderr=process.stderr, exit_code=process.returncode
        )
    except subprocess.TimeoutExpired as e:
        return ExecutionResult(
            stdout=e.stdout if e.stdout else "",
            stderr=(
                e.stderr
                if e.stderr
                else f"Execution timed out after {request.timeout} seconds."
            ),
            exit_code=124,
            error="TimeoutExpired",
        )
    except Exception as e:
        return ExecutionResult(
            stdout="",
            stderr=traceback.format_exc(),
            exit_code=1,
            error=str(type(e).__name__),
        )


def _run_hooks_server():
    uvicorn.run(hooks_app, host="0.0.0.0", port=9000, log_level="warning")


if __name__ == "__main__":
    threading.Thread(target=_run_hooks_server, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8080)
