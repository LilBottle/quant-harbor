from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AlpacaEnv:
    api_key: str
    secret: str
    endpoint: str


def _parse_env_file(path: Path) -> dict:
    out = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def load_alpaca_env() -> AlpacaEnv:
    """Load Alpaca credentials.

    Priority:
    1) process environment variables: API_KEY, SECRET, ENDPOINT
    2) fallback to reading ~/.alpaca.env (key=value per line)

    NOTE: never print secrets.
    """
    api_key = os.environ.get('API_KEY')
    secret = os.environ.get('SECRET')
    endpoint = os.environ.get('ENDPOINT')

    if not (api_key and secret and endpoint):
        p = Path.home() / '.alpaca.env'
        if p.exists():
            kv = _parse_env_file(p)
            api_key = api_key or kv.get('API_KEY')
            secret = secret or kv.get('SECRET')
            endpoint = endpoint or kv.get('ENDPOINT')

    missing = [k for k, v in [('API_KEY', api_key), ('SECRET', secret), ('ENDPOINT', endpoint)] if not v]
    if missing:
        raise RuntimeError(
            f"Missing Alpaca credentials: {missing}. Please set env vars or create ~/.alpaca.env with API_KEY=..., SECRET=..., ENDPOINT=... (chmod 600)."
        )

    return AlpacaEnv(api_key=api_key, secret=secret, endpoint=endpoint)
