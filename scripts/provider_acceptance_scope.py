"""Deterministic fixture boundary before formal permissions or host execution."""

from pathlib import Path

from langgraph.errors import GraphBubbleUp


class FixtureBoundaryStop(GraphBubbleUp):
    """Probe control failure, never a tool result, approval or model retry."""


class FixturePermission:
    def __init__(self, name, original, root):
        self.name, self.original, self.root = name, original, Path(root).resolve()

    def __getattr__(self, name):
        return getattr(self.original, name)

    def prepare(self, args, context):
        if self.name == 'bash' and args.get('command') != 'printf AUTO_OK':
            raise FixtureBoundaryStop('acceptance_command_outside_fixture')
        path_field = getattr(self.original, 'path_field', None)
        if path_field is not None:
            path = Path(args[path_field])
            requested = path if path.is_absolute() else Path(context.scope.identity.execution_path) / path
            if not requested.resolve().is_relative_to(self.root):
                raise FixtureBoundaryStop('acceptance_target_outside_fixture')
        subject = self.original.prepare(args, context)
        for key in ('requested', 'resolved'):
            if key in subject and not Path(subject[key]).resolve().is_relative_to(self.root):
                raise FixtureBoundaryStop('acceptance_target_outside_fixture')
        return subject

    def default_decision(self, subject):
        return self.original.default_decision(subject)
