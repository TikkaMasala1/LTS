"""
Simulator: generates representative "machine states" + log lines for the
first support scenario of the PoC:

  1. disk_space   – disk space warnings (C: drive nearly full)

Plus 'healthy' states (no incident) as a control group.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

FIRST = ["Jan", "Sanne", "Pieter", "Fatima", "Lars", "Esra", "Daan", "Noor", "Tom", "Mila"]
LAST = ["de Vries", "Jansen", "Bakker", "Visser", "Yilmaz", "van Dijk", "Smit", "Mulder"]
CUSTOMERS = ["Acme B.V.", "Zorggroep Flevo", "Bouwbedrijf Hendriks", "Notariskantoor Peters",
             "Logistiek Almere", "De Groene Kas", "FinTrust Advies"]
VPN_GATEWAYS = ["vpn.acme.nl", "gw01.ultimum-vpn.nl", "vpn.zorgflevo.nl"]


def _ts(base: datetime, offset_min: int) -> str:
    return (base + timedelta(minutes=offset_min)).strftime("%Y-%m-%d %H:%M:%S")


def make_case(case_id: int, scenario: str, rng: random.Random) -> dict:
    base = datetime(2026, 4, 7, 8, 0) + timedelta(hours=rng.randint(0, 200))
    user = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    username = (user[0] + "." + user.split(" ", 1)[1].replace(" ", "")).lower()
    customer = rng.choice(CUSTOMERS)
    hostname = f"WS-{customer[:5].upper()}-{rng.randint(10, 99)}"

    state: dict = {
        "case_id": f"C{case_id:03d}",
        "scenario": scenario,
        "hostname": hostname,
        "user": user,
        "username": username,
        "customer": customer,
        "uptime_days": rng.randint(1, 12),
        "os": "Windows 11 Pro 23H2",
        "services": {
            "Spooler": "running", "WinDefend": "running",
            "RasMan": "running", "Dnscache": "running", "wuauserv": "running",
        },
        "pending_updates": rng.randint(0, 3),
    }

    logs: list[str] = []
    public_ip = f"83.{rng.randint(1,254)}.{rng.randint(1,254)}.{rng.randint(1,254)}"
    private_ip = f"10.0.{rng.randint(0,40)}.{rng.randint(2,250)}"

    # ---- healthy baseline values ------------------------------------------
    disk_total = rng.choice([256, 512])
    disk_used = round(disk_total * rng.uniform(0.45, 0.65), 1)
    cpu, ram = rng.randint(8, 30), rng.randint(35, 55)
    vpn = {
        "connected": True, "gateway": rng.choice(VPN_GATEWAYS),
        "protocol": "IKEv2", "latency_ms": rng.randint(18, 45),
        "packet_loss_pct": 0.0, "throughput_mbps": rng.randint(80, 240),
        "client_version": "5.2.1", "split_tunnel": True,
    }
    top_procs = [
        {"name": "Teams.exe", "cpu_pct": rng.randint(2, 6), "ram_mb": rng.randint(400, 900)},
        {"name": "chrome.exe", "cpu_pct": rng.randint(3, 8), "ram_mb": rng.randint(600, 1500)},
        {"name": "explorer.exe", "cpu_pct": 1, "ram_mb": 180},
    ]
    large_files = [
        {"path": "C:\\Users\\%s\\Videos\\training.mp4" % username, "size_gb": 2.1},
        {"path": "C:\\Windows\\Installer\\a8f2.msi", "size_gb": 1.2},
    ]
    temp_size_gb = round(rng.uniform(0.5, 2.0), 1)

    logs.append(f"{_ts(base,-300)} INFO  System    Boot completed on {hostname} ({private_ip})")
    logs.append(f"{_ts(base,-250)} INFO  Session   User {username} ({customer}) logged on")

    # ---- scenario-specific --------------------------------------------
    if scenario == "disk_space":
        disk_used = round(disk_total * rng.uniform(0.93, 0.995), 1)
        temp_size_gb = round(rng.uniform(6, 18), 1)
        large_files = [
            {"path": f"C:\\Users\\{username}\\Downloads\\backup_2025.zip", "size_gb": round(rng.uniform(8, 25), 1)},
            {"path": "C:\\Windows\\Temp\\dump_collection.tmp", "size_gb": round(rng.uniform(4, 9), 1)},
            {"path": f"C:\\Users\\{username}\\AppData\\Local\\Teams\\Cache", "size_gb": round(rng.uniform(2, 5), 1)},
        ]
        free = round(disk_total - disk_used, 1)
        logs += [
            f"{_ts(base,-40)} WARN  Storage   Low disk space on C: — {free} GB remaining ({round(100*disk_used/disk_total)}% used)",
            f"{_ts(base,-25)} ERROR OneDrive  Sync failed: not enough disk space on volume C:",
            f"{_ts(base,-10)} WARN  WinUpdate Update KB5036893 postponed: insufficient free space",
            f"{_ts(base,-2)}  ERROR AcmeERP   Could not write tempfile: DISK_FULL (0x70)",
        ]
        gt_action = "cleanup_disk"
    else:  # healthy
        logs += [
            f"{_ts(base,-30)} INFO  Health    Scheduled check OK — no anomalies detected",
            f"{_ts(base,-15)} INFO  Backup    Nightly backup completed successfully",
        ]
        gt_action = "no_action"

    state.update({
        "logs": logs,
        "disk": {"C:": {"total_gb": disk_total, "used_gb": disk_used,
                        "free_gb": round(disk_total - disk_used, 1),
                        "used_pct": round(100 * disk_used / disk_total, 1)}},
        "performance": {"cpu_pct": cpu, "ram_pct": ram, "top_processes": top_procs},
        "vpn": vpn,
        "large_files": large_files,
        "temp_size_gb": temp_size_gb,
        "network": {"private_ip": private_ip,
                    "ping_gateway_ms": vpn["latency_ms"] if scenario == "vpn" else rng.randint(1, 8),
                    "dns_ok": True},
    })

    ground_truth = {
        "scenario": scenario,
        "expected_action": gt_action,
        "required_tools": {
            "disk_space": ["get_recent_logs", "get_disk_usage"],
            "healthy": ["get_recent_logs"],
        }[scenario],
    }
    return {"state": state, "ground_truth": ground_truth}


def generate_dataset(n_per_scenario: int = 14, n_healthy: int = 3, seed: int = 42,
                     out_path: str | Path = "evaluation/dataset/testcases.json") -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict] = []
    cid = 1
    for scenario in ["disk_space"]:
        for _ in range(n_per_scenario):
            cases.append(make_case(cid, scenario, rng)); cid += 1
    for _ in range(n_healthy):
        cases.append(make_case(cid, "healthy", rng)); cid += 1
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    return cases


if __name__ == "__main__":
    cases = generate_dataset()
    print(f"{len(cases)} testcases gegenereerd -> evaluation/dataset/testcases.json")
