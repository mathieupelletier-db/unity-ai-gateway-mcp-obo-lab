#!/usr/bin/env bash
# SPIKE (throwaway): VNet + our own private endpoint to the MCP App Service + private DNS, so an App Gateway in the
# VNet can reach the app privately while its public access stays Disabled. Idempotent-ish: skips existing resources.
set -euo pipefail
export AZURE_CONFIG_DIR="$HOME/.azure"
cd "$(dirname "$0")/../.."
RG=rg-mcp-obo-lab; LOC=canadacentral; VNET=vnet-mcp-lab
APP=$(grep ^MCP_APP_NAME= .lab.env | cut -d= -f2)
WEBAPP_ID=$(az webapp show -g "$RG" -n "$APP" --query id -o tsv)

az network vnet show -g "$RG" -n "$VNET" -o none 2>/dev/null || \
  az network vnet create -g "$RG" -n "$VNET" -l "$LOC" --address-prefixes 10.42.0.0/16 \
    --subnet-name snet-appgw --subnet-prefixes 10.42.1.0/24 -o none
az network vnet subnet show -g "$RG" --vnet-name "$VNET" -n snet-pe -o none 2>/dev/null || \
  az network vnet subnet create -g "$RG" --vnet-name "$VNET" -n snet-pe --address-prefixes 10.42.2.0/24 \
    --private-endpoint-network-policies Disabled -o none

az network private-endpoint show -g "$RG" -n pe-mcp-lab-mcp -o none 2>/dev/null || \
  az network private-endpoint create -g "$RG" -n pe-mcp-lab-mcp -l "$LOC" --vnet-name "$VNET" --subnet snet-pe \
    --private-connection-resource-id "$WEBAPP_ID" --group-id sites --connection-name pe-conn-mcp-lab-mcp -o none

az network private-dns zone show -g "$RG" -n privatelink.azurewebsites.net -o none 2>/dev/null || \
  az network private-dns zone create -g "$RG" -n privatelink.azurewebsites.net -o none
az network private-dns link vnet show -g "$RG" -z privatelink.azurewebsites.net -n link-mcp-lab -o none 2>/dev/null || \
  az network private-dns link vnet create -g "$RG" -z privatelink.azurewebsites.net -n link-mcp-lab -v "$VNET" -e false -o none
az network private-endpoint dns-zone-group show -g "$RG" --endpoint-name pe-mcp-lab-mcp -n default -o none 2>/dev/null || \
  az network private-endpoint dns-zone-group create -g "$RG" --endpoint-name pe-mcp-lab-mcp -n default \
    --private-dns-zone privatelink.azurewebsites.net --zone-name webapp -o none

echo "PE state: $(az network private-endpoint show -g "$RG" -n pe-mcp-lab-mcp --query 'privateLinkServiceConnections[0].privateLinkServiceConnectionState.status' -o tsv)"
echo "DNS A records: $(az network private-dns record-set a list -g "$RG" -z privatelink.azurewebsites.net --query "[].{n:name, ip:aRecords[0].ipv4Address}" -o tsv | tr '\n' ' ')"
echo "App public access: $(az resource show --ids "$WEBAPP_ID" --query properties.publicNetworkAccess -o tsv)"
