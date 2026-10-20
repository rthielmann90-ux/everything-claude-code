"""Send one chat request through OllamaProvider and print the reply.

Run from the repo root: PYTHONPATH=src python3 scripts/ollama_cloud_check.py
OLLAMA_API_KEY is read from the environment or from .env.
"""

import os

from llm.core.types import LLMInput, Message, Role
from llm.providers.resolver import _read_saved_llm_config, get_provider

for name, value in _read_saved_llm_config(".env").items():
    os.environ.setdefault(name, value)

provider = get_provider("ollama")
if not provider.api_key:
    raise SystemExit("OLLAMA_API_KEY is not set (environment or .env)")

output = provider.generate(
    LLMInput(messages=[Message(role=Role.USER, content="Antworte mit einem kurzen Satz: Warum ist der Himmel blau?")])
)
print(f"model: {output.model}")
print(output.content)
