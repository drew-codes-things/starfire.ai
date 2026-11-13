import logging
import os
import sys

APP_LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
APP_MODULES_DIR = os.path.dirname(os.path.abspath(__file__))
ENTRY_POINT_LOGGERS = ("__main__", "server")


def app_logger_names() -> list[str]:
    modules = sorted(name[:-len(".py")] for name in os.listdir(APP_MODULES_DIR) if name.endswith(".py"))
    return [*ENTRY_POINT_LOGGERS, *modules]


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
    for name in app_logger_names():
        logging.getLogger(name).setLevel(APP_LOG_LEVEL)


ACCESS_LOGGER = "uvicorn.access"
ACCESS_PATH_ARG = 2


class AccessLogQueryFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.paths: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) <= ACCESS_PATH_ARG or not isinstance(args[ACCESS_PATH_ARG], str):
            return True
        path, has_query, _query = args[ACCESS_PATH_ARG].partition("?")
        if has_query and path in self.paths:
            record.args = (*args[:ACCESS_PATH_ARG], path, *args[ACCESS_PATH_ARG + 1:])
        return True


def hide_access_log_query(path: str) -> None:
    access = logging.getLogger(ACCESS_LOGGER)
    installed = next((found for found in access.filters if isinstance(found, AccessLogQueryFilter)), None)
    if installed is None:
        installed = AccessLogQueryFilter()
        access.addFilter(installed)
    installed.paths.add(path)
