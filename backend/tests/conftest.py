import os

def pytest_configure(config):
    """Clear external Neo4j configuration before test collection/configuration starts."""
    vars_to_clear = [
        "SENTINEL_NEO4J_STRICT",
        "NEO4J_ENABLED",
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE"
    ]
    for var in vars_to_clear:
        os.environ.pop(var, None)
