


def scene_load_failure(log: str, declared_scene: str) -> str | None:
    if not declared_scene:
        return None
    expected = f"ERROR: Failed loading scene: {declared_scene}."
    return next((line.strip() for line in log.splitlines()
                 if line.strip() == expected), None)
