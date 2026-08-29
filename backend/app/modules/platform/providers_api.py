"""Platform-owned providers API implementation."""

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.core.user_context import get_current_user_id, get_current_user_role
from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderTestRequest(BaseModel):
    providerName: Optional[str] = Field(None, description="Provider name (uses active provider if not set)")
    model: Optional[str] = Field(None, description="Model name (uses provider default if not set)")
    prompt: str = Field(default="Say OK", description="Test prompt")
    maxTokens: int = Field(default=32, ge=1, le=1000, description="Maximum tokens for response")


class ProviderTestResponse(BaseModel):
    ok: bool
    providerName: str
    model: str
    latencyMs: int
    text: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[dict] = None


class ProviderInfoResponse(BaseModel):
    providerName: str
    model: str
    baseUrl: str
    configured: bool
    apiKeySet: bool
    apiKeyMasked: Optional[str] = None
    timeout: int


class ProvidersListResponse(BaseModel):
    userId: str
    role: str
    credentialScope: str
    activeProvider: str
    providers: list[ProviderInfoResponse]


class SetKeyRequest(BaseModel):
    providerName: str
    apiKey: str


class SetActiveRequest(BaseModel):
    providerName: str
    model: Optional[str] = None


class SetProviderConfigRequest(BaseModel):
    providerName: str
    apiKey: Optional[str] = None
    baseUrl: Optional[str] = None
    model: Optional[str] = None
    makeActive: bool = False


class SettingsResponse(BaseModel):
    ok: bool
    message: str = ""


class BigModelModelsResponse(BaseModel):
    models: list[str]


@router.post("/test", response_model=ProviderTestResponse, summary="Test Provider Connection")
async def test_provider(request: ProviderTestRequest) -> ProviderTestResponse:
    settings = get_settings()
    provider_name = request.providerName or settings.get_active_provider()

    try:
        settings.get_provider_config(provider_name)
        if not settings.get_api_key(provider_name):
            return ProviderTestResponse(
                ok=False,
                providerName=provider_name,
                model=request.model or settings.get_active_model(provider_name),
                latencyMs=0,
                error="API key not configured for the signed-in account.",
            )

        client = get_provider_client(provider_name)
        response = client.chat(
            messages=[ChatMessage(role="user", content=request.prompt)],
            model=request.model,
            max_tokens=request.maxTokens,
            temperature=0,
        )
        return ProviderTestResponse(
            ok=True,
            providerName=provider_name,
            model=response.model,
            latencyMs=response.latency_ms,
            text=response.text,
            usage=response.usage,
        )
    except ProviderError as exc:
        return ProviderTestResponse(
            ok=False,
            providerName=provider_name,
            model=request.model or settings.get_active_model(provider_name),
            latencyMs=0,
            error=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        return ProviderTestResponse(
            ok=False,
            providerName=provider_name,
            model=request.model or "",
            latencyMs=0,
            error=f"Unexpected error: {str(exc)}",
        )


@router.get("", response_model=ProvidersListResponse, summary="List Available Providers")
async def list_providers() -> ProvidersListResponse:
    settings = get_settings()
    providers = [ProviderInfoResponse(**settings.get_provider_info(name, mask_key=True)) for name in settings.PROVIDERS.keys()]
    return ProvidersListResponse(
        userId=get_current_user_id(),
        role=get_current_user_role(),
        credentialScope="user",
        activeProvider=settings.get_active_provider(),
        providers=providers,
    )


@router.post("/set-key", summary="Set API key for a provider at runtime")
async def set_provider_key(req: SetKeyRequest) -> SettingsResponse:
    settings = get_settings()
    try:
        settings.set_runtime_key(req.providerName, req.apiKey)
        return SettingsResponse(ok=True, message=f"Key set for {req.providerName}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{provider_name}/key", summary="Remove this account's provider API key")
async def remove_provider_key(provider_name: str) -> SettingsResponse:
    settings = get_settings()
    try:
        removed = settings.clear_runtime_key(provider_name)
        message = (
            f"Key removed for {provider_name}"
            if removed
            else f"No account-specific key was set for {provider_name}"
        )
        return SettingsResponse(ok=True, message=message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/set-active", summary="Set the active provider and model")
async def set_active_provider(req: SetActiveRequest) -> SettingsResponse:
    settings = get_settings()
    try:
        settings.set_active_provider(req.providerName)
        if req.model:
            settings.set_runtime_model(req.providerName, req.model)
            settings.set_active_model_global(req.model)
        return SettingsResponse(ok=True, message=f"Active provider: {req.providerName}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/set-config", summary="Set provider API key/base URL/model at runtime")
async def set_provider_config(req: SetProviderConfigRequest) -> SettingsResponse:
    settings = get_settings()
    try:
        settings.get_provider_config(req.providerName)
        if req.apiKey is not None and req.apiKey.strip():
            settings.set_runtime_key(req.providerName, req.apiKey.strip())
        if req.baseUrl is not None and req.baseUrl.strip():
            settings.set_runtime_base_url(req.providerName, req.baseUrl.strip())
        if req.model is not None and req.model.strip():
            settings.set_runtime_model(req.providerName, req.model.strip())
        if req.makeActive:
            settings.set_active_provider(req.providerName)
            if req.model is not None and req.model.strip():
                settings.set_active_model_global(req.model.strip())
        return SettingsResponse(ok=True, message=f"Config updated for {req.providerName}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/bigmodel/models", summary="List BigModel supported models")
async def bigmodel_models() -> BigModelModelsResponse:
    return BigModelModelsResponse(models=[
        "glm-4.5-air", "glm-4.6v", "glm-4.7",
        "search-std", "search-pro", "search-pro-quark", "search-pro-sogou",
    ])


@router.get("/{provider_name}", response_model=ProviderInfoResponse, summary="Get Provider Info")
async def get_provider(provider_name: str) -> ProviderInfoResponse:
    settings = get_settings()
    try:
        info = settings.get_provider_info(provider_name, mask_key=True)
        return ProviderInfoResponse(**info)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
