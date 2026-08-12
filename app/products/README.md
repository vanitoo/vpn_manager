# Product modules

The existing VPN implementation is intentionally left in its current modules.
Optional products live below `app/products/` and connect to application startup
through `ProductModule`.

Rules for a product module:

- use its own database tables and repository;
- use its own callback prefix and FSM states;
- do not import VPN handlers or `RemnawaveClient`;
- expose routers and an optional idempotent database initializer;
- remain disabled by default behind a feature flag;
- handle payment fulfillment through an explicit product binding, preserving
  the existing VPN payment path when no binding exists.

The initial SOCKS5 boundary is deliberately empty. Its implementation is kept
in the `feature/socks5` branch.

