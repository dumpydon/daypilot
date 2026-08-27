class DayPilotError(Exception):
    """Base class for expected application failures."""


class RunNotFoundError(DayPilotError):
    pass


class RunConflictError(DayPilotError):
    pass


class ToolUnavailableError(DayPilotError):
    pass


class UnauthorizedToolCallError(DayPilotError):
    pass


class DemoModeRequiredError(DayPilotError):
    pass


class DemoWorkspaceError(DayPilotError):
    pass


class InvalidPlanError(DayPilotError):
    pass


class PlanRevisionError(DayPilotError):
    pass
