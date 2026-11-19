"""Project and chat storage custody for explicitly requested cache cleanup."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
from uuid import UUID

from naiyue_test_agent.agent_runtime.async_utils import acquire_blocking, run_blocking
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.cache_cleanup import read_connection, registrations, session_rows, validate_cleanup_database
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue
from naiyue_test_agent.storage.files import acquire_store_lock, open_store_file, release_store_lock
from naiyue_test_agent.storage.project_databases import locked_project_reader
from naiyue_test_agent.tools.output_cleanup import clean_outputs


def read_registry(projects, stack):
    with projects._lock:
        if projects._repo is not None:
            database = projects._repo.database
            def read(limit, project_id=None):
                with database.transaction() as conn:
                    return registrations(conn, limit, project_id)
            return read
        path = projects.data_dir / 'projects.sqlite'
        try:
            path.lstat()
        except FileNotFoundError:
            return lambda limit, project_id=None: []
        from naiyue_test_agent.storage.files import check_store_directory
        check_store_directory(path.parent)
        fd = open_store_file(path.with_suffix('.sqlite.lock'))
        stack.callback(os.close, fd)
        acquire_store_lock(fd)
        stack.callback(release_store_lock, fd)
    def read(limit, project_id=None):
        with read_connection(path) as conn:
            return registrations(conn, limit, project_id)
    return read


@contextmanager
def hot_outputs(host, root):
    persistence = host.sessions._persistence
    sessions = host._lifetime._sessions
    with ExitStack() as reservations:
        with persistence._lock:
            active = set(persistence._active) | persistence._blocked | persistence._recovering
            if any(sessions.session_archive_root(sid) == root for sid in active):
                raise CleanupIssue('scope_busy', '会话运行或恢复仍在收尾')
            try:
                reservations.enter_context(sessions.output_guard(root, persisted=True))
            except (ValueError, RuntimeError) as error:
                raise CleanupIssue('scope_busy', '归档仍被运行或后台任务使用') from error
        yield


def clean_scope(database, profile, run, *, host=None, registry_check=None, metadata_limit=None):
    root = database.parent
    with ExitStack() as stack:
        if host is None:
            cache = root / ('cloud-input-cache' if run.category == 'cloud_inputs' else 'artifacts')
            try:
                database.lstat()
            except FileNotFoundError:
                candidate = root.parent / 'sessions' if profile == 'chats' else cache
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    return
                raise CleanupIssue('metadata_unavailable', '存在缓存范围，但缺少会话数据库', skipped=False)
            try:
                connection = stack.enter_context(locked_project_reader(database))
            except ProjectError as error:
                if error.code != 'project_in_use':
                    raise
                raise CleanupIssue('scope_busy', '项目数据库正被其他进程使用') from error
            version = validate_cleanup_database(connection, profile)
            rows = session_rows(connection, version, run.remaining + 1) if run.category == 'tool_outputs' else []
        else:
            if host.sessions._persistence.storage.path != database:
                raise CleanupIssue('registration_changed', '打开的会话库与登记路径不同')
            db = host.sessions._persistence.storage.database
            with db.transaction() as connection:
                version = validate_cleanup_database(connection, profile)
                rows = session_rows(connection, version, run.remaining + 1) if run.category == 'tool_outputs' else []
        run.check(len(rows), path=root)
        if run.category == 'cloud_inputs':
            from naiyue_test_agent.cloud_files.cache_cleanup import clean_input_cache
            if registry_check:
                registry_check()
            clean_input_cache(root / 'cloud-input-cache', run, host=host,
                              connection=connection if host is None else None, metadata_limit=metadata_limit)
            return
        if profile != 'chats':
            candidates = [(None, root / 'artifacts', any(state != 'ready' or child_busy for _, state, child_busy in rows))]
        else:
            candidates = []
            for sid, state, child_busy in rows:
                try:
                    identity = UUID(sid)
                except (ValueError, TypeError, AttributeError):
                    identity = None
                if identity is None or sid not in (identity.hex, str(identity)):
                    run.issue(root, 'check_dependencies', CleanupIssue('ownership_invalid', '会话 ID 无法用于归档路径'))
                    continue
                candidates.append((sid, root.parent / 'sessions' / sid / 'artifacts', state != 'ready' or child_busy))
        for sid, archive, recovering in candidates:
            run.select(run.category, 'chat' if sid else 'project', sid or profile)
            run.check(path=archive)
            run.counts['scopes_visited'] += 1
            try:
                if recovering and host is None:
                    raise CleanupIssue('recovery_required', '会话存在运行或恢复记录')
                if registry_check:
                    registry_check()
                with ExitStack() as guard:
                    if host is not None:
                        guard.enter_context(hot_outputs(host, archive))
                        with db.transaction() as connection:
                            current = session_rows(connection, version, run.remaining + 1, session_id=sid)
                        run.check(len(current), path=archive)
                        if sid is not None and not current:
                            raise CleanupIssue('identity_changed', '持久会话已移除')
                        if any(state != 'ready' or child_busy for _, state, child_busy in current):
                            raise CleanupIssue('recovery_required', '会话状态已变化或存在恢复记录')
                    clean_outputs(archive, run)
            except CleanupIssue as error:
                run.issue(archive, 'reserve', error)


async def scan(hosts, run):
    projects = hosts.projects
    stack = ExitStack()

    def acquire_registry():
        try:
            return read_registry(projects, stack)
        except BaseException:
            stack.close()
            raise

    async def dispose(_):
        await run_blocking(stack.close)

    reader = await acquire_blocking(acquire_registry, dispose)
    try:
        references = await run_blocking(reader, run.remaining + 1)
        run.check(len(references), path=projects.data_dir)
        for category in run.categories:
            for ref in references:
                project_id, saved_path, lifecycle, _ = ref
                run.select(category, 'project', project_id)
                run.check(path=projects.data_dir)
                database = projects.data_dir
                try:
                    if str(UUID(project_id)) != project_id:
                        raise CleanupIssue('ownership_invalid', '登记项目 ID 无效')
                    database = projects.data_dir / 'workspaces' / project_id / 'sessions.sqlite'
                    if lifecycle != 'active' or Path(saved_path) != database:
                        raise CleanupIssue('registration_changed', '项目状态或数据库位置不允许清理')
                    async with hosts.project_change(project_id):
                        if await run_blocking(reader, 1, project_id) != [ref]:
                            raise CleanupIssue('registration_changed', '扫描后项目登记已变化')
                        host = hosts._hosts.get(project_id)
                        with ExitStack() as guard:
                            if host is not None and category == 'cloud_inputs':
                                guard.enter_context(host.cloud_tasks.input_cache_cleanup())
                            await run_blocking(clean_scope, database, project_id, run, host=host,
                                metadata_limit=hosts.settings.cloud_tasks.input_limits.max_metadata_bytes,
                                registry_check=lambda: verify_registration(reader, ref))
                except run.scope_errors as error:
                    run.issue(database.parent if database != projects.data_dir else database, 'check_dependencies', error)
            if category == 'tool_outputs':
                run.select(category, 'service')
                database = projects.data_dir / 'chats' / 'sessions.sqlite'
                try:
                    async with hosts.project_change(None):
                        await run_blocking(clean_scope, database, 'chats', run, host=hosts._hosts.get(None))
                except run.scope_errors as error:
                    run.issue(database.parent, 'check_dependencies', error)
    finally:
        await run_blocking(stack.close)


def verify_registration(reader, ref):
    if reader(1, ref[0]) != [ref]:
        raise CleanupIssue('registration_changed', '删除前项目登记已变化')
