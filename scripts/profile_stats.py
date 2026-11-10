#!/usr/bin/env python3
"""Write profile/numbers.json and profile/languages.svg for the Dhiva-Labs org profile.

Counts public, non-fork repositories only. Needs GH_TOKEN in the environment.
"""
import datetime
import html
import json
import os
import time
import urllib.error
import urllib.request

ORG = "Dhiva-Labs"
FOUNDER = "dhivadhiva"
OUT = os.path.join(os.path.dirname(__file__), "..", "profile")

REPOS_QUERY = """
query($org: String!, $cursor: String) {
  organization(login: $org) {
    repositories(first: 20, after: $cursor, privacy: PUBLIC, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        defaultBranchRef { target { ... on Commit { history { totalCount } } } }
        languages(first: 30, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
        releases(first: 100) {
          totalCount
          nodes { releaseAssets(first: 50) { nodes { downloadCount } } }
        }
      }
    }
  }
}
"""


def gql(query, **variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {os.environ['GH_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    # GitHub stops a GraphQL query after 10 seconds and answers 502. A page
    # of 50 repositories with their releases took longer than that, so
    # REPOS_QUERY asks for 20 at a time; retry the occasional 5xx that is
    # left with a backoff.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
            break
        except urllib.error.HTTPError as err:
            if err.code < 500 or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(5 * 3**attempt)
    if body.get("errors"):
        raise SystemExit(json.dumps(body["errors"], indent=2))
    return body["data"]


def search_count(q):
    data = gql("query($q: String!) { search(query: $q, type: ISSUE, first: 1) { issueCount } }", q=q)
    return data["search"]["issueCount"]


def fetch_repos():
    repos, cursor = [], None
    while True:
        page = gql(REPOS_QUERY, org=ORG, cursor=cursor)["organization"]["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return repos
        cursor = page["pageInfo"]["endCursor"]


def languages_svg(langs, repo_count):
    total = sum(size for size, _ in langs.values())
    ranked = sorted(langs.items(), key=lambda kv: kv[1][0], reverse=True)
    top, rest = ranked[:11], ranked[11:]
    if rest:
        top.append(("Other", (sum(size for _, (size, _) in rest), "#565f89")))

    width, pad, bar_w, cols, row_h = 800, 25, 750, 4, 26
    rows = -(-len(top) // cols)
    height = 104 + rows * row_h
    mb = total / 1_000_000

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
        '<title id="title">Languages across Dhiva Labs</title>',
        "<style>"
        ".h{font:600 18px 'Segoe UI',Ubuntu,'Helvetica Neue',Sans-Serif;fill:#70a5fd}"
        ".s{font:400 12px 'Segoe UI',Ubuntu,Sans-Serif;fill:#a9b1d6}"
        ".n{font:600 13px 'Segoe UI',Ubuntu,Sans-Serif;fill:#c0caf5}"
        ".p{font:400 12px 'Segoe UI',Ubuntu,Sans-Serif;fill:#7aa2f7}"
        "</style>",
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6" fill="#1a1b27"/>',
        f'<text x="{pad}" y="38" class="h">Languages across Dhiva Labs</text>',
        f'<text x="{width - pad}" y="38" class="s" text-anchor="end">'
        f"{repo_count} public repos · {mb:,.1f} MB of code</text>",
        f'<clipPath id="bar"><rect x="{pad}" y="56" width="{bar_w}" height="12" rx="6"/></clipPath>',
        '<g clip-path="url(#bar)">',
    ]
    x = float(pad)
    for name, (size, color) in top:
        w = max(bar_w * size / total, 2)
        parts.append(f'<rect x="{x:.2f}" y="56" width="{w:.2f}" height="12" fill="{color}"/>')
        x += w
    parts.append("</g>")

    col_w = bar_w / cols
    for i, (name, (size, color)) in enumerate(top):
        cx = pad + (i % cols) * col_w
        cy = 98 + (i // cols) * row_h
        parts += [
            f'<circle cx="{cx + 6:.1f}" cy="{cy - 4}" r="5" fill="{color}"/>',
            f'<text x="{cx + 18:.1f}" y="{cy}" class="n">{html.escape(name)}</text>',
            f'<text x="{cx + col_w - 14:.1f}" y="{cy}" class="p" text-anchor="end">'
            f"{100 * size / total:.1f}%</text>",
        ]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    repos = fetch_repos()
    public_total = gql(
        "query($org: String!) { organization(login: $org) { repositories(privacy: PUBLIC) { totalCount } } }",
        org=ORG,
    )["organization"]["repositories"]["totalCount"]

    langs = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            node = edge["node"]
            entry = langs.setdefault(node["name"], [0, node["color"] or "#8b949e"])
            entry[0] += edge["size"]

    commits = sum(
        (r["defaultBranchRef"] or {}).get("target", {}).get("history", {}).get("totalCount", 0)
        for r in repos
    )
    downloads = sum(
        asset["downloadCount"]
        for r in repos
        for release in r["releases"]["nodes"]
        for asset in release["releaseAssets"]["nodes"]
    )

    numbers = {
        "repos": public_total,
        "commits": commits,
        "prs_merged": search_count(f"org:{ORG} is:public is:pr is:merged"),
        "issues_closed": search_count(f"org:{ORG} is:public is:issue is:closed"),
        "releases": sum(r["releases"]["totalCount"] for r in repos),
        "downloads": downloads,
        "stars": sum(r["stargazerCount"] for r in repos),
        "languages": len(langs),
        "upstream": search_count(f"author:{FOUNDER} is:pr is:merged -org:{ORG} -user:{FOUNDER}"),
    }
    numbers = {k: f"{v:,}" for k, v in numbers.items()}
    numbers["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "numbers.json"), "w") as f:
        json.dump(numbers, f, indent=2)
        f.write("\n")
    with open(os.path.join(OUT, "languages.svg"), "w") as f:
        f.write(languages_svg({k: tuple(v) for k, v in langs.items()}, public_total))
    print(json.dumps(numbers, indent=2))


if __name__ == "__main__":
    main()
