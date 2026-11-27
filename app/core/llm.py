"""Thin wrapper around the Groq chat API."""
import json
import re

from groq import Groq

from app import config

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        if not config.GROQ_API_KEY or config.GROQ_API_KEY.startswith("your_"):
            raise RuntimeError("GROQ_API_KEY is missing. Add your key to the .env file.")
        # max_retries: the SDK waits and retries automatically on rate limits (429) and server errors
        _client = Groq(api_key=config.GROQ_API_KEY, max_retries=5)
    return _client


def chat(system: str, user: str, temperature: float = 0.0, json_mode: bool = False,
         reasoning: str = "low") -> str:
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    if config.GROQ_MODEL.startswith("openai/gpt-oss"):
        # reasoning model: short thinking by default (fast, inside the free token budget);
        # the verifier asks for "medium" because judging an answer is the harder task
        kwargs["extra_body"] = {"reasoning_effort": reasoning}
    resp = _get_client().chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        **kwargs,
    )
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a reply that may contain extra text or code fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Model did not return JSON: {text[:200]}")
    return json.loads(match.group(0))


def chat_json(system: str, user: str, reasoning: str = "low") -> dict:
    """Ask for a JSON object back. Falls back to plain mode if strict JSON mode fails."""
    try:
        return _extract_json(chat(system, user, json_mode=True, reasoning=reasoning))
    except Exception as first_error:
        if "401" in str(first_error) or "404" in str(first_error):
            raise  # bad key / bad model: retrying won't help
        return _extract_json(chat(system, user + "\n\nReturn ONLY the JSON object.", json_mode=False,
                                  reasoning=reasoning))
