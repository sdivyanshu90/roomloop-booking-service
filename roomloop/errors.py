class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details if details is not None else []
