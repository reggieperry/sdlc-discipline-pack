#!/usr/bin/env bash
# sdlc-demo.tmux <story_id> — set up a four-pane tmux layout for demoing the
# SDLC chain. Each pane shows a different facet of the run.
#
# Layout:
#   ┌───────────────────────────┬─────────────────────────────┐
#   │ events stream             │  bead metadata (--watch)    │
#   ├───────────────────────────┼─────────────────────────────┤
#   │ session list (--watch)    │  artifacts + git commits    │
#   └───────────────────────────┴─────────────────────────────┘
#
# Required env vars (set per-host so the script never falls back to
# someone else's paths):
#   SDLC_DEMO_CITY — absolute path to the Gas City workspace
#   SDLC_DEMO_RIG  — absolute path to the rig dir inside the workspace
#   SDLC_DEMO_PACK — absolute path to the sdlc-discipline pack
#                    (defaults to $SDLC_DEMO_RIG/packs/sdlc-discipline)

set -e

STORY_ID="${1:-?}"
SESSION="sdlc-demo"
CITY="${SDLC_DEMO_CITY:?SDLC_DEMO_CITY is required — absolute path to the Gas City workspace}"
RIG="${SDLC_DEMO_RIG:?SDLC_DEMO_RIG is required — absolute path to the rig dir inside the workspace}"
PACK="${SDLC_DEMO_PACK:-$RIG/packs/sdlc-discipline}"

# Kill any prior demo session so we always start clean.
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n watch -x 220 -y 60

# Top-left: live event stream.
tmux send-keys -t "$SESSION:watch" \
  "cd '$CITY' && gc events --follow --since 1m" C-m

# Split right: bead metadata, refresh every 2s.
tmux split-window -h -t "$SESSION:watch"
tmux send-keys -t "$SESSION:watch" \
  "cd '$RIG' && watch -n 2 \"bd show $STORY_ID --json | jq '.[0] | {status, assignee, metadata}'\"" C-m

# Split top-left down: session list + watch script.
tmux select-pane -t "$SESSION:watch.0"
tmux split-window -v -t "$SESSION:watch"
tmux send-keys -t "$SESSION:watch" \
  "cd '$RIG' && bash '$PACK/scripts/sdlc-watch.sh' $STORY_ID" C-m

# Split bottom-right: artifacts + commits.
tmux select-pane -t "$SESSION:watch.2"
tmux split-window -v -t "$SESSION:watch"
tmux send-keys -t "$SESSION:watch" \
  "cd '$RIG' && watch -n 2 'echo === artifacts ===; ls plans/ reviews/ docs/features/ 2>/dev/null; echo; echo === commits ===; git log --oneline main..HEAD 2>/dev/null | head -8'" C-m

tmux select-pane -t "$SESSION:watch.0"
tmux attach -t "$SESSION"
