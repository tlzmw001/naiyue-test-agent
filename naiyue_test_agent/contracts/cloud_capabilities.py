"""Implemented owner API capabilities, independent of package version labels."""
BASE_CAPABILITIES=('projects:read','projects:create','commands:read','tasks:read','history:events','history:read',
    'history:detail','history:threads','observations:snapshot','observations:stream','usage:read','inputs:import',
    'tasks:create','runs:start','artifacts:capture','artifacts:download','results:read','results:actions',
    'artifacts:range','capacity:read','releases:write','releases:read','maintenance:commands')
BACKEND_CAPABILITIES=('environment:commands','environment:reconcile','tasks:delete','tasks:upgrade')


def owner_capabilities(backend):
    return list(BASE_CAPABILITIES+(BACKEND_CAPABILITIES if backend else ()))
