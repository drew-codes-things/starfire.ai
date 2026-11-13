from fastapi import FastAPI, Request

BROWSER_HARDENING = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
}


def install(app: FastAPI) -> None:

    @app.middleware("http")
    async def harden_responses(request: Request, call_next):
        response = await call_next(request)
        for name, value in BROWSER_HARDENING.items():
            response.headers.setdefault(name, value)
        return response
