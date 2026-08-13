class Session:
    """Shared, mutable holder for the post-login access token/role/username.

    Views that need to make authenticated calls (ClientsView, LoansView) are
    constructed once, before login happens, and hold a reference to this
    object rather than the token itself -- MainWindow updates it in place on
    login/logout so every view always sees the current value.
    """

    def __init__(self):
        self.access_token: str | None = None
        self.role: str | None = None
        self.username: str | None = None

    def clear(self) -> None:
        self.access_token = None
        self.role = None
        self.username = None
