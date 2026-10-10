"""HTTP endpoints."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from payload_cache.schemas import PayloadCreate, PayloadCreated, PayloadRead
from payload_cache.service import PayloadService

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> PayloadService:
    return PayloadService(session, request.app.state.transformer)


ServiceDep = Annotated[PayloadService, Depends(get_service)]


@router.post(
    "/payload",
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_200_OK: {"model": PayloadCreated, "description": "Payload reused"}},
)
async def create_payload(
    body: PayloadCreate, service: ServiceDep, response: Response
) -> PayloadCreated:
    payload_id, created = await service.create(body.list_1, body.list_2)
    if created:
        return PayloadCreated(id=payload_id, message="Payload created")
    # 200 rather than 201 tells the client nothing new was stored.
    response.status_code = status.HTTP_200_OK
    return PayloadCreated(id=payload_id, message="Payload already exists, identifier reused")


@router.get("/payload/{payload_id}")
async def read_payload(payload_id: str, service: ServiceDep) -> PayloadRead:
    output = await service.get_output(payload_id)
    if output is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payload not found")
    return PayloadRead(output=output)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
