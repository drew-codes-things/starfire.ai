import importlib

import auth

ADDON = "local_addon"
ROUTERS_ATTRIBUTE = "routers"
ADMIN_PREFIXES_ATTRIBUTE = "admin_only_prefixes"


def load():
    try:
        return importlib.import_module(ADDON)
    except ModuleNotFoundError as error:
        if error.name != ADDON:
            raise
        return None


def install(app) -> bool:
    addon = load()
    if addon is None:
        return False
    for router in getattr(addon, ROUTERS_ATTRIBUTE, ()):
        app.include_router(router)
    auth.admin_only_addon_prefixes.extend(getattr(addon, ADMIN_PREFIXES_ATTRIBUTE, ()))
    return True
