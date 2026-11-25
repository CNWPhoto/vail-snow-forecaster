"""Serve the map page locally. Unlike `python -m http.server`, it tells the browser to check for a
newer copy every time, so an updated forecast or page is never hidden behind a cached one.

    python3 web/serve.py [port]
"""
import functools
import http.server
import sys
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    handler = functools.partial(Handler, directory=str(Path(__file__).resolve().parent / "app"))
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
