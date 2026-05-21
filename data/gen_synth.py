import json
import random
from datetime import datetime, timedelta


def generate_disk_space_logs(n=12):
    hosts = ["SRV-CLIENT-042", "SRV-CLIENT-107", "SRV-DEV-003", "SRV-PROD-021"]
    drives = ["C:", "D:", "E:"]
    logs = []

    for i in range(n):
        host = random.choice(hosts)
        drive = random.choice(drives)
        total_gb = random.choice([120, 150, 250, 500])

        # Keep pct_free under 10% to match the threshold warning logic
        pct_free = round(random.uniform(1.0, 9.9), 1)
        free_gb = round((pct_free / 100) * total_gb, 1)

        timestamp = (datetime.now() - timedelta(minutes=random.randint(5, 180))).strftime("%Y-%m-%d %H:%M:%S")

        log_text = (
            f"[{timestamp}] WARNING: Low disk space on {drive}\n"
            f"Host: {host}\n"
            f"Free space: {free_gb} GB of {total_gb} GB ({pct_free}% free)\n"
            f"Threshold: < 10% free space"
        )

        logs.append({
            "id": f"disk_{i + 1:03d}",
            "scenario": "disk_space",
            "input_log": log_text,
            "ground_truth_diagnosis": f"Disk space on {drive} is critically low ({pct_free}% free).",
            "ground_truth_action": "Notify user, clear temporary files, or expand disk volume.",
            "expected_mcp_tools": ["check_disk_space", "get_system_info"],
            "notes": ""
        })

    return logs


if __name__ == "__main__":
    dataset = generate_disk_space_logs(12)

    with open("synth/test_dataset_disk.json", "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated and saved {len(dataset)} logs.")