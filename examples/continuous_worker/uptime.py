"""Paste into a Worker routine; stdlib only. Replace URL before activation."""
import time
import urllib.error
import urllib.request

URL = "https://example.com"
INTERVAL_SECONDS = 60


def run(ctx):
    previous = ctx.checkpoint
    while not ctx.stopping:
        try:
            with urllib.request.urlopen(URL, timeout=10) as response:
                current = {"url": URL, "up": response.status < 400}
        except (OSError, urllib.error.URLError):
            current = {"url": URL, "up": False}
        ctx.ready()
        if current != previous:
            ctx.emit(
                session_key=URL,
                event_id=str(time.time_ns()),
                payload=current,
                checkpoint=current,
            )
            previous = current
        if ctx.wait(INTERVAL_SECONDS):
            break
