"""Reproduction of catastrophic-backtracking regex inside a WAF middleware."""
import os
import re

from fastapi import FastAPI, Request

EVIL = re.compile(r'(?:(?:"|\d|.*)+(?:.*=.*))')
app = FastAPI()


@app.middleware("http")
async def waf(request: Request, call_next):
    if os.environ.get("EVIL_REGEX_ACTIVE") == "1":
        EVIL.match(str(request.url.query))
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def root():
    return {"ok": True}
