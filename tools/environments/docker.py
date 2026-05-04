"""Docker execution environment tool for Hermès - Run commands in isolated Docker containers."""

from typing import Any, Callable, Optional, List, Dict
import time

from tools.registry import registry, tool_result, tool_error
from tools.utils import is_dangerous_command, redact_sensitive_text

DOCKER_AVAILABLE = False
docker = None

try:
    import docker
    from docker.errors import APIError, ImageNotFound, DockerException
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
except Exception as e:
    DOCKER_AVAILABLE = False


def docker_run(
    image: str,
    command: str,
    volumes: Optional[List[str]] = None,
    environment: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    Run a command inside an isolated Docker container.

    Args:
        image: Docker image to use (e.g., "python:3.11-slim")
        command: Command to execute in the container
        volumes: Optional list of volume mappings in "host:container" format
        environment: Optional dictionary of environment variables
        timeout: Timeout in seconds (default: 60)
        task_id: Task identifier for tracking
        on_log: Optional callback for logging

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "exit_code": int
        }
    """
    if not DOCKER_AVAILABLE:
        return tool_result(
            error="Docker Python SDK not installed. Please install it with: pip install docker"
        )

    start_time = time.time()

    if not image or not image.strip():
        return tool_result(error="Docker image name cannot be empty")

    if not command or not command.strip():
        return tool_result(error="Command cannot be empty")

    if is_dangerous_command(command):
        return tool_result(error="Command is identified as potentially dangerous and cannot be executed")

    client = None
    container = None
    stdout = ""
    stderr = ""
    exit_code = -1

    try:
        client = docker.from_env()
        client.ping()

        if on_log:
            on_log(f"🔍 Pulling Docker image: {image}")

        try:
            client.images.get(image)
        except ImageNotFound:
            if on_log:
                on_log(f"📥 Image not found locally, pulling: {image}")
            client.images.pull(image)

        if on_log:
            on_log(f"🚀 Starting container from image: {image}")

        container = client.containers.run(
            image=image,
            command=command,
            volumes=volumes,
            environment=environment,
            detach=True,
            auto_remove=False,
            privileged=False,
            network_mode="bridge"
        )

        container_start_time = time.time()
        timed_out = False

        while True:
            container.reload()
            if container.status != "running":
                break
            elapsed = time.time() - container_start_time
            if elapsed > timeout:
                timed_out = True
                break
            time.sleep(0.5)

        if timed_out:
            if on_log:
                on_log(f"⏱️  Container execution timed out after {timeout} seconds, stopping...")
            container.stop(timeout=10)

        logs = container.logs(stdout=True, stderr=True, stream=False)
        stdout_bytes = b""
        stderr_bytes = b""

        if hasattr(logs, "stdout"):
            stdout_bytes = logs.stdout or b""
            stderr_bytes = logs.stderr or b""
        else:
            stdout_bytes = logs

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        stdout = redact_sensitive_text(stdout)
        stderr = redact_sensitive_text(stderr)

        if on_log:
            if stdout:
                on_log(stdout)
            if stderr:
                on_log(stderr)

        exit_code = container.wait(timeout=10)["StatusCode"]
        success = exit_code == 0

        return {
            "success": success,
            "stdout": stdout[:10240],
            "stderr": stderr[:10240],
            "exit_code": exit_code
        }

    except DockerException as e:
        if on_log:
            on_log(f"❌ Docker error: {str(e)}")
        return tool_result(error=f"Docker error: {str(e)}")
    except APIError as e:
        if on_log:
            on_log(f"❌ Docker API error: {str(e)}")
        return tool_result(error=f"Docker API error: {str(e)}")
    except Exception as e:
        if on_log:
            on_log(f"❌ Unexpected error: {str(e)}")
        return tool_result(error=f"Unexpected error: {str(e)}")
    finally:
        if container:
            try:
                container.remove(force=True)
            except:
                pass
        if client:
            try:
                client.close()
            except:
                pass


DOCKER_RUN_SCHEMA = {
    "name": "docker_run",
    "description": "Run command inside an isolated Docker container.",
    "parameters": {
        "type": "object",
        "properties": {
            "image": {
                "type": "string",
                "description": "Docker image to use (e.g., \"python:3.11-slim\")"
            },
            "command": {
                "type": "string",
                "description": "Command to execute in the container"
            },
            "volumes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of volume mappings in \"host:container\" format",
                "default": None
            },
            "environment": {
                "type": "object",
                "description": "Optional dictionary of environment variables",
                "default": None
            },
            "timeout": {
                "type": "integer",
                "default": 60,
                "description": "Timeout in seconds (default: 60)"
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier for tracking",
                "default": ""
            }
        },
        "required": ["image", "command"]
    }
}


def register_all() -> None:
    """Register all Docker tools to the registry."""
    if DOCKER_AVAILABLE:
        registry.register(
            name=DOCKER_RUN_SCHEMA["name"],
            handler=docker_run,
            description=DOCKER_RUN_SCHEMA["description"],
            parameters=DOCKER_RUN_SCHEMA["parameters"],
            toolset="environments",
            emoji="🐳"
        )


register_all()
