"""A Windows Proactor event loop whose listening socket survives a failed accept.

WHY (site outage 2026-11-01 11:12 ET)
-------------------------------------
On Windows uvicorn runs on ``asyncio.ProactorEventLoop``. When one AcceptEx
completion fails -- typically ``OSError [WinError 64] The specified network name
is no longer available`` because the client (or the tunnel in front of us)
dropped the half-open connection -- CPython's
``BaseProactorEventLoop._start_serving`` logs "Accept failed on a socket" and
CLOSES THE LISTENING SOCKET. It never re-arms, so the HTTP listener is dead
while the process, its scheduler and its health supervisor all stay up.

WHAT THIS DOES
--------------
``ResilientProactorEventLoop._start_serving`` is the installed CPython
implementation with one change: in the ``OSError`` branch, when the error is a
per-connection failure (``RETRYABLE_WINERRORS``) and both the loop and the
listening socket are still open, it re-arms ``accept`` after a short backoff
(``BACKOFF_INITIAL``..``BACKOFF_MAX`` seconds, reset after every successful
accept) instead of closing the listener. Every other error keeps the stock
behaviour (log + close).

The copy is only used when the installed ``_start_serving`` is byte-for-byte
the source it was copied from (``STOCK_SOURCE_SHA256``); on any other Python
build the stock implementation runs unchanged (``RESILIENT_ACTIVE`` False).

It stays a Proactor loop on purpose: the admin job route runs jobs through
``asyncio.create_subprocess_exec``, which a Selector loop cannot do on Windows.

Wired by ``scripts/asp-backend-supervisor.ps1``:
``uvicorn ... --loop app.winloop:ResilientProactorEventLoop``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import sys

logger = logging.getLogger("app.winloop")

#: Per-connection accept failures that must not take the listener down:
#: 64 ERROR_NETNAME_DELETED, 121 ERROR_SEM_TIMEOUT, 1236 ERROR_CONNECTION_ABORTED,
#: 10053 WSAECONNABORTED, 10054 WSAECONNRESET.
RETRYABLE_WINERRORS = frozenset({64, 121, 1236, 10053, 10054})
BACKOFF_INITIAL = 0.05
BACKOFF_MAX = 1.0

#: sha256 of ``inspect.getsource(BaseProactorEventLoop._start_serving)`` on the
#: CPython this override was copied from (Anaconda 3.12.4, the production venv).
STOCK_SOURCE_SHA256 = "7b6bcf24da6fafb38c63e276e1de553706da3f0d0bbc7300b521cb4599145afe"


def _stock_source_sha256() -> str | None:
    try:
        from asyncio import proactor_events

        source = inspect.getsource(proactor_events.BaseProactorEventLoop._start_serving)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 - unknown build: fall back to stock
        return None


def stock_matches() -> bool:
    return sys.platform == "win32" and _stock_source_sha256() == STOCK_SOURCE_SHA256


def is_retryable_accept_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in RETRYABLE_WINERRORS


def _resilient_start_serving(self, protocol_factory, sock,
                             sslcontext=None, server=None, backlog=100,
                             ssl_handshake_timeout=None,
                             ssl_shutdown_timeout=None):
    # Copied from CPython 3.12.4 Lib/asyncio/proactor_events.py
    # BaseProactorEventLoop._start_serving; the only change is the retryable
    # branch inside ``except OSError``.
    from asyncio import exceptions, trsock
    from asyncio.log import logger as asyncio_logger

    backoff = {"delay": BACKOFF_INITIAL}

    def loop(f=None):
        try:
            if f is not None:
                conn, addr = f.result()
                backoff["delay"] = BACKOFF_INITIAL
                if self._debug:
                    asyncio_logger.debug("%r got a new connection from %r: %r",
                                         server, addr, conn)
                protocol = protocol_factory()
                if sslcontext is not None:
                    self._make_ssl_transport(
                        conn, protocol, sslcontext, server_side=True,
                        extra={'peername': addr}, server=server,
                        ssl_handshake_timeout=ssl_handshake_timeout,
                        ssl_shutdown_timeout=ssl_shutdown_timeout)
                else:
                    self._make_socket_transport(
                        conn, protocol,
                        extra={'peername': addr}, server=server)
            if self.is_closed():
                return
            f = self._proactor.accept(sock)
        except OSError as exc:
            if sock.fileno() != -1 and is_retryable_accept_error(exc) and not self.is_closed():
                delay = backoff["delay"]
                backoff["delay"] = min(delay * 2, BACKOFF_MAX)
                logger.warning("Accept failed on a socket (winerror %s); listener kept, re-arming in %.2fs",
                               getattr(exc, "winerror", None), delay)
                self.call_later(delay, _rearm)
            elif sock.fileno() != -1:
                self.call_exception_handler({
                    'message': 'Accept failed on a socket',
                    'exception': exc,
                    'socket': trsock.TransportSocket(sock),
                })
                sock.close()
            elif self._debug:
                asyncio_logger.debug("Accept failed on socket %r",
                                     sock, exc_info=True)
        except exceptions.CancelledError:
            sock.close()
        else:
            self._accept_futures[sock.fileno()] = f
            f.add_done_callback(loop)

    def _rearm():
        # The server may have been closed (socket closed) or the loop stopped during the backoff.
        if self.is_closed() or sock.fileno() == -1:
            return
        loop()

    self.call_soon(loop)


RESILIENT_ACTIVE = stock_matches()

if sys.platform == "win32":
    class ResilientProactorEventLoop(asyncio.ProactorEventLoop):  # type: ignore[name-defined,misc]
        if RESILIENT_ACTIVE:
            _start_serving = _resilient_start_serving
else:  # pragma: no cover - production is Windows; elsewhere behave like the default loop
    ResilientProactorEventLoop = asyncio.SelectorEventLoop  # type: ignore[misc,assignment]


__all__ = ["ResilientProactorEventLoop", "RESILIENT_ACTIVE", "RETRYABLE_WINERRORS", "is_retryable_accept_error"]
