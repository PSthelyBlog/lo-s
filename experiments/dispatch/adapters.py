"""Model adapters.

One contract for every provider: a system prompt, a user message and a JSON schema go in,
and a dict comes out. `complete_valid` checks the dict against the schema and retries, so
nothing here relies on a provider guaranteeing the shape of its output.
"""
import json
import subprocess
import tempfile
import time
import urllib.request


class ClaudeCli:
    """Runs the user's own `claude` CLI in print mode. The login never leaves the CLI."""

    provider = "claude-cli"

    def __init__(self, model="claude-opus-5-5", effort="medium"):
        self.model, self.effort = model, effort
        # A neutral directory, so no project instructions or memory reach the model.
        self.cwd = tempfile.mkdtemp(prefix="los-claude-")

    def complete(self, system, user, schema):
        cmd = ["claude", "-p", "--safe-mode", "--model", self.model, "--effort", self.effort,
               "--tools", "", "--no-session-persistence", "--output-format", "json",
               "--system-prompt", system, "--json-schema", json.dumps(schema)]
        start = time.time()
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=self.cwd, timeout=300)
        reply = json.loads(proc.stdout)
        if reply.get("is_error"):
            raise RuntimeError(reply.get("result"))
        return reply["structured_output"], {
            "seconds": round(time.time() - start, 2),
            "output_tokens": reply.get("usage", {}).get("output_tokens"),
        }


class OpenAICompat:
    """Any server that speaks the OpenAI chat API, llama.cpp's llama-server included."""

    provider = "openai-compat"

    def __init__(self, base_url, model, extra=None):
        self.base_url, self.model, self.extra = base_url.rstrip("/"), model, extra or {}

    def complete(self, system, user, schema):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "seed": 1,
            "max_tokens": 300,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "output", "schema": schema, "strict": True}},
            **self.extra,
        }
        request = urllib.request.Request(self.base_url + "/chat/completions", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
        start = time.time()
        with urllib.request.urlopen(request, timeout=900) as response:
            reply = json.load(response)
        timings = reply.get("timings", {})
        return json.loads(reply["choices"][0]["message"]["content"]), {
            "seconds": round(time.time() - start, 2),
            "output_tokens": reply.get("usage", {}).get("completion_tokens"),
            "prompt_tokens": reply.get("usage", {}).get("prompt_tokens"),
            "prompt_tokens_evaluated": timings.get("prompt_n"),
            "prompt_tokens_per_second": timings.get("prompt_per_second"),
            "output_tokens_per_second": timings.get("predicted_per_second"),
        }


def validate(value, schema, path="$"):
    """Check a value against the subset of JSON Schema this project uses. Returns the problems found."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}: not an object"]
        properties = schema.get("properties", {})
        problems = [f"{path}: missing {key}" for key in schema.get("required", []) if key not in value]
        if schema.get("additionalProperties") is False:
            problems += [f"{path}: unexpected {key}" for key in value if key not in properties]
        for key, sub in properties.items():
            if key in value:
                problems += validate(value[key], sub, f"{path}.{key}")
        return problems
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: not an array"]
        return [p for i, item in enumerate(value) for p in validate(item, schema.get("items", {}), f"{path}[{i}]")]
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path}: not a string"]
        if "enum" in schema and value not in schema["enum"]:
            return [f"{path}: {value!r} is not an allowed value"]
    return []


def complete_valid(model, system, user, schema, tries=3):
    """Ask until the output validates. Returns (output, meta); meta records how many tries it took."""
    problems = []
    for attempt in range(1, tries + 1):
        try:
            output, meta = model.complete(system, user, schema)
        except Exception as error:  # a failed call counts as a failed try
            problems = [f"{type(error).__name__}: {error}"]
            continue
        problems = validate(output, schema)
        if not problems:
            meta["tries"] = attempt
            return output, meta
    raise RuntimeError(f"no valid output after {tries} tries: {problems}")
