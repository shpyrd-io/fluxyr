"""Optional local Chrome controller. Private values travel on inherited pipes only."""

import atexit
import base64
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .models import VaultItem
from .otp import generate


class BrowserError(ValueError):
    pass


class BrowserRejected(BrowserError):
    """A completed protocol exchange rejected a stale/invalid browser operation."""


def artifact_id():
    """Keep all UUID bits in a 22-character, filename-safe base62 name."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    value = uuid.uuid4().int
    chars = []
    while value:
        value, remainder = divmod(value, 62)
        chars.append(alphabet[remainder])
    return "".join(reversed(chars)).rjust(22, "0")


class Driver:
    def __init__(self, runtime, profile, downloads):
        node = os.getenv("FLUXYR_BROWSER_NODE") or shutil.which("node")
        if not node or not (runtime / "node_modules/public-browser").exists():
            raise BrowserError(
                "Browser runtime missing. Install Node 20+ and Chrome, then run python -m fluxyr.browser install."
            )
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            in (
                "PATH",
                "HOME",
                "TMPDIR",
                "TEMP",
                "TMP",
                "LANG",
                "LC_ALL",
                "CHROME_PATH",
                "SYSTEMROOT",
                "DISPLAY",
                "XDG_RUNTIME_DIR",
            )
        }
        env.update(
            FLUXYR_BROWSER_PROFILE=str(profile),
            FLUXYR_BROWSER_DOWNLOADS=str(downloads),
            PUBLIC_BROWSER_CORTEX_DIR=str(profile.parent / "cortex"),
        )
        self.process = subprocess.Popen(
            [node, str(runtime / "driver.mjs")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.messages = queue.Queue(maxsize=16)
        self.lock = threading.RLock()
        self.last_used = time.monotonic()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            if self.messages.get(timeout=10).get("type") != "ready":
                raise BrowserError("Browser driver could not start")
        except (queue.Empty, BrowserError):
            self.close()
            raise BrowserError("Browser driver startup timed out") from None

    def _read(self):
        try:
            while line := self.process.stdout.readline(16 * 1024 * 1024):
                if not line.endswith("\n"):
                    break
                self.messages.put(json.loads(line), timeout=1)
        except (ValueError, OSError, queue.Full):
            pass
        finally:
            try:
                self.messages.put(
                    {"error": "Browser disconnected; inspect the page before retrying"},
                    timeout=1,
                )
            except queue.Full:
                pass

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, op, *, secret=None, persist=None, stop=lambda: False, **args):
        with self.lock:
            self.last_used = time.monotonic()
            request_id = uuid.uuid4().hex
            consumed = False
            persisted = False
            try:
                self._send({"id": request_id, "op": op, **args})
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if stop():
                        raise BrowserError(
                            "Browser operation cancelled; inspect before retrying"
                        )
                    try:
                        message = self.messages.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if message.get("id") != request_id:
                        raise BrowserError("Unexpected browser response")
                    if message.get("error"):
                        raise BrowserRejected(message["error"])
                    if message.get("type") == "passkey_store":
                        if persist is None or persisted:
                            raise BrowserError("Unexpected passkey persistence request")
                        persisted = True
                        try:
                            persist(message["credential"])
                            saved = True
                        except Exception:  # noqa: BLE001 - private SQL/credential errors must not enter protocol output
                            # No SQL, credential or CDP details leave this private channel.
                            saved = False
                        self._send(
                            {"type": "secret_value", "id": request_id, "value": saved}
                        )
                    elif message.get("type") == "secret_request":
                        if secret is None or consumed:
                            raise BrowserError("Private input is unavailable")
                        consumed = True
                        self._send(
                            {
                                "type": "secret_value",
                                "id": request_id,
                                "value": secret(),
                            }
                        )
                    else:
                        return message["result"]
                raise BrowserError(
                    "Browser operation timed out; inspect before retrying"
                )
            except BrowserRejected:
                raise
            except (BrokenPipeError, OSError, ValueError):
                # A interrupted exchange cannot be safely reused or replayed.
                self.close()
                raise BrowserError(
                    "Browser operation failed or destination changed. Reopen and inspect the page before retrying; private values were not recorded."
                ) from None
            finally:
                self.last_used = time.monotonic()

    def close(self):
        with self.lock:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe:
                    pipe.close()


class Browsers:
    def __init__(self, engine):
        self.engine = engine
        self.enabled = os.getenv("FLUXYR_BROWSER_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
        )
        bundled = Path(__file__).with_name("browser_runtime")
        default = (
            bundled
            if (bundled / "node_modules").exists()
            else engine.settings.runtime / "browser-engine"
        )
        self.runtime = Path(os.getenv("FLUXYR_BROWSER_RUNTIME") or default).resolve()
        self.limit = max(1, int(os.getenv("FLUXYR_BROWSER_MAX_SESSIONS", "2")))
        self.ttl = max(60, int(os.getenv("FLUXYR_BROWSER_IDLE_SECONDS", "600")))
        self.sessions = {}
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.reaper = None
        from .passkeys import Passkeys

        self.passkeys = Passkeys(self)

    def _get(self, session_id, create=True):
        if not self.enabled:
            raise BrowserError("Enable FLUXYR_BROWSER_ENABLED to use the browser")
        with self.lock:
            current = self.sessions.get(session_id)
            if current and current[0].process.poll() is not None:
                self._close(session_id)
                current = None
            if not current:
                if not create:
                    raise BrowserError(
                        "Browser request expired. Inspect the page and request input again."
                    )
                if len(self.sessions) >= self.limit:
                    raise BrowserError(
                        "Browser session limit reached. Close an unused browser first."
                    )
                if self.shutdown.is_set():
                    raise BrowserError("Browser service is stopping")
                directory = Path(
                    tempfile.mkdtemp(
                        prefix="fluxyr-browser-", dir=self.engine.settings.runtime
                    )
                )
                downloads = self.engine.files.path("tmp/browser/" + artifact_id())
                downloads.mkdir(parents=True, exist_ok=True)
                try:
                    driver = Driver(self.runtime, directory / "profile", downloads)
                except Exception:
                    shutil.rmtree(directory)
                    raise
                current = (driver, directory, uuid.uuid4().hex)
                self.sessions[session_id] = current
                if not self.reaper:
                    self.reaper = threading.Thread(target=self._reap, daemon=True)
                    self.reaper.start()
                    atexit.register(self.close)
            return current

    def _reap(self):
        while not self.shutdown.wait(15):
            with self.lock:
                for sid, (driver, _, _) in list(self.sessions.items()):
                    if driver.lock.acquire(blocking=False):
                        try:
                            if time.monotonic() - driver.last_used > self.ttl:
                                self._close(sid)
                        finally:
                            driver.lock.release()

    def _close(self, sid):
        current = self.sessions.pop(sid, None)
        if current:
            current[0].close()
            shutil.rmtree(current[1], ignore_errors=True)

    def close(self):
        self.shutdown.set()
        with self.lock:
            for sid in list(self.sessions):
                self._close(sid)
        if self.reaper and self.reaper is not threading.current_thread():
            self.reaper.join(timeout=2)
        atexit.unregister(self.close)

    def call(self, session_id, tool, arguments=None, stop=lambda: False):
        if tool == "help":
            schemas = json.loads(
                Path(__file__)
                .with_name("browser_runtime")
                .joinpath("tool-schemas.json")
                .read_text()
            )
            # Diagnostic collectors/plans are omitted: they may capture private network bodies.
            excluded = {
                "console_logs",
                "network_monitor",
                "run_plan",
                "batch_evaluate",
                "set_page_data",
            }
            schemas = {k: v for k, v in schemas.items() if k not in excluded}
            selected = (arguments or {}).get("tool")
            if selected:
                if selected not in schemas:
                    raise BrowserError("Unknown browser tool")
                return schemas[selected]
            return {
                "tools": {k: v["description"] for k, v in schemas.items()},
                "close": "Close this session's browser",
            }
        if tool == "close":
            with self.lock:
                self._close(session_id)
            return {"closed": True}
        driver, _, _ = self._get(session_id)
        result = driver.call("call", tool=tool, args=arguments or {}, stop=stop)
        # Images are explicit artifacts, not megabytes in every tool/event payload.
        content = []
        for block in result.get("content", []):
            if block.get("type") == "image":
                suffix = {"image/png": ".png", "image/webp": ".webp"}.get(
                    block.get("mimeType"), ".jpg"
                )
                path = self.engine.files.path("tmp/browser/" + artifact_id() + suffix)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as image:
                    image.write(base64.b64decode(block["data"], validate=True))
                content.append(
                    {
                        "type": "image_file",
                        "path": str(path.relative_to(self.engine.settings.data)),
                        "url": "/preview/"
                        + str(path.relative_to(self.engine.settings.data)),
                    }
                )
            elif block.get("type") == "text":
                content.append({"type": "text", "text": block.get("text", "")[:24000]})
        return {"content": content, "isError": bool(result.get("isError"))}

    def prepare(self, session_id, origin, ref=None, selector=None, stop=lambda: False):
        if bool(ref) == bool(selector):
            raise BrowserError("Supply exactly one field ref or CSS selector")
        driver, _, generation = self._get(session_id)
        target = driver.call(
            "prepare",
            target={"ref": ref, "selector": selector},
            origin=origin,
            stop=stop,
        )
        return {**target, "generation": generation, "session_id": session_id}

    def fill(
        self,
        target,
        *,
        value=None,
        vault_item_id=None,
        field=None,
        submit=False,
        stop=lambda: False,
    ):
        driver, _, generation = self._get(target["session_id"], create=False)
        if generation != target["generation"]:
            raise BrowserError("Browser was restarted; request private input again")

        def resolve():
            if value is not None:
                return value
            with self.engine.db.transaction() as db:
                item = db.get(VaultItem, vault_item_id)
                if not item:
                    raise BrowserError("Vault item not found")
                kind = item.type
                content = self.engine.vault.decrypt(item.content)
            if kind == "totp":
                if field not in (None, "code"):
                    raise BrowserError("A TOTP item only provides its current code")
                code, expires = generate(content)
                # Wait at most five seconds, only after Chrome has confirmed the field.
                remaining = expires - time.time()
                if remaining < 5:
                    deadline = time.monotonic() + remaining + 0.05
                    while time.monotonic() < deadline:
                        if stop():
                            raise BrowserError("Cancelled")
                        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    code, _ = generate(content)
                return code
            fields = {
                "text": ("value",),
                "key_password": ("password", "key"),
                "access_token": ("access_token",),
            }
            supported = fields.get(kind, ())
            selected = field or (supported[0] if supported else None)
            if selected not in supported:
                raise BrowserError("Unsupported private Vault field")
            return content[selected]

        return driver.call(
            "fill",
            target_id=target["target_id"],
            secret=resolve,
            submit=submit,
            stop=stop,
        )


def install():
    """Explicit installation only; serving Fluxyr never downloads executable code."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Install the optional Fluxyr browser controller (requires Node 20+ and Chrome)"
    )
    parser.add_argument("command", choices=["install"])
    parser.add_argument(
        "--directory",
        default=os.getenv("FLUXYR_BROWSER_RUNTIME", ".runtime/browser-engine"),
    )
    args = parser.parse_args()
    destination = Path(args.directory).resolve()
    source = Path(__file__).with_name("browser_runtime")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("driver.mjs", "passkeys.mjs", "package.json", "pnpm-lock.yaml"):
        if source / name != destination / name:
            shutil.copy2(source / name, destination / name)
    if shutil.which("pnpm"):
        command = ["pnpm", "install", "--prod", "--frozen-lockfile", "--ignore-scripts"]
    elif shutil.which("npm"):
        command = ["npm", "install", "--omit=dev", "--ignore-scripts"]
    else:
        parser.error("Install Node 20+ with npm or pnpm first")
    subprocess.run(command, cwd=destination, check=True)
    print(
        f"Browser runtime installed in {destination}. Set FLUXYR_BROWSER_ENABLED=true and, for a custom path, FLUXYR_BROWSER_RUNTIME."
    )


if __name__ == "__main__":
    install()
