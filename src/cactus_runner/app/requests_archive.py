import logging
from pathlib import Path
import re
import shutil

from cactus_runner.app import proxy
from cactus_runner.models import RequestEntry

logger = logging.getLogger(__name__)

# nosec B108: Safe in short lived K8s pods (one per test, destroyed after run)
# Alternatives tried: hardcoded paths (permission errors), tempfile (failed on write to zip, issue on finalise?)
REQUEST_DATA_DIR = Path("/tmp/cactus_request_data")  # nosec B108


def ensure_request_data_dir() -> Path:
    """Ensure the request data directory exists and return it."""
    REQUEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return REQUEST_DATA_DIR


def sanitise_url_to_filename(url: str) -> str:
    """Convert URL path to safe filename."""
    path = url.split("?")[0].lstrip("/")
    sanitised = re.sub(r"[^a-zA-Z0-9._-]", "_", path)
    return sanitised if sanitised else "root"


def write_request_response_files(
    request_id: int,
    proxy_result: proxy.ProxyResult,
    entry: RequestEntry,
) -> None:
    """
    Write HTTP-style request/response files directly to storage.
    Fails silently with logging if write fails.

    Args:
        request_id: Sequential integer ID for the request
        proxy_result: The proxy result containing request/response data
        entry: RequestEntry with metadata (method, path, status, step_name)
    """
    try:
        storage_dir = ensure_request_data_dir()

        # Decode request body
        request_body = None
        if proxy_result.request_body:
            encoding = proxy_result.request_encoding or "utf-8"
            try:
                request_body = proxy_result.request_body.decode(encoding=encoding, errors="replace")
            except Exception as exc:
                logger.error(f"Error decoding request body with encoding '{encoding}'", exc_info=exc)
                request_body = "[binary data]"

        # Decode response body
        response_body = None
        if hasattr(proxy_result.response, "text") and proxy_result.response.text:
            response_body = proxy_result.response.text
        elif hasattr(proxy_result.response, "body") and proxy_result.response.body:
            try:
                response_body = proxy_result.response.body.decode(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.error("Error decoding response body", exc_info=exc)
                response_body = "[binary data]"

        # Create filename
        sanitised_path = sanitise_url_to_filename(entry.path)
        base_name = f"{request_id:03d}-{entry.step_name}-{sanitised_path}"

        # Write .request file
        request_file = storage_dir / f"{base_name}.request"
        with open(request_file, "w", encoding="utf-8", errors="replace") as fp:
            lines = [f"{entry.method.value} {entry.path} HTTP/1.1"]
            for header, value in proxy_result.request_headers.items():
                lines.append(f"{header}: {value}")
            if request_body:
                lines.append("")
                lines.append(request_body)
            fp.write("\n".join(lines))

        # Write .response file
        response_file = storage_dir / f"{base_name}.response"
        with open(response_file, "w", encoding="utf-8", errors="replace") as fp:
            lines = [f"HTTP/1.1 {entry.status.value} {entry.status.phrase}"]
            for header, value in proxy_result.response.headers.items():
                lines.append(f"{header}: {value}")
            if response_body:
                lines.append("")
                lines.append(response_body)
            fp.write("\n".join(lines))

        logger.debug(f"Successfully wrote request/response files for request_id={request_id}")

    except Exception as exc:
        logger.error(f"Failed to write request/response files for request_id={request_id}", exc_info=exc)


def read_request_response_files(request_id: int) -> tuple[str | None, str | None]:
    """
    Read raw request/response files from disk.

    Args:
        request_id: Sequential integer ID for the request

    Returns:
        Tuple of (request_content, response_content). Either may be None if read fails.
    """
    try:
        storage_dir = ensure_request_data_dir()

        # Find files matching the request_id pattern
        request_files = list(storage_dir.glob(f"{request_id:03d}-*.request"))
        response_files = list(storage_dir.glob(f"{request_id:03d}-*.response"))

        request_content = None
        response_content = None

        if request_files:
            try:
                with open(request_files[0], "r", encoding="utf-8") as f:
                    request_content = f.read()
            except Exception as exc:
                logger.error(f"Failed to read request file for request_id={request_id}", exc_info=exc)
        else:
            logger.warning(f"Request file not found for request_id={request_id}")

        if response_files:
            try:
                with open(response_files[0], "r", encoding="utf-8") as f:
                    response_content = f.read()
            except Exception as exc:
                logger.error(f"Failed to read response file for request_id={request_id}", exc_info=exc)
        else:
            logger.warning(f"Response file not found for request_id={request_id}")

        return request_content, response_content

    except Exception as exc:
        logger.error(f"Failed to read request/response files for request_id={request_id}", exc_info=exc)
        return None, None


def copy_request_response_files_to_archive(archive_dir: Path) -> None:
    """
    Copy all request/response files from storage to archive directory.
    Fails silently with logging if copy fails.

    Args:
        archive_dir: Destination directory for the archive
    """
    try:
        storage_dir = ensure_request_data_dir()

        if not storage_dir.exists():
            logger.warning(f"Request data directory does not exist: {storage_dir}")
            return

        requests_dir = archive_dir / "requests"
        requests_dir.mkdir(exist_ok=True)

        # Copy all .request files
        request_files = list(storage_dir.glob("*.request"))
        for file_path in request_files:
            try:
                shutil.copy2(file_path, requests_dir / file_path.name)
            except Exception as exc:
                logger.error(f"Failed to copy request file {file_path.name}", exc_info=exc)

        # Copy all .response files
        response_files = list(storage_dir.glob("*.response"))
        for file_path in response_files:
            try:
                shutil.copy2(file_path, requests_dir / file_path.name)
            except Exception as exc:
                logger.error(f"Failed to copy response file {file_path.name}", exc_info=exc)

        logger.info(f"Copied {len(request_files)} request files and {len(response_files)} response files to archive")

    except Exception as exc:
        logger.error("Failed to copy request/response files to archive", exc_info=exc)


def get_all_request_ids() -> list[int]:
    """
    Get all request IDs that have stored data.

    Returns:
        Sorted list of request IDs
    """
    try:
        storage_dir = ensure_request_data_dir()

        if not storage_dir.exists():
            return []

        # Extract request IDs from filenames (format: 001-step-path.request)
        request_ids = set()
        for file_path in storage_dir.glob("*.request"):
            try:
                # Extract the numeric prefix
                id_str = file_path.name.split("-")[0]
                request_ids.add(int(id_str))
            except (ValueError, IndexError) as exc:
                logger.warning(f"Failed to parse request ID from filename: {file_path.name}", exc_info=exc)

        return sorted(request_ids)

    except Exception as exc:
        logger.error("Failed to get request IDs", exc_info=exc)
        return []
