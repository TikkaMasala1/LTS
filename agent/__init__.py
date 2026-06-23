"""LTS troubleshooting-agent package.

Loads the project ``.env`` (if present) on import, so that ``OLLAMA_MODEL``,
``LTS_LLM``, ``GEMINI_API_KEY``, etc. actually take effect. Every entry point
(evaluation, demo, UI, MCP server) imports from ``agent``, so this is a single
place that covers them all. python-dotenv is a declared dependency; if it is
somehow missing we fall back to the real environment variables only and never
make the import fatal.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv is declared; keep import side effects non-fatal
    pass
