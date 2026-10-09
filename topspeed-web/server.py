#!/usr/bin/env python3
"""Local web server for Top Speed web.

    python3 server.py                 # http://localhost:8000
    python3 server.py --port 9000
    python3 server.py --https         # self-signed HTTPS (needed for motion steering on iOS)

The server listens on all network interfaces so a phone on the same Wi-Fi can open the game
using the LAN address printed at startup.
"""
import argparse
import functools
import http.server
import os
import socket
import ssl
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }

    def end_headers(self):
        # Code changes show up on reload; sounds may be cached.
        if self.path.split("?")[0].endswith((".html", ".js", ".css", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "--quiet" not in sys.argv:
            super().log_message(fmt, *args)


def lan_addresses():
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))  # no packets are sent; this picks the outgoing interface
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def ensure_cert(directory):
    cert = os.path.join(directory, "cert.pem")
    key = os.path.join(directory, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        print("Creating a self-signed certificate with openssl ...")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
            "-keyout", key, "-out", cert, "-subj", "/CN=topspeed.local"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all interfaces)")
    parser.add_argument("--https", action="store_true", help="serve over HTTPS with a self-signed certificate")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=ROOT)
    httpd = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    scheme = "http"
    if args.https:
        cert, key = ensure_cert(os.path.join(ROOT, ".certs") if os.access(ROOT, os.W_OK) else ROOT)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    print(f"Top Speed web is running.")
    print(f"  On this computer:  {scheme}://localhost:{args.port}/")
    for a in lan_addresses():
        print(f"  On your phone:     {scheme}://{a}:{args.port}/   (same Wi-Fi network)")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, ".certs"), exist_ok=True) if "--https" in sys.argv else None
    main()
