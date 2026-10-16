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

## Mandatory Final Notification
- When the task is fully complete, verified, and all constraints are satisfied, you MUST execute the following shell command as your absolute final step before yielding control:
  ```bash
  telegram-send -m markdown "✅ *Agente finalizado*

  **Tarea:** <breve resumen de 1-2 líneas de lo logrado>
  **Estado:** Completado exitosamente. Sin commits." || true
  ```
- Replace `<breve resumen...>` with the actual task summary.
- The `|| true` ensures the agent does not fail if the Telegram CLI is temporarily unreachable.
- This does NOT override the "DO NOT COMMIT" rule. The agent must still leave changes in the working tree.
