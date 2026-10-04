"""A stand-in for llama-server in tests: serves /health on --port, or exits at once with --crash."""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--crash" in args:
        sys.exit(3)
    port = int(args[args.index("--port") + 1])
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
