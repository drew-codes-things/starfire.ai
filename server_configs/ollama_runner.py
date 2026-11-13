from config import config

SHIFTS_CONTEXT = False


def runner_options(options: dict | None) -> dict:
    window = {"num_ctx": config.task_num_ctx} if config.task_num_ctx else {}
    return {**window, **(options or {})}


def for_runner(payload: dict) -> dict:
    options = runner_options(payload.get("options"))
    return {**payload, **({"options": options} if options else {}), "shift": SHIFTS_CONTEXT}
