"""SPIKE (throwaway): run a client.lab command against the App Gateway-backed MCP Service.

Usage: uv run python infra/spike_agw/run_lab.py <cmd> --tag <label>   (inbound is always entra)
"""
import argparse
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from client.config import LabConfig, read_env_file  # noqa: E402
from client.lab import run  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("cmd")
parser.add_argument("--tag", required=True)
parser.add_argument("--calls", type=int, default=30)
args = parser.parse_args()
fqdn = read_env_file(ROOT / ".lab.env")["AGW_FQDN"]
cfg = dataclasses.replace(LabConfig.load(), mcp_service_name="mcp_obo_lab.mcp.mcp_lab_mcp_agw", mcp_server_url=f"https://{fqdn}/mcp")
run(args.cmd, cfg, "entra", args.calls, args.tag)
