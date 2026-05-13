class SchedulingException(Exception):       
    pass

class CalendarAccessError(SchedulingException):
    def __init__(self, message: str = "Calendar access denied"):
        self.message = message
        super().__init__(self.message)

class NoAvailableSlotsError(SchedulingException):
    def __init__(self, message: str = "No available slots found"):
        self.message = message
        super().__init__(self.message)

class ConflictError(SchedulingException):
    def __init__(self, message: str = "Meeting time conflicts with existing event"):
        self.message = message
        super().__init__(self.message)

class InvalidTimeError(SchedulingException):
    def __init__(self, message: str = "Invalid time specified"):
        self.message = message
        super().__init__(self.message)

class AuthenticationError(SchedulingException):
    def __init__(self, message: str = "Authentication failed"):
        self.message = message
        super().__init__(self.message)

class IntentExtractionError(SchedulingException):
    def __init__(self, message: str = "Could not understand request"):
        self.message = message
        super().__init__(self.message)