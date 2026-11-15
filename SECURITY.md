# Security

EcoCentinela is a conceptual portfolio project: the field nodes are simulated and there are no customers or
personal data. The cloud deployment, however, is real, so it is hardened as if it were production.

## Reporting a vulnerability

Please use **GitHub → Security → Report a vulnerability** (private vulnerability reporting). Do not open a public issue.

## What is public on purpose

| Item | Why it is not a secret |
|---|---|
| Project ID, project number, service-account e-mails, Workload Identity provider name | Identifiers, not credentials. GitHub can only impersonate the deploy account with an OIDC token issued for **this repository ID** on branch `main`. |
| Dashboard URL | The dashboard is read-only for everyone. Writing (registering a spray) requires Google Sign-In with an account on an allowlist kept in Secret Manager. |
| OAuth client ID | Public by design in Google Sign-In; the server verifies signature, audience, `email_verified` and the allowlist. |

## What is never in this repository

* Keys or tokens of any kind: CI/CD uses Workload Identity Federation (no service-account keys exist).
* `infra/terraform/terraform.tfvars` (billing account, alert e-mail) — git-ignored.
* The Gemini API key and the allowlist of e-mails — stored in Secret Manager and loaded with `gcloud`, so they never reach the Terraform state either.
* Third-party PDFs used as sources and personal interview notes (`docs/fuentes_v2/`, `docs/guia/`) — git-ignored.

## Cost controls (abuse of the public dashboard)

* Dashboard: max 1 Cloud Run instance, shared in-memory cache (Firestore ≤ 1 read/30 s, BigQuery ≤ 1 query/10 min), WebSocket sessions capped at 10 min.
* Gemini agent: free-tier key in a project **without billing** (it cannot generate charges) plus a global limit of 30 questions/hour.
* Ingestion and the billing kill switch only accept IAM-authenticated calls.
* Monthly budget with an automatic kill switch that unlinks billing when the cap is reached (see `services/corte`).

## Repository settings

Secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting,
branch protection on `main` (no force pushes or deletion), read-only default `GITHUB_TOKEN`, and actions pinned by commit SHA.
