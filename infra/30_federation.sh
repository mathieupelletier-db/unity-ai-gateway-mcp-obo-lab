#!/usr/bin/env bash
# Account federation policy: accept v2 Entra tokens issued for the lab agent backend, map them by email.
set -euo pipefail
cd "$(dirname "$0")/.."
TENANT_ID=$(grep ^TENANT_ID= .lab.env | cut -d= -f2)
BE_APP_ID=$(grep ^BACKEND_CLIENT_ID= .lab.env | cut -d= -f2)

POLICY_ID=$(databricks account federation-policy create --profile "<your-cli-account-profile>" -o json --json "{
  \"description\": \"lab: mcp-lab-agent-backend user tokens\",
  \"oidc_policy\": {
    \"issuer\": \"https://login.microsoftonline.com/$TENANT_ID/v2.0\",
    \"audiences\": [\"$BE_APP_ID\"],
    \"subject_claim\": \"email\"
  }
}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('policy_id') or d['name'].rsplit('/',1)[-1])")

{
  grep -v '^FEDERATION_POLICY_ID=' .lab.env || true
  echo "FEDERATION_POLICY_ID=$POLICY_ID"
} > .lab.env.tmp
mv .lab.env.tmp .lab.env
echo "Federation policy: $POLICY_ID"
