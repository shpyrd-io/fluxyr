"""Small structured logging seam shared with the extracted harness."""

import contextvars
import functools
import json
import logging
import time
from contextlib import contextmanager

_context = contextvars.ContextVar("log_context", default={})


class Logger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)

    def _write(self, level, event, *args, **fields):
        exc = fields.pop("exc_info", None)
        self.logger.log(
            level,
            "%s %s",
            event % args if args else event,
            json.dumps({**_context.get(), **fields}, default=str),
            exc_info=exc,
        )

    def debug(self, event, *args, **fields):
        self._write(logging.DEBUG, event, *args, **fields)

    def info(self, event, *args, **fields):
        self._write(logging.INFO, event, *args, **fields)

    def warning(self, event, *args, **fields):
        self._write(logging.WARNING, event, *args, **fields)

    def error(self, event, *args, **fields):
        self._write(logging.ERROR, event, *args, **fields)

    def exception(self, event, *args, **fields):
        self._write(logging.ERROR, event, *args, exc_info=True, **fields)


def get_logger(name):
    return Logger(name)


@contextmanager
def scope(**values):
    token = _context.set({**_context.get(), **values})
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def feature_tag(name):
    with scope(feature=name):
        yield


def carry_context(fn):
    context = contextvars.copy_context()

    @functools.wraps(fn)
    def call(*args, **kwargs):
        return context.copy().run(fn, *args, **kwargs)

    return call


class timed:
    def __init__(self):
        self.start = time.monotonic()

    @property
    def ms(self):
        return round((time.monotonic() - self.start) * 1000, 2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def arg_keys(args):
    return {
        "arg_keys": list(args) if isinstance(args, dict) else [],
        "arg_count": len(args or {}),
    }


def result_bytes(result):
    return len(json.dumps(result, default=str).encode())
