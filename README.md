# Meta Ads Control

Small control service for resolving valid Meta Ads targeting interests. It does not store ad-account data and does not modify campaigns. Actual ad-account writes remain in the connected ads-management workflow.

Required environment variable: `META_ACCESS_TOKEN`.

Endpoints:
- `GET /health`
- `GET /interests?q=cricket`
- `GET /interests/batch?q=cricket,sports%20betting,sportsbook`

Never commit tokens or secrets to this repository.
