import json
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["AIAND_API_KEY"], base_url=os.environ["AIAND_BASE_URL"])
    return _client


def chat(system: str, user: str, temperature: float = 0.0) -> str:
    r = client().chat.completions.create(
        model=os.environ["AIAND_MODEL"],
        temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return r.choices[0].message.content or ""


def chat_with_tools(messages: list, tools: list):
    r = client().chat.completions.create(
        model=os.environ["AIAND_MODEL"], temperature=0, messages=messages, tools=tools
    )
    return r.choices[0].message


def chat_final(messages: list) -> str:
    r = client().chat.completions.create(model=os.environ["AIAND_MODEL"], temperature=0, messages=messages)
    return r.choices[0].message.content or ""


def chat_json(system: str, user: str) -> dict:
    """Open models often wrap JSON in prose or code fences; extract the first object."""
    text = chat(system + "\nReply with a single JSON object only.", user)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"No JSON in model reply: {text[:200]}")
    return json.loads(m.group(0))
