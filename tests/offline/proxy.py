"""
tests/offline/proxy.py — a recording, switchable HTTP/HTTPS proxy for the
offline-assurance suite.

Why this exists at all
----------------------

Every offline test Snowdesk has ever written asserts against
``page.context.set_offline(True)``, and that call is a lie in the one place
it matters. It disables the network for the *page*; a service worker's own
``fetch`` is unaffected and still reaches the server (SNOW-516's finding,
recorded again here because it keeps being rediscovered). ``page.route`` has
the same hole from the other direction — it never sees a service worker's
script fetches at all. So the two tools every browser test reaches for are
both blind to the exact traffic ``sw.js`` generates, which is the traffic the
offline story is made of.

A proxy has no such blind spot. Chromium routes *everything* through it —
the page, the worker, the worker's own script, the tile requests MapLibre
issues from a web worker — because the routing decision is made below all of
them. That makes this module two things at once:

1. **The observer.** ``NetworkRecorder.requests`` is the ground truth for
   "did anything leave this device", at request granularity for plain HTTP
   (the live server) and connection granularity for TLS (the tile origins,
   tunnelled via CONNECT and deliberately not intercepted — see below).

2. **The weather.** ``mode`` switches the real-world condition being
   simulated, and the three are not interchangeable — ``bounded-offline-read-paths.md``
   exists precisely because the project had only ever tested the first:

   ``pass``       forward everything; the network is healthy.
   ``reject``     refuse immediately — the radio is off, ``fetch`` REJECTS,
                  and every ``catch`` branch in the app runs.
   ``blackhole``  accept the connection and never answer — the captive
                  portal / Underground case, where ``fetch`` neither
                  resolves nor rejects and ``sw.js``'s ``_boundedFetch``
                  budgets plus ``OFFLINE_LATCH_THRESHOLD`` are the only
                  thing standing between the user and a blank page.

Why CONNECT is tunnelled rather than intercepted
------------------------------------------------

Reading tile URLs out of TLS would mean minting a CA, trusting it in the
browser profile, and terminating every connection — a large amount of
machinery whose only product is finer-grained logging. The assertions this
suite actually makes do not need it: "zero connections were opened to
``tiles.example``" is exactly as strong a statement as "zero requests", and
the request-level detail we *do* need is same-origin, which is plain HTTP
against the live server and fully visible. If a future test genuinely needs
per-tile URLs over TLS, that is the moment to add MITM, not before.

Threading model
---------------

One accept loop in a daemon thread, one thread per connection. Connections
are short and the counts are small (a fuzzed region download is a few
hundred requests), so a thread per connection is cheaper to reason about
than a selector loop and fast enough not to distort the timings the
``blackhole`` mode measures.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from http.client import HTTPConnection
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# How long a worker thread waits on a client socket before giving up. Only
# reached when the client opens a connection and sends nothing; the browser
# does that routinely for pre-connect sockets, so this must be short enough
# not to pin threads for a whole test run.
_CLIENT_READ_TIMEOUT_S = 30.0

# Upstream connect/read timeout in ``pass`` mode. Generous: a real tile
# origin on a cold cache is the slowest thing in the suite, and a timeout
# here surfaces as a confusing test failure rather than the network fault
# it actually is.
_UPSTREAM_TIMEOUT_S = 30.0

# Size of the bidirectional relay buffer for tunnelled CONNECT traffic.
_RELAY_CHUNK = 65536


@dataclass(frozen=True)
class Exchange:
    """One thing the browser tried to send through the proxy.

    Args:
        method: HTTP method, or ``"CONNECT"`` for a TLS tunnel request.
        host: Target ``host:port``.
        url: Full URL for plain HTTP; for ``CONNECT`` the authority only,
            since the request line inside the tunnel is encrypted.
        at: ``time.monotonic()`` when the proxy read the request line.
        outcome: What the proxy did — ``"forwarded"``, ``"rejected"`` or
            ``"blackholed"``.

    """

    method: str
    host: str
    url: str
    at: float
    outcome: str

    def to_string(self) -> str:
        """Return a one-line human-readable form for failure messages."""
        return f"{self.outcome:<10} {self.method:<7} {self.url}"


@dataclass
class NetworkRecorder:
    """Mutable, thread-safe state shared between the proxy and the tests.

    The tests drive ``mode`` and read ``requests``; the proxy threads do the
    reverse. Both go through ``_lock``.
    """

    mode: str = "pass"
    requests: list[Exchange] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, exchange: Exchange) -> None:
        """Append one exchange to the log."""
        with self._lock:
            self.requests.append(exchange)

    def current_mode(self) -> str:
        """Return the mode to apply to a request arriving now."""
        with self._lock:
            return self.mode

    def set_mode(self, mode: str) -> None:
        """Switch the simulated network condition.

        Args:
            mode: One of ``"pass"``, ``"reject"``, ``"blackhole"``.

        Raises:
            ValueError: If ``mode`` is not one of the three.

        """
        if mode not in {"pass", "reject", "blackhole"}:
            raise ValueError(f"unknown proxy mode {mode!r}")
        with self._lock:
            self.mode = mode

    def mark(self) -> int:
        """Return the current length of the log, as a watermark.

        Callers pair this with ``since`` to scope an assertion to one phase
        of a test rather than the whole session.
        """
        with self._lock:
            return len(self.requests)

    def since(self, watermark: int) -> list[Exchange]:
        """Return every exchange recorded after ``watermark``."""
        with self._lock:
            return list(self.requests[watermark:])

    def report(self, watermark: int = 0, limit: int = 40) -> str:
        """Render exchanges since ``watermark`` for an assertion message.

        Args:
            watermark: Start index, from ``mark()``.
            limit: Maximum lines to render; the rest are summarised.

        Returns:
            A newline-joined block, or a placeholder when nothing was seen.

        """
        rows = self.since(watermark)
        if not rows:
            return "    (no network activity)"
        lines = [f"    {row.to_string()}" for row in rows[:limit]]
        if len(rows) > limit:
            lines.append(f"    … and {len(rows) - limit} more")
        return "\n".join(lines)


class _ConnectionHandler:
    """Serves one client connection for the lifetime of that connection.

    Split out of ``RecordingProxy`` so the per-connection state (the two
    sockets) is not shared mutable state on the server object.
    """

    def __init__(self, client: socket.socket, recorder: NetworkRecorder) -> None:
        """Store the accepted socket and the shared recorder.

        Args:
            client: The accepted client socket.
            recorder: Shared mode/log state.

        """
        self._client = client
        self._recorder = recorder

    def serve(self) -> None:
        """Read one request, apply the current mode, and act on it.

        Swallows every exception: a proxy thread raising into nothing would
        be invisible, and a client that disappears mid-handshake (which
        Chromium does constantly, for speculative sockets) is normal rather
        than a fault. Genuine faults surface as a failed assertion in the
        test, which is where they are legible.
        """
        try:
            self._serve()
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("proxy connection ended: %s", exc)
        finally:
            try:
                self._client.close()
            except OSError:  # pragma: no cover - defensive
                pass

    def _serve(self) -> None:
        """Parse the request line and dispatch to the CONNECT or HTTP path."""
        self._client.settimeout(_CLIENT_READ_TIMEOUT_S)
        reader = self._client.makefile("rb")
        request_line = reader.readline(65536)
        if not request_line:
            # A speculative socket the browser opened and never used.
            return
        parts = request_line.decode("latin-1").split()
        if len(parts) < 3:
            return
        method, target, _version = parts[0], parts[1], parts[2]

        headers: list[tuple[str, str]] = []
        while True:
            line = reader.readline(65536)
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            headers.append((name.strip(), value.strip()))

        if method == "CONNECT":
            self._handle_connect(target)
            return
        self._handle_http(method, target, headers, reader)

    # -- CONNECT (TLS tunnel) ------------------------------------------------

    def _handle_connect(self, authority: str) -> None:
        """Record and then tunnel, refuse or hang a TLS connection.

        Args:
            authority: The ``host:port`` from the CONNECT request line.

        """
        mode = self._recorder.current_mode()
        outcome = {
            "pass": "forwarded",
            "reject": "rejected",
            "blackhole": "blackholed",
        }[mode]
        self._recorder.record(
            Exchange(
                method="CONNECT",
                host=authority,
                url=f"https://{authority}",
                at=time.monotonic(),
                outcome=outcome,
            )
        )
        if mode == "reject":
            self._reset()
            return
        if mode == "blackhole":
            self._hang()
            return

        host, _, port_text = authority.rpartition(":")
        try:
            upstream = socket.create_connection(
                (host, int(port_text)), timeout=_UPSTREAM_TIMEOUT_S
            )
        except OSError:
            self._client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        self._client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._relay(self._client, upstream)

    # -- Plain HTTP ----------------------------------------------------------

    def _handle_http(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        reader: object,
    ) -> None:
        """Record and then forward, refuse or hang a plain-HTTP request.

        Args:
            method: HTTP method.
            target: Absolute-form request target, as a proxy always receives.
            headers: Request headers, in wire order.
            reader: Buffered reader positioned at the request body.

        """
        mode = self._recorder.current_mode()
        split = urlsplit(target)
        outcome = {
            "pass": "forwarded",
            "reject": "rejected",
            "blackhole": "blackholed",
        }[mode]
        self._recorder.record(
            Exchange(
                method=method,
                host=split.netloc,
                url=target,
                at=time.monotonic(),
                outcome=outcome,
            )
        )
        if mode == "reject":
            self._reset()
            return
        if mode == "blackhole":
            self._hang()
            return

        header_map = {name: value for name, value in headers}
        body = b""
        length = header_map.get("Content-Length")
        if length and length.isdigit():
            body = reader.read(int(length))  # type: ignore[attr-defined]

        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        try:
            upstream = HTTPConnection(split.netloc, timeout=_UPSTREAM_TIMEOUT_S)
            # Hop-by-hop headers must not be relayed; Connection in
            # particular would have the upstream close a socket the browser
            # expects to keep.
            forwarded = {
                name: value
                for name, value in headers
                if name.lower() not in {"proxy-connection", "connection", "keep-alive"}
            }
            upstream.request(method, path, body=body or None, headers=forwarded)
            response = upstream.getresponse()
            payload = response.read()
        except OSError:
            self._client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        # Rebuilt rather than relayed verbatim: the payload has already been
        # de-chunked by http.client, so any Transfer-Encoding or original
        # Content-Length on the response is now wrong.
        out = [f"HTTP/1.1 {response.status} {response.reason}"]
        for name, value in response.getheaders():
            if name.lower() in {"transfer-encoding", "content-length", "connection"}:
                continue
            out.append(f"{name}: {value}")
        out.append(f"Content-Length: {len(payload)}")
        out.append("Connection: close")
        head = ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")
        self._client.sendall(head + payload)

    # -- Shared mechanics ----------------------------------------------------

    def _reset(self) -> None:
        """Tear the connection down at TCP level, sending no HTTP response.

        This is ``reject`` mode, and the *how* is the whole of it. The
        obvious implementation — answer ``502 Bad Gateway`` — does not
        simulate a dead radio: a 502 is a perfectly successful HTTP
        exchange, so ``fetch`` RESOLVES with a 502 response and not one
        ``catch`` branch in the app ever runs. That reads as a passing test
        while proving nothing, which is worse than no test.

        An RST (``SO_LINGER`` with a zero timeout, so ``close`` sends RST
        rather than FIN) surfaces to Chromium as
        ``net::ERR_CONNECTION_RESET`` and to the page as a rejected
        ``fetch`` — which is the condition every offline fallback in
        ``sw.js`` and ``pwa_offline.js`` was written against.
        """
        try:
            self._client.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            self._client.close()
        except OSError:  # pragma: no cover - defensive
            pass

    def _hang(self) -> None:
        """Hold the connection open, answering nothing, until it dies.

        This is the whole point of ``blackhole`` mode. A closed socket would
        surface to ``fetch`` as a rejection, which is the condition the app
        already handles; holding it open is what produces the pending
        promise ``_boundedFetch``'s budget exists to abort.
        """
        deadline = time.monotonic() + _CLIENT_READ_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                self._client.settimeout(0.5)
                if self._client.recv(_RELAY_CHUNK) == b"":
                    return
            except TimeoutError:
                continue
            except OSError:
                return

    @staticmethod
    def _relay(left: socket.socket, right: socket.socket) -> None:
        """Pump bytes both ways until either side closes.

        Args:
            left: One end of the tunnel.
            right: The other end.

        """

        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    chunk = src.recv(_RELAY_CHUNK)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                for sock in (src, dst):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        forward = threading.Thread(target=pump, args=(left, right), daemon=True)
        forward.start()
        pump(right, left)
        forward.join(timeout=5)
        try:
            right.close()
        except OSError:
            pass


class RecordingProxy:
    """A proxy the browser is pointed at for the whole of one test.

    Usage::

        with RecordingProxy() as proxy:
            context = browser.new_context(proxy=proxy.playwright_proxy())
            ...
            proxy.recorder.set_mode("reject")

    The server binds an ephemeral port on loopback, so parallel workers do
    not collide.
    """

    def __init__(self) -> None:
        """Bind the listening socket without yet accepting on it."""
        self.recorder = NetworkRecorder()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(128)
        self._server.settimeout(0.5)
        self.port: int = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> RecordingProxy:
        """Start the accept loop and return self."""
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop the accept loop and close the listening socket."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self._server.close()
        except OSError:  # pragma: no cover - defensive
            pass

    def playwright_proxy(self) -> dict[str, str]:
        """Return the ``proxy=`` argument for ``browser.new_context``.

        ``bypass`` is the load-bearing part. Chromium bypasses the proxy for
        loopback by DEFAULT, and the live server is on loopback — so without
        ``<-loopback>`` every same-origin request the app makes would go
        straight to the server, unseen and unblockable, and the suite would
        cheerfully report that no traffic left the device while the app
        talked to the server the whole time. That is the exact failure this
        suite exists to detect, so getting it wrong here would make every
        assertion below vacuous.
        """
        return {"server": f"http://127.0.0.1:{self.port}", "bypass": "<-loopback>"}

    def _accept_loop(self) -> None:
        """Accept connections until stopped, one handler thread each."""
        while not self._stop.is_set():
            try:
                client, _addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:  # pragma: no cover - listener closed under us
                return
            handler = _ConnectionHandler(client, self.recorder)
            threading.Thread(target=handler.serve, daemon=True).start()
