"""Load bounded project instructions for a single workspace run."""

import logging


logger = logging.getLogger(__name__)
MAX_PROJECT_INSTRUCTIONS_BYTES = 64 * 1024

BASE_SYSTEM_PROMPT = (
    '你是在naiyue-test-agent中工作的通用编程与测试助手，协助用户完成编程与软件测试等任务。'
    '根据当前请求分析问题；需要时使用本次实际提供的工具读取资料、修改文件或运行命令，'
    '并依据结果继续工作。\n'
    '只把工具实际返回的内容当作已观察结果。工具失败、操作被拒绝或执行结果不确定时，'
    '明确说明当前状态。修改文件前先了解相关内容，保留与本次任务无关的已有改动。\n'
    '回答使用用户的语言。说明实际修改的位置、执行过的验证及其结果；'
    '没有验证的结论要明确标为推断。保持回答简洁。'
)


class ProjectInstructionsError(ValueError):
    """The selected workspace instruction file cannot be used safely."""


def load_project_instruction_part(files):
    """Select AGENTS.md before CLAUDE.md and return a per-run prompt section."""
    if files is None:
        return None

    files.validate()
    execution_path = files.environment.path(files.environment.root)

    for name in ('AGENTS.md', 'CLAUDE.md'):
        candidate = execution_path / name
        try:
            info = files.resource_info(str(candidate))
            if not info.exists and not info.is_link:
                continue
            target = files.environment.path(files.resolve_resource(str(candidate)))
            relative = target.relative_to(execution_path)
            if any(part.startswith('.') for part in relative.parts) or files.resource_info(str(target)).kind != 'file':
                raise ValueError('invalid_project_instruction_target')
            content = files.read_resource(str(target), MAX_PROJECT_INSTRUCTIONS_BYTES)
            if len(content) > MAX_PROJECT_INSTRUCTIONS_BYTES:
                raise ValueError('project_instruction_too_large')
            decoded = content.decode('utf-8-sig')
            if '\x00' in decoded:
                raise ValueError('project_instruction_not_text')
        except (OSError, ValueError, UnicodeError, RuntimeError) as error:
            logger.warning('project_instructions_failed source=%s error_type=%s',
                           name, type(error).__name__)
            raise ProjectInstructionsError(f'Could not load {name}') from error

        logger.info('project_instructions_loaded source=%s bytes=%s', name, len(content))
        if not decoded.strip():
            return None
        return {'id': 'project', 'source': name, 'text': (
            f'\n\n<project_instructions source="{name}">\n{decoded}\n</project_instructions>')}
    return None
