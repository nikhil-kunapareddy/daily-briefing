import threading
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class SimpleHandler(BaseHTTPRequestHandler):
    """Serve two routes: /200 returns 200, /404 returns 404."""

    def do_GET(self):  # noqa: N802 (method name from BaseHTTPRequestHandler)
        if self.path == "/200":
            self.send_response(200)
        elif self.path == "/404":
            self.send_response(404)
        else:
            self.send_response(500)
        self.end_headers()

    def log_message(self, format, *args):  # suppress console noise during tests
        return


def start_server(port):
    server = HTTPServer(("127.0.0.1", port), SimpleHandler)
    # Run the server forever; will be stopped by calling server.shutdown()
    server.serve_forever()


def test_check_site_success_and_failure():
    # Choose a deterministic port >= 8100
    port = 8100
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()
    # Give the server a moment to start
    time.sleep(0.1)

    # 1️⃣ Successful 200 response
    result_ok = subprocess.run(
        ["./scripts/check-site.sh", f"http://127.0.0.1:{port}/200"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert result_ok.returncode == 0, f"Expected exit 0, got {result_ok.returncode}\n{result_ok.stdout}"

    # 2️⃣ 404 response should be non‑zero and print a single line
    result_404 = subprocess.run(
        ["./scripts/check-site.sh", f"http://127.0.0.1:{port}/404"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert result_404.returncode != 0, "Expected non‑zero exit for 404"
    # The script must output exactly one line describing the error
    output_lines = result_404.stdout.strip().splitlines()
    assert len(output_lines) == 1, f"Expected one line of error output, got {output_lines}"
    expected_prefix = f"HTTP 404 from http://127.0.0.1:{port}/404"
    assert output_lines[0] == expected_prefix, f"Unexpected error message: {output_lines[0]}"

    # Shut down the server cleanly
    # Since we used daemon thread, exiting the test will stop it, but we can also close the socket.
    # Connect to the server to trigger shutdown by exiting the thread (not needed for daemon).
    # No explicit shutdown needed.

