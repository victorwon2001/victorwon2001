#!/usr/bin/env python3
"""Synchronize public upstream pull-request activity with README.md."""

from __future__ import annotations

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


API_ROOT = "https://api.github.com"
README = Path(__file__).resolve().parents[1] / "README.md"


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


def date_of(value: str | None) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()


def recent_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_No merged pull requests yet._"

    lines: list[str] = []
    for item in items:
        repo = repo_name(item)
        number = str(item["html_url"]).rstrip("/").split("/")[-1]
        merged_at = date_of(item.get("pull_request", {}).get("merged_at"))
        lines.append(
            f"- [`{repo}`](https://github.com/{repo}) · "
            f"[`#{number}`]({item['html_url']}) "
            f"<sub>· {merged_at}</sub>"
        )
    return "\n".join(lines)


def replace_section(source: str, name: str, body: str) -> str:
    start = f"<!-- {name}:START -->"
    end = f"<!-- {name}:END -->"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    updated, replacements = pattern.subn(f"{start}\n{body}\n{end}", source, count=1)
    if replacements != 1:
        raise RuntimeError(f"Could not find exactly one {name} section")
    return updated


def synchronize(user: str) -> bool:
    total, all_items = all_upstream_prs(user)
    merged = search(
        f"is:pr author:{user} is:merged -user:{user} sort:updated-desc",
        per_page=4,
    )
    open_prs = search(f"is:pr author:{user} is:open -user:{user}", per_page=1)

    signal = f"""<table>
  <tr>
    <td align="center"><strong>{total}</strong><br /><sub>PRs</sub></td>
    <td align="center"><strong>{int(merged['total_count'])}</strong><br /><sub>MERGED</sub></td>
    <td align="center"><strong>{int(open_prs['total_count'])}</strong><br /><sub>OPEN</sub></td>
    <td align="center"><strong>{len({repo_name(item) for item in all_items})}</strong><br /><sub>REPOSITORIES</sub></td>
  </tr>
</table>"""

    current = README.read_text(encoding="utf-8")
    updated = replace_section(current, "UPSTREAM_SIGNAL", signal)
    updated = replace_section(
        updated,
        "RECENTLY_MERGED",
        recent_lines(list(merged.get("items", []))),
    )
    if updated == current:
        return False
    README.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    changed = synchronize(username())
    print("Signal synchronized." if changed else "Signal already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
