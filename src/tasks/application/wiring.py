"""Composition root for the tasks context.

The single place allowed to construct concrete adapters and inject them into use
cases (AGENTS.md, constraint 3). Views never build a use case; they read the
ready-made ones off `current_app`.

The auto-refresh wrapping lives in `src/interfaces/web/app.py` alongside the
equivalent wrapping for every other context, because `refreshing()` there closes
over the request-scoped cookie manager. This module's job is only to construct
the use cases around an already-wrapped adapter -- and to make it impossible to
wire one to a raw, unrefreshed client.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.shared.domain.ports.clock import Clock
from src.shared.infrastructure.clock import SystemClock
from src.tasks.application.use_cases import (
    AddTaskToTodayPlanUseCase,
    CreateTaskUseCase,
    DeleteTaskUseCase,
    GenerateDailyPlanUseCase,
    GetBacklogUseCase,
    GetCompletedTasksUseCase,
    GetDailyPlanUseCase,
    GetGoalDetailUseCase,
    GetGoalsUseCase,
    GetLlmHealthUseCase,
    MoveTaskUseCase,
    TriggerLlmDecompositionUseCase,
    TriggerLlmEvaluationUseCase,
    UpdateTaskUseCase,
)


@dataclass(frozen=True, slots=True)
class TasksUseCases:
    """Every tasks use case, wired to one shared adapter and clock."""

    get_backlog: GetBacklogUseCase
    create_task: CreateTaskUseCase
    update_task: UpdateTaskUseCase
    delete_task: DeleteTaskUseCase
    get_completed_tasks: GetCompletedTasksUseCase
    get_goals: GetGoalsUseCase
    get_goal_detail: GetGoalDetailUseCase
    get_daily_plan: GetDailyPlanUseCase
    generate_daily_plan: GenerateDailyPlanUseCase
    add_task_to_daily_plan: AddTaskToTodayPlanUseCase
    move_task: MoveTaskUseCase
    get_llm_health: GetLlmHealthUseCase
    trigger_llm_evaluation: TriggerLlmEvaluationUseCase
    trigger_llm_decomposition: TriggerLlmDecompositionUseCase

    #: Attribute name on the Flask app -> attribute name here. Views read
    #: `current_app.get_backlog_use_case`, matching the existing contexts.
    APP_ATTRIBUTES = (
        ("get_backlog_use_case", "get_backlog"),
        ("create_task_use_case", "create_task"),
        ("update_task_use_case", "update_task"),
        ("delete_task_use_case", "delete_task"),
        ("get_completed_tasks_use_case", "get_completed_tasks"),
        ("get_goals_use_case", "get_goals"),
        ("get_goal_detail_use_case", "get_goal_detail"),
        ("get_daily_plan_use_case", "get_daily_plan"),
        ("generate_daily_plan_use_case", "generate_daily_plan"),
        ("add_task_to_daily_plan_use_case", "add_task_to_daily_plan"),
        ("move_task_use_case", "move_task"),
        ("get_llm_health_use_case", "get_llm_health"),
        ("trigger_llm_evaluation_use_case", "trigger_llm_evaluation"),
        ("trigger_llm_decomposition_use_case", "trigger_llm_decomposition"),
    )

    def attach_to(self, target: object) -> None:
        """Expose each use case as an attribute on `target` (the Flask app)."""
        for app_name, field_name in self.APP_ATTRIBUTES:
            setattr(target, app_name, getattr(self, field_name))


def build_tasks_use_cases(
    tasks_client: object, clock: Clock | None = None
) -> TasksUseCases:
    """Build the tasks use cases around an adapter and a clock.

    `clock` defaults to `SystemClock` so the composition root can stay terse,
    while tests can pass a frozen clock without patching anything.

    `tasks_client` is typed loosely because the caller passes the
    ``AutoRefreshingRepository`` proxy, not the adapter itself. Typing it as the
    `TasksApiClient` protocol would be true of the adapter and false of the
    proxy, whose methods are synthesised at runtime by ``__getattr__`` -- and a
    static type that the production wiring does not satisfy is a type that gets
    suppressed rather than believed.
    """
    resolved_clock: Clock = clock if clock is not None else SystemClock()
    return TasksUseCases(
        get_backlog=GetBacklogUseCase(tasks_client=tasks_client),
        create_task=CreateTaskUseCase(tasks_client=tasks_client, clock=resolved_clock),
        update_task=UpdateTaskUseCase(tasks_client=tasks_client),
        delete_task=DeleteTaskUseCase(tasks_client=tasks_client),
        get_completed_tasks=GetCompletedTasksUseCase(tasks_client=tasks_client),
        get_goals=GetGoalsUseCase(tasks_client=tasks_client),
        get_goal_detail=GetGoalDetailUseCase(tasks_client=tasks_client),
        get_daily_plan=GetDailyPlanUseCase(
            tasks_client=tasks_client, clock=resolved_clock
        ),
        generate_daily_plan=GenerateDailyPlanUseCase(tasks_client=tasks_client),
        add_task_to_daily_plan=AddTaskToTodayPlanUseCase(
            tasks_client=tasks_client, clock=resolved_clock
        ),
        move_task=MoveTaskUseCase(tasks_client=tasks_client),
        get_llm_health=GetLlmHealthUseCase(tasks_client=tasks_client),
        trigger_llm_evaluation=TriggerLlmEvaluationUseCase(tasks_client=tasks_client),
        trigger_llm_decomposition=TriggerLlmDecompositionUseCase(
            tasks_client=tasks_client
        ),
    )


__all__ = [
    "TasksUseCases",
    "build_tasks_use_cases",
]
