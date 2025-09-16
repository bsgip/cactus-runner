import datetime as dt
import json
import logging
from typing import override

# Changing these log file paths requires updating cactus-deploy logconf.json files - these are baked into built images.
# In theory, we could try and make this more dynamic but for practical reasons, moving these around shouldn't be a
# common use case
LOG_FILE_ENVOY_SERVER = "/shared/envoy.server.jsonl"
LOG_FILE_ENVOY_ADMIN = "/shared/envoy.admin.jsonl"
LOG_FILE_ENVOY_NOTIFICATION = "/shared/envoy.notification.jsonl"
LOG_FILE_CACTUS_RUNNER = "/shared/cactus_runner.jsonl"
LOG_FILE_UVICORN = "/shared/uvicorn.jsonl"

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JSONLFormatter(logging.Formatter):
    """A logging formatter that produces JSONL messages.

    JSONL (https://jsonlines.org/) is a line based JSON format,
    which is suitable for structured (machine-parsable) log files.
    """

    def __init__(self, *, fmt_keys: dict[str, str] | None = None):
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord):
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(),
        }

        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val if (msg_val := always_fields.pop(val, None)) is not None else getattr(record, val)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        # Append extra attributes to the dictionary-based log messages e.g. jsonl
        # Extra attributes set in the logging call e.g.,
        #   logger.INFO(message="My logging message", extra={"x": 100, some_flag=True})
        # The values in the 'extra' dictionary are appended to the log message.
        for key, value in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = value

        return message


class NonErrorFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        """Logging filter which excludes warnings and errors"""
        return record.levelno <= logging.INFO


def read_log_file(log_file_path: str) -> str:
    """Reads all text at the specified file path. Returns the resulting data or an error string.

    Significantly large log files will be truncated"""
    try:
        with open(log_file_path, "r") as file:
            return file.read(1024 * 1024 * 4)  # Limit to 4MB so we don't over fetch - should be more than enough
    except Exception as exc:
        return f"Error extracting {log_file_path}: {exc}"
