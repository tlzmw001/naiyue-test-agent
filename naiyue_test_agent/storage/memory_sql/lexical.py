"""Register deterministic SQLite lexical functions."""

import json
from naiyue_test_agent.contracts.memory_lexical import tokens, alias_text, digest, LEXEMES


def configure(connection):
    connection.create_function('memory_terms', 1, lambda text: ' '.join(tokens(text)), deterministic=True)
    connection.create_function('memory_aliases', 1, alias_text, deterministic=True)
    connection.create_function('memory_digest', 3, digest, deterministic=True)
    connection.create_function('memory_anchors', 4, lambda title, aliases, body, required:
        int(set(json.loads(required)) <= set(LEXEMES.findall(' '.join((title, alias_text(aliases), body))))),
        deterministic=True)
