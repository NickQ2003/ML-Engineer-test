"""Keep the optional preview access key out of supervised subprocesses."""

import os


def worker_environment(**overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(overrides)
    environment.pop("EXTRACT_PREVIEW_TOKEN", None)
    # Workers may instantiate Settings; they neither store nor serve previews.
    environment["EXTRACT_PREVIEW_ENABLED"] = "false"
    return environment
