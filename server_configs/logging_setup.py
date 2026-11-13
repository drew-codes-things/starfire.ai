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
