"""FastAPI wrapper for local development.

A thin adapter: every request becomes a `handler.Request`, goes through the same
`handler.route`, and the `Response` is turned back into JSON. Nothing is routed,
authorised or validated here that is not also done in the Lambda path.

Run it with:
    DEMO_KEY=... uv run uvicorn api.local:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import Response as FastAPIResponse

from api.handler import DEMO_KEY_HEADER, Request, allowed_origin, route

app = FastAPI(title="RxSync Investigator", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[allowed_origin()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", DEMO_KEY_HEADER],
)


@app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS"], include_in_schema=False)
async def everything(request: FastAPIRequest, path: str) -> FastAPIResponse:
    raw = await request.body()
    response = route(
        Request(
            method=request.method,
            path="/" + path,
            headers={key.lower(): value for key, value in request.headers.items()},
            body=raw.decode("utf-8") if raw else None,
        )
    )
    if response.body is None:
        return FastAPIResponse(status_code=response.status, headers=response.headers)
    return JSONResponse(
        status_code=response.status, content=response.body, headers=response.headers
    )
