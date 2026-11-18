from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictStr

from arty_devices import API_SUFFIX, ask_brain, require_admin

router = APIRouter(prefix="/api/arty-memory")

MAX_TEXT_CHARS = 20_000
PASSED_ON = frozenset({400, 404, 409, 429, 503})
ONLY_ADMINS = "edit Arty's memory"
MEMORY_PATH = f"{API_SUFFIX}/memory"


class AddBody(BaseModel):
    entity: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    fact: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    type: StrictStr = Field("", max_length=MAX_TEXT_CHARS)


class EditBody(BaseModel):
    entity: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    old: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    new: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class DeleteBody(BaseModel):
    entity: StrictStr = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    fact: StrictStr = Field("", max_length=MAX_TEXT_CHARS)


def shown_entity(entity) -> dict | None:
    if not isinstance(entity, dict) or not isinstance(entity.get("name"), str):
        return None
    kind = entity.get("entityType")
    facts = [fact for fact in entity.get("observations") or [] if isinstance(fact, str)]
    return {"name": entity["name"], "type": kind if isinstance(kind, str) else "", "facts": facts}


def shown_memory(graph: dict) -> dict:
    entities = graph.get("entities") if isinstance(graph, dict) else None
    if not isinstance(entities, list):
        return {"entities": []}
    return {"entities": [shown for shown in map(shown_entity, entities) if shown is not None]}


async def change_memory(action: str, body: BaseModel) -> dict:
    return await ask_brain("POST", f"{MEMORY_PATH}/{action}", json_body=body.model_dump(exclude_defaults=True),
                           passed_on=PASSED_ON)


@router.get("")
async def read_memory(request: Request):
    require_admin(request, ONLY_ADMINS)
    return shown_memory(await ask_brain("GET", MEMORY_PATH, passed_on=PASSED_ON))


@router.post("/add")
async def add_fact(request: Request, body: AddBody):
    require_admin(request, ONLY_ADMINS)
    return await change_memory("add", body)


@router.post("/edit")
async def edit_fact(request: Request, body: EditBody):
    require_admin(request, ONLY_ADMINS)
    return await change_memory("edit", body)


@router.post("/delete")
async def delete_fact(request: Request, body: DeleteBody):
    require_admin(request, ONLY_ADMINS)
    return await change_memory("delete", body)
