"""A direct argv wrapper; AITest and project Skills own the testing workflow."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
import json
import re

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .permissions import ToolPermission, require_tool_permit, require_process_system
from .registry import ToolSpec
from .schemas import ExecutionTimeout, build_tool_schemas
from .settings import AITestSettings


class AITestPermission(ToolPermission):
    def prepare(self, args, context):
        require_process_system(context).validate()
        subject = super().prepare(args, context)
        subject['target'] = 'aitest ' + json.dumps(args['args'], ensure_ascii=False)
        subject['cli_args'] = args['args']
        return subject

    def default_decision(self, subject):
        args = subject['cli_args']
        # CLI options belong to AITest. Only the command and actual --help flag
        # determine the default; shell tokens are always literal arguments.
        help_call = (args == ['--help'] or len(args) == 2 and args[1] == '--help'
                     and re.fullmatch(r'[a-z][a-z0-9_-]*', args[0]) is not None)
        return 'allow' if args[0] in ('doctor', 'report') or help_call else 'ask'


class _AITestInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    timeout: ExecutionTimeout = None

    @field_validator('args', check_fields=False)
    @classmethod
    def no_nul(cls, values):
        if any('\0' in value for value in values):
            raise ValueError('CLI arguments cannot contain NUL')
        return values


def create_aitest_kit_tool(*, settings=None, process_settings=None, progress_settings=None):
    settings = settings or AITestSettings()
    public, execution = build_tool_schemas('AITestInput', _AITestInput,
        args=(list[str], Field(min_length=1, max_length=settings.max_args)))

    async def execute(args: list[str], runtime: ToolRuntime[RuntimeContext], timeout: int | None = None):
        require_tool_permit(runtime)
        port = require_process_system(runtime.context)
        if sum(len(value) for value in args) > settings.max_argument_chars:
            raise ToolException('aitest_kit arguments exceed the configured character budget.')
        return await port.argv(args, timeout, runtime, settings=settings,
            process_settings=process_settings, progress_settings=progress_settings)

    item = StructuredTool.from_function(coroutine=execute, name='aitest_kit', args_schema=execution,
        description=('Run aitest CLI subcommands using a literal args array, without a shell. '
            'Use ["--help"] or ["<command>", "--help"] to discover CLI usage. '
            'Run ["doctor"] first for testing tasks. doctor, report and help default to Allow; other commands default to Ask. '
            'The session workspace is cwd; --workspace is an ordinary CLI option. '
            'Omit timeout or pass 0 for unlimited execution; positive integer seconds set a deadline. '
            f'Returns merged output tail up to {settings.max_output_lines} lines/{settings.max_output_bytes} bytes, '
            'with archive paths for longer output. Nonzero exit is tool error feedback.'))
    item.handle_validation_error = 'Invalid aitest_kit arguments. Provide a nonempty args array of strings and an optional nonnegative integer timeout.'
    return ToolSpec(item, public, execution_mode='sequential', side_effects='may_mutate',
        permissions=AITestPermission(), prompt_snippet='Run AITest CLI commands in the test workspace.',
        prompt_guidelines=(
            'For AITest tasks, prefer matching project .claude/skills Skills; read their SKILL.md before acting. '
            'This task-specific source preference overrides the general default source. Explicit user source selection takes priority. '
            'If the requested Skill/source is unavailable, say so rather than silently substituting.',
            'Discover registered knowledge with the shell: python aitest_config/list_knowledge_refs.py [--target <t>]. '
            'Read relevant knowledge_refs before designing cases or diagnosing failures.',
            'Markdown cases are the source of truth; test_workspace/generated/ is compiled output and must not be hand-edited. '
            'For failures consult knowledge_refs, the environment variable named by service.base_url_env in target.yaml, '
            'capture.jsonl from run --capture, then source_root. Triage PRECONDITION_MISSING, ENVIRONMENT_ERROR, '
            'TEST_SCAFFOLD_ERROR, CODEGEN_ERROR and ASSERTION_FAILURE before blaming the product. '
            'Service URLs and credentials come from environment variables or AITEST_ENV_FILE, never hardcoded values.',
        ))
