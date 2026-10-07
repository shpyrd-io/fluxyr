"""Public Flask-compatible application with an explicit engine lifecycle."""

import os
import signal
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from .config import Settings
from .native_tools import NativeTool


class Fluxyr(Flask):
    """A WSGI Flask application. Construction/registration never starts workers.

    app.run() owns the HTTP server and embedded worker. WSGI hosts may call
    app.start() explicitly in one process; HTTP-only processes use initialize().
    """

    def __init__(self, import_name, *, settings=None, adapter_factory=None, **kwargs):
        kwargs.setdefault("static_folder", str(Path(__file__).with_name("static")))
        kwargs.setdefault("static_url_path", "/assets-static")
        super().__init__(import_name, **kwargs)
        self._fluxyr_settings = settings
        self._adapter_factory = adapter_factory
        self._native_tools = {}
        self._initialize_lock = threading.RLock()
        self._initialized = False
        self._closed = False

    def tool(self, function=None, **options):
        """Register a typed function; return it unchanged for ordinary Python use."""

        def register(fn):
            if self._initialized:
                raise RuntimeError(
                    "Register tools before initialize(), start() or the first request"
                )
            tool = NativeTool.from_function(fn, **options)
            if tool.name in self._native_tools:
                raise ValueError(f"Duplicate tool: {tool.name}")
            self._native_tools[tool.name] = tool
            return fn

        return register(function) if function else register

    def initialize(self):
        """Initialize persistence, routes and file skills without starting workers."""
        with self._initialize_lock:
            if self._closed:
                raise RuntimeError("This Fluxyr app is closed; create a new instance")
            if self._initialized:
                return self
            if self._fluxyr_settings is None:
                # Deterministic location: the consumer app's directory, never site-packages.
                load_dotenv(Path(self.root_path) / ".env", override=False)
                self._fluxyr_settings = Settings()
                if not os.getenv("FLUXYR_ROOT"):
                    self._fluxyr_settings.root = Path(self.root_path)
                elif not self._fluxyr_settings.root.is_absolute():
                    self._fluxyr_settings.root = (
                        Path(self.root_path) / self._fluxyr_settings.root
                    )
            from .app import create_app

            try:
                create_app(self._fluxyr_settings, self._adapter_factory, app=self)
            except Exception:
                self.close()
                raise
            self._initialized = True
            return self

    @property
    def engine(self):
        self.initialize()
        return self.extensions["engine"]

    @property
    def db(self):
        """SQLAlchemy database; use db.transaction() for a scoped Session."""
        return self.engine.db

    def start(self):
        self.initialize()
        if not self._adapter_factory:
            self._fluxyr_settings.validate_credentials()
        self.engine.start()
        return self

    def close(self):
        with self._initialize_lock:
            if self._closed:
                return
            if "engine" in self.extensions:
                self.extensions["engine"].stop()
                self.extensions["engine"].db.engine.dispose()
            self._closed = True

    def wsgi_app(self, environ, start_response):
        self.initialize()
        return super().wsgi_app(environ, start_response)

    def test_client(self, *args, **kwargs):
        self.initialize()
        return super().test_client(*args, **kwargs)

    def run(self, host=None, port=None, debug=None, **options):
        """Serve with Waitress. Use the CLI --reload for local Python development."""
        if debug or options:
            raise ValueError(
                "Use fluxyr --app app:app --reload for development; Flask reloader options are not supported here"
            )
        self.start()
        settings = self._fluxyr_settings
        previous = {}
        if threading.current_thread() is threading.main_thread():

            def stop(*_):
                raise SystemExit(0)

            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, stop)
        try:
            from waitress import serve

            serve(
                self,
                host=host or settings.host,
                port=port or settings.port,
                threads=settings.http_threads,
            )
        finally:
            self.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
