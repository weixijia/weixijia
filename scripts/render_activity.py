"""Render a minimal GitHub activity card (light and dark SVG) for the profile README.

Reads the public contribution calendar (which includes private contribution
counts when the profile shows them) through the GitHub GraphQL API.

Usage: GITHUB_TOKEN=... python render_activity.py <user> <out_dir>
"""

import datetime as dt
import json
import os
import sys
import urllib.request
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"

THEMES = {
    "light": {"ink": "#211d18", "muted": "#6f675c", "rule": "#e4ddd1", "accent": "#8d3b1b"},
    "dark": {"ink": "#e8e2d7", "muted": "#a1988a", "rule": "#302b24", "accent": "#d4906a"},
}
SERIF = "Newsreader, Georgia, 'Times New Roman', serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"


def graphql(query: str, token: str) -> dict:
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def fetch_days(user: str, token: str, today: dt.date) -> dict[dt.date, int]:
    created = graphql(f'{{ user(login: "{user}") {{ createdAt }} }}', token)["user"]["createdAt"]
    days: dict[dt.date, int] = {}
    for year in range(int(created[:4]), today.year + 1):
        end = f"{year}-12-31T23:59:59Z" if year < today.year else f"{today.isoformat()}T23:59:59Z"
        query = f"""{{ user(login: "{user}") {{
            contributionsCollection(from: "{year}-01-01T00:00:00Z", to: "{end}") {{
              contributionCalendar {{ weeks {{ contributionDays {{ date contributionCount }} }} }}
            }} }} }}"""
        calendar = graphql(query, token)["user"]["contributionsCollection"]["contributionCalendar"]
        for week in calendar["weeks"]:
            for day in week["contributionDays"]:
                date = dt.date.fromisoformat(day["date"])
                if date.year == year and date <= today:
                    days[date] = day["contributionCount"]
    return days


def longest_streak(days: dict[dt.date, int]) -> int:
    best = run = 0
    previous = None
    for date in sorted(d for d, count in days.items() if count > 0):
        run = run + 1 if previous is not None and (date - previous).days == 1 else 1
        best = max(best, run)
        previous = date
    return best


def summarise(days: dict[dt.date, int], today: dt.date) -> dict:
    window_start = today - dt.timedelta(days=364)
    window = {d: c for d, c in days.items() if d >= window_start}
    by_year: dict[int, int] = {}
    for date, count in days.items():
        by_year[date.year] = by_year.get(date.year, 0) + count
    first_active = min(year for year, total in by_year.items() if total > 0)
    return {
        "last_year": sum(window.values()),
        "active_days": sum(1 for c in window.values() if c > 0),
        "lifetime": sum(days.values()),
        "since": min(days),
        "streak": longest_streak(days),
        "by_year": {y: by_year.get(y, 0) for y in range(max(first_active, today.year - 7), today.year + 1)},
        "today": today,
    }


def text(x, y, content, *, size, color, family, anchor="start", weight=400, spacing=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}" text-anchor="{anchor}"{extra}>{escape(content)}</text>'
    )


def render(stats: dict, theme: dict) -> str:
    width, pad = 820, 24
    years = stats["by_year"]
    bar_top, row = 196, 22
    height = bar_top + row * len(years) + 20
    label = {"size": 11, "color": theme["muted"], "family": MONO, "spacing": "0.12em"}

    parts = [
        text(pad, 28, "GITHUB ACTIVITY", **label),
        text(width - pad, 28, f"updated {stats['today'].isoformat()}", size=11, color=theme["muted"],
             family=MONO, anchor="end"),
    ]

    years_sorted = sorted(years)
    this_year, last_year = years_sorted[-1], years_sorted[-2]
    growth = years[this_year] / years[last_year] if years[last_year] else 0
    kpis = [
        (f"{stats['last_year']:,}", "contributions, past year"),
        (f"{stats['active_days']}", "active days, past year"),
        (f"{stats['lifetime']:,}", f"contributions since {stats['since'].year}"),
        (f"{growth:.1f}\u00d7", f"{this_year} to date vs {last_year}"),
    ]
    column = (width - 2 * pad) / len(kpis)
    for i, (value, caption) in enumerate(kpis):
        x = pad + i * column
        parts.append(text(x, 82, value, size=34, color=theme["ink"], family=SERIF, weight=500))
        parts.append(text(x, 106, caption, size=11, color=theme["muted"], family=MONO))

    parts.append(f'<line x1="{pad}" y1="136" x2="{width - pad}" y2="136" stroke="{theme["rule"]}" />')
    parts.append(text(pad, 170, "CONTRIBUTIONS BY YEAR", **label))

    label_w, max_bar = 92, width - 2 * pad - 92 - 90
    peak = max(years.values())
    current = stats["today"].year
    for i, (year, total) in enumerate(years.items()):
        y = bar_top + i * row
        is_current = year == current
        name = f"{year} ytd" if is_current else str(year)
        bar = max(2.0, max_bar * total / peak)
        opacity = 1 if is_current else 0.45
        parts.append(text(pad, y + 12, name, size=12, color=theme["muted"], family=MONO))
        parts.append(
            f'<rect x="{pad + label_w}" y="{y + 2}" width="{bar:.1f}" height="12" '
            f'fill="{theme["accent"]}" fill-opacity="{opacity}" />'
        )
        parts.append(
            text(pad + label_w + bar + 8, y + 13, f"{total:,}", size=14, color=theme["ink"], family=SERIF)
        )

    summary = (
        f"{stats['last_year']:,} contributions in the last 12 months, "
        f"{stats['lifetime']:,} since {stats['since'].year}, longest streak {stats['streak']} days."
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">'
        f'<title id="t">GitHub activity</title><desc id="d">{escape(summary)}</desc>'
        + "".join(parts)
        + "</svg>\n"
    )


def main() -> None:
    user, out_dir = sys.argv[1], sys.argv[2]
    token = os.environ["GITHUB_TOKEN"]
    today = dt.datetime.now(dt.timezone.utc).date()
    stats = summarise(fetch_days(user, token, today), today)
    os.makedirs(out_dir, exist_ok=True)
    for name, theme in THEMES.items():
        with open(os.path.join(out_dir, f"activity-{name}.svg"), "w", encoding="utf-8") as handle:
            handle.write(render(stats, theme))
    print(json.dumps({k: v for k, v in stats.items() if k not in {"since", "today"}}, default=str))


if __name__ == "__main__":
    main()
