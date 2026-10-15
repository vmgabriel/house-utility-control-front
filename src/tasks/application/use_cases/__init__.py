"""Use cases for the tasks context.

Each module holds one use case and does the same three things: validate the
input, call the port, return the domain object. Presentation happens later, in
`src/tasks/interfaces/viewmodels.py`, so nothing here knows a template exists.
"""

from src.tasks.application.use_cases.create_task import CreateTaskUseCase
from src.tasks.application.use_cases.daily_plans import (
    AddTaskToTodayPlanUseCase,
    GenerateDailyPlanUseCase,
)
from src.tasks.application.use_cases.delete_task import DeleteTaskUseCase
from src.tasks.application.use_cases.get_backlog import GetBacklogUseCase
from src.tasks.application.use_cases.get_completed_tasks import GetCompletedTasksUseCase
from src.tasks.application.use_cases.get_daily_plan import GetDailyPlanUseCase
from src.tasks.application.use_cases.get_goal_detail import GetGoalDetailUseCase
from src.tasks.application.use_cases.get_goals import GetGoalsUseCase
from src.tasks.application.use_cases.get_llm_health import GetLlmHealthUseCase
from src.tasks.application.use_cases.move_task import MoveTaskUseCase
from src.tasks.application.use_cases.trigger_llm import (
    TriggerLlmDecompositionUseCase,
    TriggerLlmEvaluationUseCase,
)
from src.tasks.application.use_cases.update_task import UpdateTaskUseCase

__all__ = [
    "AddTaskToTodayPlanUseCase",
    "CreateTaskUseCase",
    "DeleteTaskUseCase",
    "GenerateDailyPlanUseCase",
    "GetBacklogUseCase",
    "GetCompletedTasksUseCase",
    "GetDailyPlanUseCase",
    "GetGoalDetailUseCase",
    "GetGoalsUseCase",
    "GetLlmHealthUseCase",
    "MoveTaskUseCase",
    "TriggerLlmDecompositionUseCase",
    "TriggerLlmEvaluationUseCase",
    "UpdateTaskUseCase",
]
