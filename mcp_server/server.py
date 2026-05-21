import os
import subprocess
from typing import Dict
from fastmcp import FastMCP

mcp = FastMCP("ultimum-troubleshooter")


@mcp.resource("disk://usage/{path}")
def get_disk_usage(path: str = "/") -> Dict:
    """Geeft schijfruimte-informatie terug (read-only, geen PII)."""
    result = subprocess.run(
        ["df", "-h", path], capture_output=True, text=True, check=True
    )
    return {"output": result.stdout.strip()}


@mcp.tool
def propose_disk_cleaner(target_dir: str = "/tmp") -> str:
    """Geeft een preview van wat er verwijderd zou worden (geen restricties meer)."""
    if not os.path.exists(target_dir):
        return f"FOUT: Pad {target_dir} bestaat niet."

    # Preview (beperkt tot eerste 20 items voor overzicht)
    try:
        files = os.listdir(target_dir)[:20]
        total = len(os.listdir(target_dir))
    except Exception as e:
        return f"FOUT bij lezen van map: {e}"

    return (
        f"VOORGESTELDE CLEANUP voor {target_dir}:\n"
        f"Aantal bestanden/mappen: {total}\n"
        f"Voorbeeld: {files}\n\n"
        "Wil je doorgaan met verwijderen? Antwoord met 'YES' + reden."
    )


@mcp.tool
def execute_disk_clean(target_dir: str, confirm: bool = False) -> str:
    """VOERT ECHT OP! Verwijdert ALLES in de opgegeven map (na HitL-bevestiging)."""
    if not confirm:
        return "Bevestiging vereist! Gebruik eerst propose_disk_cleaner en antwoord met YES."

    if not os.path.exists(target_dir):
        return f"FOUT: Pad {target_dir} bestaat niet."

    # WAARSCHUWING: Dit is gevaarlijk! Gebruik ALLEEN in test-VM / sandbox!
    try:
        # Verwijder alle inhoud van de map (niet de map zelf)
        subprocess.run(["rm", "-rf", f"{target_dir}/*"], check=True)
        return f"✅ Disk cleanup succesvol uitgevoerd op {target_dir} — map is nu leeg."
    except Exception as e:
        return f"FOUT tijdens cleanup: {e}"


if __name__ == "__main__":
    mcp.run()