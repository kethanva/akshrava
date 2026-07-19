# ADR 0001: Transport-neutral gateway before HTTP/3 migration

## Status

Accepted for the production rollout; WebTransport is an explicit subsequent scale milestone.

## Context

The system currently uses secure WebSockets from Android phones. The architecture review proposes
WebTransport/QUIC plus a message bus at fleet scale to remove TCP head-of-line blocking and make
gateway nodes fully stateless.

## Decision

The current release retains WSS because it has an Android client implementation, accessibility
failure behavior, release matrix, and field validation. The WebSocket adapter now delegates every
validated frame to `SessionApplicationService`; it no longer owns calibration/inference business
transactions. That use case is deliberately transport-neutral and is the integration point for a
future WebTransport adapter or broker consumer.

## Consequences

- The rollout supports horizontally scaled GPU workers through Redis-backed replay protection and
  per-device frame admission control.
- A reconnect intentionally starts a fresh short-lived tracker session; no sticky session is
  required for safety because stale tracks are never reused.
- HTTP/3/WebTransport is not represented as available functionality until Android and server
  implementations are interoperable, benchmarked on carrier handovers, and pass the same safety
  test matrix. Shipping an unverified alternate transport in an assistive safety path would be a
  regression, not a scale improvement.

## Exit criteria for WebTransport

1. Android transport adapter passes API 28–36 instrumentation tests.
2. Gateway supports WSS and WebTransport protocol version negotiation with identical frame/result
   contract tests.
3. Carrier-handover, packet-loss, and stale-result trials prove no result is spoken beyond the
   phone-owned freshness deadline.
4. Broker-backed GPU result routing has bounded queue depth, cancellation, replay protection, and
   overload SLOs.
