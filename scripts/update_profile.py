#!/usr/bin/env python3
"""Synchronize public upstream pull-request activity with README.md."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


API_ROOT = "https://api.github.com"
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SIGNAL_SVG = ROOT / "assets" / "signal.svg"
CARD_VERSION = 2


def username() -> str:
    return (
        os.getenv("PROFILE_USERNAME")
        or os.getenv("GITHUB_REPOSITORY_OWNER")
        or "victorwon2001"
    )


def api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{API_ROOT}{path}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "upstream-signal",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {error.code}: {body}") from error


def search(query: str, *, per_page: int = 1, page: int = 1) -> dict[str, Any]:
    return api_get(
        "/search/issues",
        {"q": query, "per_page": per_page, "page": page},
    )


def all_upstream_prs(user: str) -> tuple[int, list[dict[str, Any]]]:
    query = f"is:pr author:{user} -user:{user} sort:created-desc"
    first = search(query, per_page=100)
    total = int(first["total_count"])
    items = list(first.get("items", []))

    page_count = min((total + 99) // 100, 10)
    for page in range(2, page_count + 1):
        items.extend(search(query, per_page=100, page=page).get("items", []))
    return total, items


def repo_name(item: dict[str, Any]) -> str:
    return str(item["repository_url"]).removeprefix(f"{API_ROOT}/repos/")


def merged_at(item: dict[str, Any]) -> datetime | None:
    value = item.get("pull_request", {}).get("merged_at")
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def display_date(item: dict[str, Any]) -> str:
    value = merged_at(item)
    return value.strftime("%d %b %Y").upper() if value else "—"


def replace_section(source: str, name: str, body: str) -> str:
    start = f"<!-- {name}:START -->"
    end = f"<!-- {name}:END -->"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    updated, replacements = pattern.subn(f"{start}\n{body}\n{end}", source, count=1)
    if replacements != 1:
        raise RuntimeError(f"Could not find exactly one {name} section")
    return updated


def recent_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p><sub>NO MERGED PULL REQUESTS</sub></p>"

    rows: list[str] = []
    for item in items:
        repo = html.escape(repo_name(item))
        repo_url = f"https://github.com/{repo}"
        pr_url = html.escape(str(item["html_url"]), quote=True)
        number = html.escape(pr_url.rstrip("/").split("/")[-1])
        rows.append(
            "    <tr>\n"
            f'      <td><a href="{repo_url}"><strong>{repo}</strong></a></td>\n'
            f'      <td align="center"><a href="{pr_url}"><code>#{number}</code></a></td>\n'
            f'      <td align="right"><sub>{display_date(item)}</sub></td>\n'
            "    </tr>"
        )

    return """<table width="100%">
  <thead>
    <tr>
      <th align="left" width="60%"><sub>REPOSITORY</sub></th>
      <th align="center" width="20%"><sub>PULL REQUEST</sub></th>
      <th align="right" width="20%"><sub>MERGED</sub></th>
    </tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>""".format(rows="\n".join(rows))


def visible_state(
    total: int,
    merged_total: int,
    open_total: int,
    repositories: int,
    recent: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": CARD_VERSION,
        "prs": total,
        "merged": merged_total,
        "open": open_total,
        "repositories": repositories,
        "recent": [
            {
                "repo": repo_name(item),
                "url": item["html_url"],
                "merged_at": item.get("pull_request", {}).get("merged_at"),
            }
            for item in recent
        ],
    }


def state_signature(state: dict[str, Any]) -> str:
    serialized = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def previous_card_metadata() -> tuple[str | None, str | None]:
    if not SIGNAL_SVG.exists():
        return None, None
    source = SIGNAL_SVG.read_text(encoding="utf-8")
    signature = re.search(r'data-signature="([0-9a-f]+)"', source)
    updated = re.search(r'data-updated="([^"]+)"', source)
    return (
        signature.group(1) if signature else None,
        updated.group(1) if updated else None,
    )


def render_signal_svg(
    *,
    total: int,
    merged_total: int,
    open_total: int,
    repositories: int,
    signature: str,
    updated_at: str,
) -> str:
    updated = datetime.fromisoformat(updated_at)
    updated_label = updated.strftime("%d %b %Y · %H:%M KST").upper()
    description = (
        f"{total} upstream pull requests, {merged_total} merged, "
        f"{open_total} open, across {repositories} repositories. "
        f"Updated {updated_label}."
    )

    stats = (
        (str(total), "PULL REQUESTS", 52),
        (str(merged_total), "MERGED", 284),
        (str(open_total), "OPEN", 516),
        (str(repositories), "REPOSITORIES", 748),
    )
    stat_groups: list[str] = []
    for value, label, x in stats:
        stat_groups.append(
            f'  <g transform="translate({x} 0)">\n'
            f'    <text class="value" x="0" y="161">{value}</text>\n'
            f'    <text class="label" x="1" y="188">{label}</text>\n'
            "  </g>"
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="260" viewBox="0 0 1000 260" role="img" aria-labelledby="title description" data-signature="{signature}" data-updated="{updated_at}">
  <title id="title">Upstream signal</title>
  <desc id="description">{html.escape(description)}</desc>
  <defs>
    <linearGradient id="surface" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a0c10" />
      <stop offset="0.58" stop-color="#10141b" />
      <stop offset="1" stop-color="#0b0e13" />
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#7c5cff" stop-opacity="0.22" />
      <stop offset="1" stop-color="#7c5cff" stop-opacity="0" />
    </radialGradient>
    <linearGradient id="signal" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#8b5cf6" />
      <stop offset="1" stop-color="#22d3ee" />
    </linearGradient>
    <clipPath id="frame">
      <rect x="1" y="1" width="998" height="258" rx="22" />
    </clipPath>
    <style>
      .value {{ fill: #f4f7fb; font: 650 42px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: -1.5px; }}
      .label {{ fill: #7f8998; font: 600 11px ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: 1.8px; }}
      .micro {{ fill: #828b99; font: 600 11px ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: 1.7px; }}
    </style>
  </defs>

  <rect x="1" y="1" width="998" height="258" rx="22" fill="url(#surface)" stroke="#272d37" />
  <g clip-path="url(#frame)">
    <ellipse cx="820" cy="-32" rx="330" ry="190" fill="url(#glow)" />
    <path d="M694 54h36l10-13 16 29 16-47 17 38 12-18 12 11h135" fill="none" stroke="#39414e" stroke-width="1.2" />
    <path d="M730 54l10-13 16 29 16-47 17 38 12-18 12 11" fill="none" stroke="url(#signal)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
  </g>

  <circle cx="54" cy="49" r="5" fill="#3ddc97" />
  <circle cx="54" cy="49" r="10" fill="#3ddc97" opacity="0.10" />
  <text class="micro" x="72" y="53">UPSTREAM SIGNAL</text>
  <text class="micro" x="948" y="53" text-anchor="end">WINDOW ≤ 5 MIN</text>
  <line x1="52" y1="86" x2="948" y2="86" stroke="#252b34" />

{chr(10).join(stat_groups)}

  <line x1="255" y1="119" x2="255" y2="190" stroke="#242a33" />
  <line x1="487" y1="119" x2="487" y2="190" stroke="#242a33" />
  <line x1="719" y1="119" x2="719" y2="190" stroke="#242a33" />
  <line x1="52" y1="215" x2="948" y2="215" stroke="#252b34" />
  <text class="micro" x="52" y="239">UPDATED · {updated_label}</text>
  <text class="micro" x="948" y="239" text-anchor="end">PUBLIC UPSTREAM</text>
</svg>
"""


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def synchronize(user: str) -> bool:
    total, all_items = all_upstream_prs(user)
    merged = search(
        f"is:pr author:{user} is:merged -user:{user} sort:updated-desc",
        per_page=4,
    )
    open_prs = search(f"is:pr author:{user} is:open -user:{user}", per_page=1)

    recent = list(merged.get("items", []))
    merged_total = int(merged["total_count"])
    open_total = int(open_prs["total_count"])
    repositories = len({repo_name(item) for item in all_items})
    state = visible_state(total, merged_total, open_total, repositories, recent)
    signature = state_signature(state)

    previous_signature, previous_updated_at = previous_card_metadata()
    if previous_signature == signature and previous_updated_at:
        updated_at = previous_updated_at
    else:
        updated_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="minutes")

    signal_svg = render_signal_svg(
        total=total,
        merged_total=merged_total,
        open_total=open_total,
        repositories=repositories,
        signature=signature,
        updated_at=updated_at,
    )

    current = README.read_text(encoding="utf-8")
    card = f"""<p align="center">
  <img src="./assets/signal.svg?v={signature}" width="100%" alt="Current public upstream contribution signal" />
</p>"""
    updated = replace_section(current, "SIGNAL_CARD", card)
    updated = replace_section(updated, "RECENTLY_MERGED", recent_table(recent))

    changed = write_if_changed(SIGNAL_SVG, signal_svg)
    changed = write_if_changed(README, updated) or changed
    return changed


def main() -> int:
    changed = synchronize(username())
    print("Signal synchronized." if changed else "Signal already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
