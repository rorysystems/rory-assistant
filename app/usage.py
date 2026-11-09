"""Uso do Claude: limites do plano (sessão de 5 h, semana) e tokens com custo estimado.

1. Limites do plano: o mesmo endpoint que o `/usage` do Claude Code consulta
   (`api.anthropic.com/api/oauth/usage`), com o token de login do próprio usuário,
   lido de `~/.claude/.credentials.json`. Não é API documentada: se mudar ou falhar,
   a tela mostra o erro e o resto continua. O token NUNCA é renovado aqui (renovar
   rotaciona o refresh token e poderia deslogar o Claude Code); vencido, o Claude Code
   renova sozinho na próxima mensagem. No macOS o token fica no Keychain: sem limites lá.

2. Tokens e custo: soma o `usage` de cada resposta registrada nos transcripts locais
   (`~/.claude/projects/**/*.jsonl`) e multiplica pela tabela de preços da API. É o que
   custaria pagando por token; na assinatura não é cobrado assim.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# US$ por milhão de tokens: (entrada, saída, leitura de cache). Escrita de cache:
# 1,25x a entrada (5 min) ou 2x (1 h). A ordem importa: o primeiro prefixo que bater vale.
PRICES: list[tuple[str, tuple[float, float, float]]] = [
    ("claude-fable-5-1", (10.0, 50.0, 0.25)),
    ("claude-mythos-5-1", (10.0, 50.0, 0.25)),
    ("claude-fable-5", (10.0, 50.0, 1.0)),
    ("claude-mythos", (10.0, 50.0, 1.0)),
    ("claude-opus-5-5", (4.0, 20.0, 0.20)),
    ("claude-opus-5", (5.0, 25.0, 0.50)),
    ("claude-opus-4-8", (5.0, 25.0, 0.50)),
    ("claude-opus-4-7", (5.0, 25.0, 0.50)),
    ("claude-opus-4-6", (5.0, 25.0, 0.50)),
    ("claude-opus-4-5", (5.0, 25.0, 0.50)),
    ("claude-opus-4", (15.0, 75.0, 1.50)),
    ("claude-sonnet-5-5", (2.0, 10.0, 0.20)),
    ("claude-sonnet-5", (2.0, 10.0, 0.20)),
    ("claude-sonnet-4", (3.0, 15.0, 0.30)),
    ("claude-3-7-sonnet", (3.0, 15.0, 0.30)),
    ("claude-haiku-5-5", (0.10, 0.50, 0.01)),
    ("claude-haiku-4-5", (1.0, 5.0, 0.10)),
    ("claude-3-5-haiku", (0.80, 4.0, 0.08)),
]


def price_for(model: str) -> tuple[float, float, float] | None:
    model = (model or "").lower()
    for prefix, price in PRICES:
        if model.startswith(prefix):
            return price
    return None


# --- limites do plano ---------------------------------------------------------------
def _access_token() -> tuple[str | None, str | None]:
    path = CLAUDE_DIR / ".credentials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None, "Sem login por assinatura neste computador (ou o token fica no Keychain do macOS)."
    token = data.get("accessToken")
    expires = data.get("expiresAt")
    if expires and expires / 1000 < datetime.now().timestamp():
        return None, "O token de login venceu. Mande uma mensagem em qualquer conversa (o Claude Code renova) e atualize."
    return token, None if token else "Token de login não encontrado."


def plan_usage() -> dict[str, Any]:
    token, problem = _access_token()
    if not token:
        return {"ok": False, "error": problem}
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "rory-assistant",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return {"ok": False, "error": "Login recusado (token vencido?). Mande uma mensagem e atualize."}
        return {"ok": False, "error": f"O servidor respondeu {error.code}."}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"Sem conexão com a Anthropic: {error}"}

    def window(key: str, label: str) -> dict[str, Any] | None:
        w = raw.get(key)
        if not isinstance(w, dict) or w.get("utilization") is None:
            return None
        return {"key": key, "label": label, "percent": w.get("utilization"), "resets_at": w.get("resets_at")}

    windows = [
        w
        for w in (
            window("five_hour", "Sessão atual (5 h)"),
            window("seven_day", "Semana (todos os modelos)"),
            window("seven_day_opus", "Semana (Opus)"),
            window("seven_day_sonnet", "Semana (Sonnet)"),
        )
        if w
    ]
    extra = raw.get("extra_usage") or {}
    breakdown = (raw.get("seven_day_breakdown") or {}).get("rows") or []
    return {
        "ok": True,
        "windows": windows,
        "extra": {
            "enabled": bool(extra.get("is_enabled")),
            "used": extra.get("used_credits"),
            "limit": extra.get("monthly_limit"),
            "currency": extra.get("currency"),
            "decimals": extra.get("decimal_places", 2),
        }
        if extra
        else None,
        "breakdown": [
            {"name": r.get("display_name"), "percent": r.get("percent")} for r in breakdown if r.get("percent")
        ],
    }


# --- tokens e custo pelos transcripts locais --------------------------------------------
_cache_lock = threading.Lock()
Row = tuple[datetime, str, int, int, int, int, int]


class _FileState:
    """Leitura incremental de um transcript: guarda até onde já leu (offset), as linhas
    de uso extraídas e as respostas já contadas. Arquivo que encolheu recomeça do zero."""

    __slots__ = ("offset", "rows", "seen")

    def __init__(self) -> None:
        self.offset = 0
        self.rows: list[Row] = []
        self.seen: set[str] = set()


_file_cache: dict[str, _FileState] = {}


def _parse_line(line: str, state: _FileState) -> None:
    if '"usage"' not in line or '"assistant"' not in line:
        return
    try:
        entry = json.loads(line)
    except ValueError:
        return
    if entry.get("type") != "assistant":
        return
    message = entry.get("message") or {}
    usage = message.get("usage") or {}
    if not usage:
        return
    # cada bloco da mesma resposta repete o usage: conta uma vez por resposta
    key = f"{message.get('id')}:{entry.get('requestId')}"
    if key in state.seen:
        return
    try:
        stamp = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return
    state.seen.add(key)
    creation = usage.get("cache_creation") or {}
    write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    write_total = int(usage.get("cache_creation_input_tokens") or 0)
    state.rows.append(
        (
            stamp,
            message.get("model") or "",
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            max(write_total - write_1h, 0),
            write_1h,
            int(usage.get("cache_read_input_tokens") or 0),
        )
    )


def _rows_for(path: Path) -> list[Row]:
    """Linhas de uso de um transcript, lendo só o que foi acrescentado desde a última vez
    (os transcripts crescem só por append; com imagens chegam a dezenas de MB)."""
    size = path.stat().st_size
    key = str(path)
    with _cache_lock:
        state = _file_cache.get(key)
        if state is None or size < state.offset:  # novo ou truncado/reescrito
            state = _file_cache[key] = _FileState()
        if size > state.offset:
            with path.open("rb") as handle:
                handle.seek(state.offset)
                chunk = handle.read(size - state.offset)
            end = chunk.rfind(b"\n") + 1  # só linhas completas; o resto fica para a próxima
            for raw in chunk[:end].split(b"\n"):
                if raw:
                    _parse_line(raw.decode("utf-8", errors="replace"), state)
            state.offset += end
        return state.rows


def _all_rows(since: datetime) -> list[Row]:
    root = CLAUDE_DIR / "projects"
    rows: list[Row] = []
    if not root.exists():
        return rows
    limit = since.timestamp()
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < limit:
                continue  # não mexido no período: não tem nada novo
            rows.extend(r for r in _rows_for(path) if r[0] >= since)
        except OSError:
            continue
    return rows


def conversation_usage(session_id: str | None) -> dict[str, Any]:
    """Tokens de uma conversa: total da sessão (inclui subagentes) e o contexto atual,
    que é o tamanho da última requisição (entrada + cache) da conversa principal."""
    if not session_id:
        return {"ok": False, "error": "conversa ainda sem sessão"}
    root = CLAUDE_DIR / "projects"
    main = next(iter(root.glob(f"*/{session_id}.jsonl")), None)
    if main is None:
        return {"ok": False, "error": "sessão não encontrada"}
    rows = list(_rows_for(main))
    main_rows = list(rows)
    extra_dir = main.with_suffix("")  # <sessão>/subagents/*.jsonl
    if extra_dir.is_dir():
        for sub in extra_dir.rglob("*.jsonl"):
            try:
                rows.extend(_rows_for(sub))
            except OSError:
                continue
    summary = _summarize(rows)
    last = max(main_rows, key=lambda r: r[0]) if main_rows else None
    context = (last[2] + last[4] + last[5] + last[6]) if last else 0
    return {"ok": True, **summary, "context": context, "model": last[1] if last else None}


def _summarize(rows: list[tuple[datetime, str, int, int, int, int, int]]) -> dict[str, Any]:
    total = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "cost": 0.0, "unpriced": 0, "messages": 0}
    models: dict[str, float] = {}
    for _, model, inp, out, write5, write1h, read in rows:
        total["messages"] += 1
        total["input"] += inp
        total["output"] += out
        total["cache_write"] += write5 + write1h
        total["cache_read"] += read
        price = price_for(model)
        if price is None:
            total["unpriced"] += 1
            continue
        p_in, p_out, p_read = price
        cost = (inp * p_in + out * p_out + write5 * p_in * 1.25 + write1h * p_in * 2 + read * p_read) / 1_000_000
        total["cost"] += cost
        name = model.removeprefix("claude-")
        models[name] = models.get(name, 0.0) + cost
    total["cost"] = round(total["cost"], 4)
    total["models"] = sorted(({"model": m, "cost": round(c, 4)} for m, c in models.items()), key=lambda x: -x["cost"])
    return total


def token_usage(session_start: datetime | None = None, week_start: datetime | None = None) -> dict[str, Any]:
    """Tokens e custo estimado: sessão de 5 h, semana e mês corrente.
    Sem as janelas do plano, usa as últimas 5 h e os últimos 7 dias."""
    now = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    session_start = session_start or now - timedelta(hours=5)
    week_start = week_start or now - timedelta(days=7)
    oldest = min(session_start, week_start, month_start)
    rows = _all_rows(oldest)
    return {
        "session": _summarize([r for r in rows if r[0] >= session_start]),
        "week": _summarize([r for r in rows if r[0] >= week_start]),
        "month": _summarize([r for r in rows if r[0] >= month_start]),
        "month_label": month_start.strftime("%m/%Y"),
    }


def usage_report() -> dict[str, Any]:
    plan = plan_usage()
    session_start = week_start = None
    for w in plan.get("windows") or []:
        try:
            resets = datetime.fromisoformat(w["resets_at"])
        except (TypeError, ValueError):
            continue
        if w["key"] == "five_hour":
            session_start = resets - timedelta(hours=5)
        elif w["key"] == "seven_day":
            week_start = resets - timedelta(days=7)
    try:
        tokens = {"ok": True, **token_usage(session_start, week_start)}
    except Exception as error:  # noqa: BLE001
        tokens = {"ok": False, "error": str(error)}
    return {"plan": plan, "tokens": tokens, "checked_at": datetime.now().isoformat(timespec="seconds")}
