from config import config
from main_model import main_model

SHIFTS_CONTEXT = False


def fixed_context(model: str) -> int:
    main = main_model()
    is_the_main = bool(main) and model == main
    return config.task_num_ctx if is_the_main and config.task_num_ctx > 0 else 0


def runner_options(model: str, options: dict | None) -> dict:
    window = {"num_ctx": config.task_num_ctx} if config.task_num_ctx else {}
    chosen = {**window, **(options or {})}
    fixed = fixed_context(model)
    return {**chosen, "num_ctx": fixed} if fixed else chosen


def for_runner(payload: dict) -> dict:
    options = runner_options(payload.get("model", ""), payload.get("options"))
    return {**payload, **({"options": options} if options else {}), "shift": SHIFTS_CONTEXT}
