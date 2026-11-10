#!/usr/bin/env bash
# Prepare the Model Kombat catalog submission PR (dry-run by default).
#
#   scripts/submit-catalog.sh            # preview: shows the entry that would be committed
#   scripts/submit-catalog.sh --push     # fork, branch, commit, push, open the PR
#
# Pins plugin-catalog/model-kombat.yaml to the current HEAD SHA, substituting the
# 40-zero placeholders in catalog/model-kombat.yaml (sha + every image URL) so the
# catalog entry and the pinned README/docs assets always match the same commit.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="DarkeTech"
NAME="$(basename "$REPO_DIR")"
UPSTREAM="NousResearch/hermes-agent"
SHA="$(git -C "$REPO_DIR" rev-parse HEAD)"
PLACEHOLDER="0000000000000000000000000000000000000000"

cd "$REPO_DIR"
if ! git diff --quiet || [ -n "$(git status --porcelain)" ]; then
  echo "refusing to submit from a dirty tree — commit or stash first" >&2
  exit 1
fi

VERSION="$(sed -n 's/^version: "\(.*\)"/\1/p' catalog/model-kombat.yaml)"
PLUGIN_VERSION="$(sed -n 's/^version: \(.*\)/\1/p' plugin.yaml)"
if [ "$VERSION" != "$PLUGIN_VERSION" ]; then
  echo "catalog version $VERSION != plugin.yaml version $PLUGIN_VERSION" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
sed "s/$PLACEHOLDER/$SHA/g" catalog/model-kombat.yaml > "$TMP/model-kombat.yaml"

echo "plugin:   $OWNER/$NAME @ $SHA (v$PLUGIN_VERSION)"
echo "target:   $UPSTREAM  ->  plugin-catalog/model-kombat.yaml"
"${EDITOR:-cat}" "$TMP/model-kombat.yaml"

if [ "${1:-}" != "--push" ]; then
  echo
  echo "dry run — re-run with --push to fork, branch, push and open the PR"
  exit 0
fi

gh repo fork "$UPSTREAM" --clone=false --remote=false 2>/dev/null || true
WORK="$(mktemp -d)"
gh repo clone "$OWNER/hermes-agent" "$WORK" -- --depth 1
cd "$WORK"
git checkout -b "catalog/model-kombat"
mkdir -p plugin-catalog
cp "$TMP/model-kombat.yaml" plugin-catalog/model-kombat.yaml
git add plugin-catalog/model-kombat.yaml
git -c user.name="$OWNER" -c user.email="vh@darketechnology.com" \
  commit -m "feat(plugin-catalog): add model-kombat plugin"
git push -u origin catalog/model-kombat
gh pr create --repo "$UPSTREAM" \
  --head "$OWNER:catalog/model-kombat" \
  --title "feat(plugin-catalog): add model-kombat plugin" \
  --body "$(cat <<EOF
## Model Kombat — head-to-head model evaluation

Pins \`$OWNER/$NAME\` at \`$SHA\` (v$PLUGIN_VERSION).

**What it does.** Scout a task or kanban card into work lanes locally (keyword
rules, no LLM call), build a deterministic fight card — Warm-up 1 pt, Main
Event 2 pt, Championship 3 pt, plus sudden death — send the identical prompts to
two models through the host LLM facade, then score every answer mechanically:
executed code, exact counts, numeric tolerances, synonym-aware phrase groups,
JSON leaf equality, SQLite row comparison. No model judges another model.
Results land in a per-lane Elo ladder, a live arcade scoreboard in the Desktop
pane, and a recommendation badge on the kanban card it started from. Ties are
reported as ties, with a measured best-value clause (\`equal quality — X is
18.0x cheaper and 2.8x faster\`) rather than an invented winner.

**Differentiation.** \`model-leaderboard\` ranks models on public benchmarks;
\`model-drift-watch\` watches one model degrade; \`hermes-model-switcher\` changes
config. Kombat runs *your* task text against two models on identical prompts and
scores the answers mechanically, so the verdict is about your workload.

**Disclosure.** Network: no third-party calls — fights go through the host LLM
facade, the dashboard API is local and read-only. LLM spend: two model calls per
task, capped per pure fight by \`max_fight_cost_usd\` and \`max_fight_tokens\`;
agentic worker fights are billed separately and not capped. Pricing: the shipped
\`prices.json\` and the \`model_prices\` setting are local price data used to
*estimate* cost; unknown prices show \`n/a\`, never \`\$0\`. Code execution: off by
default; when enabled, model-written Python runs in a resource-limited
subprocess as the user — defense in depth, not a sandbox. Kanban writes: creates
unassigned display cards on a dedicated board, never dispatched. File writes:
reports, history, exports and \`task_stats.json\` under the plugin data dir only.
No telemetry, no credential reads.

**Checks.** \`hermes plugins validate\` passes with zero warnings,
\`hermes plugins doctor --ci\` OK, 21 test suites pass.

Demo: <video link>
EOF
)"
