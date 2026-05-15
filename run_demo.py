"""Quick CLI demo: runs one case per scenario end-to-end (mock LLM)."""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from simulator.log_generator import make_case
from agent.agent import TroubleshooterAgent
from agent.backends import DirectBackend
from agent.llm_client import MockLLM

rng = random.Random(1)
for i, scenario in enumerate(["disk_space", "performance", "vpn"], 1):
    case = make_case(i, scenario, rng)
    s = case["state"]
    backend = DirectBackend(); backend.load_state(s)
    result = TroubleshooterAgent(backend, MockLLM()).diagnose(
        hostname=s["hostname"], customer=s["customer"], user=s["user"],
        trigger=s["logs"][-1])
    d = result.diagnosis
    print(f"\n=== {s['case_id']} [{scenario}] {s['hostname']} ({s['customer']}) ===")
    print(f"  diagnose : {d.scenario} (confidence {d.confidence:.0%}, {result.latency_s:.2f}s)")
    print(f"  oorzaak  : {d.root_cause}")
    print(f"  voorstel : {d.proposed_action} -> wacht op HitL-goedkeuring")
    print(f"  tools    : {[t['name'] for t in result.tool_calls]}")
