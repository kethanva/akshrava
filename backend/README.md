# Akshrava backend

The backend accepts authenticated, current JPEG frames from the Android assistance service,
applies the bounded detection/tracking/alert policy, and returns compact results. It is a
supervised-pilot component: it never supplies crossing, collision-avoidance, approach-speed or
clear-path claims.

Run the repository test command from the project root:

```bash
./scripts/test_backend.sh
```

See the [field guide](../docs/README.md#field-readiness-and-supervised-trials) before field use.
