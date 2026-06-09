from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/{provider}")
async def payment_webhook(provider: str) -> dict[str, str]:
    raise HTTPException(
        status_code=501,
        detail=f"Payment provider '{provider}' not implemented yet.",
    )
