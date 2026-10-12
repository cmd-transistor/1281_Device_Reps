"""Local HTTP API for prompt-driven ISO structure creation.

Run ``python iso_ai_api.py`` (default http://127.0.0.1:8765). Endpoints:

* ``GET  /health``                         -> engine statistics
* ``POST /structures``          {"prompt"}  -> new design (JSON)
* ``GET  /structures/<id>``                -> current design
* ``POST /structures/<id>/feedback`` {"action": "revise"|"accept"|"reject", "text"}
* ``GET  /structures/<id>/buildsheet.csv``  -> one-row buildsheet CSV
* ``GET  /structures/<id>/layout.png``      -> unit-cell layout preview

If ``ISO_AI_API_TOKEN`` is set, every request needs ``Authorization: Bearer <token>``.
Binding to a non-loopback host requires the token.

Example::

    curl -X POST http://127.0.0.1:8765/structures -H "Content-Type: application/json" \
         -d "{\\"prompt\\": \\"CTG 132 NP HVT type A SDR reference\\"}"
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from iso_structure_ai import StructureDesigner, UnknownDesign

MAX_BODY_BYTES = 64 * 1024
ROUTE_RE = re.compile(r"^/structures/(?P<id>[0-9a-f]{12})(?P<sub>/feedback|/buildsheet\.csv|/layout\.png)?$")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def make_handler(designer: StructureDesigner, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ISOStructureAI/1.0"

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            self._send(status, json.dumps(payload, indent=1).encode("utf-8"), "application/json")

        def _authorize(self) -> None:
            if not token:
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
                raise ApiError(HTTPStatus.UNAUTHORIZED, "missing or invalid bearer token")

        def _body(self) -> dict:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from None
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body must be 1..{MAX_BODY_BYTES} bytes")
            try:
                data = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "body is not valid JSON") from None
            if not isinstance(data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
            return data

        def _dispatch(self, method: str) -> None:
            try:
                self._authorize()
                path = urlparse(self.path).path.rstrip("/") or "/"
                if method == "GET" and path == "/health":
                    return self._json(HTTPStatus.OK, {"status": "ok", **designer.stats()})
                if method == "POST" and path == "/structures":
                    prompt = self._body().get("prompt")
                    if not isinstance(prompt, str):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "'prompt' must be a string")
                    return self._json(HTTPStatus.CREATED, designer.create(prompt).to_dict())
                m = ROUTE_RE.match(path)
                if not m:
                    raise ApiError(HTTPStatus.NOT_FOUND, "unknown endpoint")
                design_id, sub = m["id"], m["sub"]
                if method == "GET" and not sub:
                    return self._json(HTTPStatus.OK, designer.get(design_id).to_dict())
                if method == "GET" and sub == "/buildsheet.csv":
                    csv_text = designer.buildsheet_csv([designer.get(design_id).row])
                    self._send(HTTPStatus.OK, csv_text.encode("utf-8"), "text/csv; charset=utf-8")
                    return None
                if method == "GET" and sub == "/layout.png":
                    png = designer.layout_png(design_id)
                    if png is None:
                        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "layout preview unavailable for this design")
                    return self._send(HTTPStatus.OK, png.read_bytes(), "image/png")
                if method == "POST" and sub == "/feedback":
                    data = self._body()
                    action, text = data.get("action"), data.get("text", "")
                    if not isinstance(text, str):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "'text' must be a string")
                    handlers = {"revise": designer.revise, "accept": designer.accept, "reject": designer.reject}
                    if action not in handlers:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "'action' must be revise, accept or reject")
                    return self._json(HTTPStatus.OK, handlers[action](design_id, text).to_dict())
                raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except UnknownDesign:
                self._json(HTTPStatus.NOT_FOUND, {"error": "unknown design id"})
            except ValueError as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except Exception:
                self.log_error("unhandled error on %s %s", method, self.path)
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return Handler


def create_server(designer: StructureDesigner, host: str = "127.0.0.1", port: int = 8765,
                  token: str | None = None) -> ThreadingHTTPServer:
    token = os.environ.get("ISO_AI_API_TOKEN", "") if token is None else token
    if host not in LOOPBACK and not token:
        raise ValueError("Set ISO_AI_API_TOKEN before binding the API to a non-loopback host")
    server = ThreadingHTTPServer((host, port), make_handler(designer, token))
    server.daemon_threads = True
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="Local HTTP API for prompt-driven ISO structure creation")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-llm", action="store_true", help="ignore ISO_AI_ENDPOINT; rule-based interpretation only")
    args = ap.parse_args()
    designer = StructureDesigner(use_llm=not args.no_llm)
    server = create_server(designer, args.host, args.port)
    print(f"ISO structure API on http://{args.host}:{server.server_address[1]}  {designer.stats()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
