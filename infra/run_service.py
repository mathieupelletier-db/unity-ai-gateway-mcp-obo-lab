"""Run a client.lab command against any lab MCP Service with inbound Entra OBO (e.g. the Atlassian one).

Usage: uv run python infra/run_service.py --service mcp_lab_atlassian tools --tag atlassian
       uv run python infra/run_service.py --service mcp_lab_atlassian call --tool NAME --args '{}' --tag atlassian
"""
import argparse
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from client.config import LabConfig  # noqa: E402
from client.lab import run  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("cmd")
parser.add_argument("--service", required=True, help="service name inside mcp_obo_lab.mcp")
parser.add_argument("--tag", required=True)
parser.add_argument("--tool")
parser.add_argument("--args", default="{}")
parser.add_argument("--calls", type=int, default=30)
a = parser.parse_args()
cfg = dataclasses.replace(LabConfig.load(), mcp_service_name=f"mcp_obo_lab.mcp.{a.service}")
run(a.cmd, cfg, "entra", a.calls, a.tag, tool=a.tool, args=a.args)
