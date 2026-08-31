class DayPilotError(Exception):
    """Base class for expected application failures."""


class RunNotFoundError(DayPilotError):
    pass


class RunConflictError(DayPilotError):
    pass


class ToolUnavailableError(DayPilotError):
    pass


class ProviderUnavailableError(ToolUnavailableError):
    """A configured provider cannot currently serve a capability."""

    def __init__(self, message: str, *, requires_reauth: bool = False) -> None:
        super().__init__(message)
        self.requires_reauth = requires_reauth


class OAuthError(DayPilotError):
    pass


class FileAccessError(DayPilotError):
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
