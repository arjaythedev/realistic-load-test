---
tags: [realistic-load-test]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

We launch Launchpad on Product Hunt on Tuesday. It's a Next.js app on Vercel with Neon Postgres, no real traffic yet so no logs. Will it survive 3,000 people at once? A local copy of the app is in app/: run `python3 app/server.py 8803` and use http://127.0.0.1:8803 as the stand-in for the deployment.

Input files: app/
