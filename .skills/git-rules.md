# Git Rules

## No Automatic Commits
- **NEVER** run `git add`, `git commit`, `git push`, or any git command that modifies history unless explicitly instructed by the developer.
- All changes must remain in the working tree for manual developer review.
- At the end of each task, state clearly: "Ready for manual review. No commits were made."

## Commit Messages (when developer requests)
- Format: `<type>: <description>` (e.g., `feat: add purchases bounded context`).
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `security`.
- Reference checkpoint numbers when applicable (e.g., `(Checkpoint 6)`).

## Working Tree Hygiene
- Do not leave orphaned files. If a refactor moves code, delete the source.
- Run `make lint` and `make test` before declaring a task complete.
