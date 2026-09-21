"""Local preview server for the WebAR site with the MIME types AR viewers expect.

usage: python serve.py [port]
"""
import http.server, os, sys, functools

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".usdz": "model/vnd.usdz+zip",
        ".webp": "image/webp",
        ".js": "text/javascript",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(Handler, directory=SITE)
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler) as httpd:
        print(f"serving {SITE} on http://localhost:{PORT}/", flush=True)
        httpd.serve_forever()
