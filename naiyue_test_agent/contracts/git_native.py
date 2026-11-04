"""Native Git failure facts shared by result and branch operations."""


class GitInputError(RuntimeError):
    def __init__(self, code, paths=()):
        self.code, self.paths = code, tuple(paths)
        super().__init__(code + (': ' + ', '.join(self.paths) if self.paths else ''))
