# Akshrava documentation

| Document | Canonical purpose |
|---|---|
| [Protocol](PROTOCOL.md) | Exact phone-to-backend wire contract and safety invariants |
| [Android](ANDROID.md) | Android compatibility, resource limits, and device scope |
| [Deployment](DEPLOYMENT.md) | Infrastructure roles, migrations, mTLS, worker failover, and backups |
| [Operations](OPERATIONS.md) | Operator actions, provisioning commands, and failure handling |
| [Release and verification](RELEASE_AND_VERIFICATION.md) | CI/release sequence and reproducible checks |
| [Field guide](FIELD_GUIDE.md) | Device qualification, release gates, and supervised trials |
| [Privacy](PRIVACY.md) | Data minimisation, retention, consent, and incident handling |
| [Cloud fallback](CLOUD_IMAGE_FALLBACK.md) | Optional provider fallback and privacy constraints |
| [Transport ADR](ADR/0001-transport-evolution.md) | Staged WebTransport/HTTP3 decision |

The architecture overview is [Important Architecture.md](../Important%20Architecture.md). It is a
design reference; these guides are the operational source of truth.
