# ADR-0004: cTrader Open API through JSON over WebSocket, demo host only

- Status: accepted
- Date: 2026-10-25

## Context

The user selected cTrader Open API for the first demo connector. The docs offer Protobuf (port 5035) and JSON (port 5036), over TCP/SSL or WebSocket. The project is standard-library only. The JSON pages do not document TCP framing, nor whether JSON uses WebSocket text or binary frames.

## Decision

- Use JSON on port 5036 over `wss://`. WebSocket frames carry message boundaries, which avoids relying on undocumented JSON-over-TCP framing, and JSON needs no protobuf dependency.
- Implement a minimal RFC 6455 client (`brokers/websocket.py`) instead of adding a third-party package. Frames are consumed only when complete, so read timeouts never lose bytes.
- Hard-code the demo host. Refuse `isLive: true` before account authorization; treat a missing `isLive` as UNKNOWN.
- Declare order lookup by client id unsupported; the gateway then keeps unresolved orders frozen for human review.
- Keep DEMO non-executable until a read-only check against the user's account confirms the assumptions listed in BROKERS.md.

## Alternatives

- Protobuf over TCP with the documented 4-byte length prefix: needs the `protobuf` package and generated classes; reconsider if the JSON endpoint proves unreliable.
- Third-party WebSocket library: adds a dependency for a small protocol surface; reconsider if TLS or proxy needs grow.

## Revisit when

The first supervised connection disagrees with any listed assumption, the account is hedged with several positions per symbol, or a second cTrader account must run concurrently (controls are global per store).
