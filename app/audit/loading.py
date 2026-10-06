from __future__ import annotations

import re


def parse_raw_hex(raw_hex: str) -> list[int]:
    payload = raw_hex.strip()
    if not payload:
        raise ValueError("empty raw_hex")
    if any(separator in payload for separator in [";", " ", ",", "\t"]):
        tokens = [token for token in re.split(r"[\s;,]+", payload) if token]
        if not tokens:
            raise ValueError("empty tokenized raw_hex")
        bytes_out: list[int] = []
        for index, token in enumerate(tokens):
            token = token.removeprefix("0x").removeprefix("0X")
            if not re.fullmatch(r"[0-9A-Fa-f]{1,2}", token):
                raise ValueError(f"token {index} is not 1-2 digit hex: {token!r}")
            bytes_out.append(int(token, 16))
        return bytes_out
    compact = payload.removeprefix("0x").removeprefix("0X")
    if not re.fullmatch(r"[0-9A-Fa-f]+", compact):
        raise ValueError("raw_hex contains non-hex characters")
    if len(compact) % 2:
        raise ValueError("raw_hex has odd number of hex characters")
    return [int(compact[index : index + 2], 16) for index in range(0, len(compact), 2)]

