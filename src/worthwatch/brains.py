"""The brains that can do worthwatch's judging, and how to tell which ones this machine has.

  agent   your own AI agent (Claude Code, Codex, Cursor and others) through `worthwatch mcp`:
          worthwatch fetches and checks, the agent judges, and worthwatch calls no model itself
  claude  Claude Code's command line, `claude -p`, with the account you already have
  codex   OpenAI's Codex command line, `codex exec`, with the account you already have
  api     any OpenAI-compatible API, with its key in an environment variable: Google's Gemini
          (a free key from Google AI Studio), OpenRouter, OpenAI or Groq
  ollama  a model running on this machine with Ollama

`worthwatch init` asks which one to use and records it in settings.toml ([brain] kind);
`worthwatch doctor` checks that the one you chose is there. A key is only ever looked up by its
variable's name: its value is never printed, logged or written anywhere.
"""
import json
import os
import shutil
import urllib.error
import urllib.request

BRAIN_KINDS = ("agent", "claude", "codex", "api", "ollama")
BRAIN_WORDS = {
    "agent": "your own AI agent, through worthwatch's MCP server (worthwatch calls no model)",
    "claude": "Claude Code's command line (claude -p), with your own account",
    "codex": "OpenAI's Codex command line (codex exec), with your own account",
    "api": "an OpenAI-compatible API, its key in an environment variable "
           "(Gemini's free key, OpenRouter, OpenAI or Groq)",
    "ollama": "a local model with Ollama, on this machine",
}
# In the order a key is looked for. Each names the service, so the doctor can say which.
API_KEY_VARIABLES = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL", "http://localhost:11434")
# The order init suggests a brain in when nobody chooses: a tool you already have first, and agent
# mode, which needs nothing, last.
SUGGEST_ORDER = ("claude", "codex", "api", "ollama", "agent")


def ollama_models(timeout=1.0):
    """The models a local Ollama has pulled. Raises OSError when it does not answer, and at once
    when WORTHWATCH_NO_NETWORK_CHECKS is set (tests and offline machines)."""
    if os.environ.get("WORTHWATCH_NO_NETWORK_CHECKS"):
        raise OSError("not asked: WORTHWATCH_NO_NETWORK_CHECKS is set")
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as e:
        raise OSError(str(getattr(e, "reason", e))) from e
    return [m.get("name") for m in data.get("models", []) if m.get("name")]


def detect(env=None, which=None, ollama_models=ollama_models):
    """{kind: (available, how)} for every brain, in plain words. Never shows a key's value."""
    env = os.environ if env is None else env
    which = which or shutil.which
    found = {"agent": (True, "always there: connect worthwatch to your agent with `worthwatch mcp`")}
    for kind, command in (("claude", "claude"), ("codex", "codex")):
        path = which(command)
        found[kind] = ((True, f"`{command}` is installed") if path
                       else (False, f"`{command}` is not installed, or not on the PATH"))
    named = [name for name in API_KEY_VARIABLES if (env.get(name) or "").strip()]
    found["api"] = ((True, f"a key is set in {named[0]}") if named
                    else (False, "no key is set in " + ", ".join(API_KEY_VARIABLES)))
    try:
        models = ollama_models()
        found["ollama"] = ((True, f"Ollama answers, with {len(models)} model(s) pulled") if models
                           else (False, "Ollama answers, but has no model pulled yet"))
    except OSError as e:
        found["ollama"] = (False, f"Ollama is not answering ({e})")
    return found
