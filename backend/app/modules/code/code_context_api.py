"""
Code Context API Endpoints

Provides endpoints for:
- Building repository context (scan + chunk)
- Searching context
- Listing contexts
"""

import os
import uuid
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.code.context.repo_scanner import RepoScanner, ScanResult
from app.code.context.chunker import CodeChunker, CodeChunk
from app.code.context.retriever import CodeRetriever, SearchResult
from app.code.context.context_pack import ContextPacker, ContextPack
from app.storage.repo_context_storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/context", tags=["code_context"])


# Request/Response Models

class BuildContextRequest(BaseModel):
    """Request to build repository context."""
    repoPath: str = Field(..., max_length=1024, description="Absolute path to repository")
    includeGlobs: List[str] = Field(default=["*"], description="Glob patterns to include")
    excludeGlobs: List[str] = Field(default=[], description="Additional glob patterns to exclude")
    maxFileSize: int = Field(default=1024*1024, description="Maximum file size in bytes")


class BuildContextResponse(BaseModel):
    """Response from context build."""
    repoContextId: str
    repoPath: str
    fileCount: int
    chunkCount: int
    totalLines: int
    languages: dict
    scanDurationMs: int
    createdAt: str


class SearchContextRequest(BaseModel):
    """Request to search context."""
    repoContextId: str = Field(..., description="Context ID to search")
    query: str = Field(..., description="Search query")
    topK: int = Field(default=10, ge=1, le=50, description="Number of results")
    fileFilter: Optional[str] = Field(default=None, description="Filter by file path pattern")
    languageFilter: Optional[str] = Field(default=None, description="Filter by language")


class ChunkResult(BaseModel):
    """A chunk in search results."""
    id: str
    filePath: str
    language: str
    content: str
    startLine: int
    endLine: int
    chunkType: str
    name: Optional[str]
    score: float
    matchTerms: List[str]


class SearchContextResponse(BaseModel):
    """Response from context search."""
    repoContextId: str
    query: str
    resultCount: int
    results: List[ChunkResult]


class ContextListItem(BaseModel):
    """Item in context list."""
    id: str
    repoPath: str
    fileCount: int
    chunkCount: int
    languages: dict
    createdAt: str


class ContextListResponse(BaseModel):
    """Response listing all contexts."""
    contexts: List[ContextListItem]
    total: int


class ContextDetailResponse(BaseModel):
    """Detailed context response."""
    id: str
    repoPath: str
    fileCount: int
    chunkCount: int
    totalLines: int
    languages: dict
    createdAt: str
    files: List[dict]


# Endpoints

@router.post(
    "/build",
    response_model=BuildContextResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Build Repository Context",
    description="Scan a repository and build searchable context from code files."
)
async def build_context(request: BuildContextRequest) -> BuildContextResponse:
    """Build context from a repository."""
    storage = get_storage()
    
    # Validate repo path
    repo_path = os.path.abspath(request.repoPath)
    if not os.path.isdir(repo_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository path does not exist: {repo_path}"
        )
    
    # Generate context ID
    context_id = f"ctx_{uuid.uuid4().hex[:12]}"
    created_at = datetime.utcnow().isoformat()
    
    try:
        # Scan repository
        scanner = RepoScanner(
            include_patterns=request.includeGlobs,
            exclude_patterns=request.excludeGlobs,
            max_file_size=request.maxFileSize,
        )
        scan_result = scanner.scan(repo_path)
        
        # Chunk files
        chunker = CodeChunker()
        all_chunks = []
        
        for file_info in scan_result.files:
            try:
                with open(file_info.absolute_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                chunks = chunker.chunk_file(file_info.path, content, file_info.language)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.warning(f"Failed to chunk {file_info.path}: {e}")
        
        # Save context
        context_data = {
            "id": context_id,
            "repo_path": repo_path,
            "file_count": scan_result.file_count,
            "chunk_count": len(all_chunks),
            "total_lines": scan_result.total_lines,
            "total_bytes": scan_result.total_bytes,
            "languages": scan_result.languages,
            "scan_duration_ms": scan_result.scan_duration_ms,
            "created_at": created_at,
            "files": [f.to_dict() for f in scan_result.files],
            "chunks": [c.to_dict() for c in all_chunks],
            "skipped_files": scan_result.skipped_files[:100],  # Limit for storage
        }
        
        storage.save(context_id, context_data)
        
        logger.info(f"Built context {context_id}: {scan_result.file_count} files, {len(all_chunks)} chunks")
        
        return BuildContextResponse(
            repoContextId=context_id,
            repoPath=repo_path,
            fileCount=scan_result.file_count,
            chunkCount=len(all_chunks),
            totalLines=scan_result.total_lines,
            languages=scan_result.languages,
            scanDurationMs=scan_result.scan_duration_ms,
            createdAt=created_at,
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to build context: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build context: {str(e)}"
        )


@router.post(
    "/search",
    response_model=SearchContextResponse,
    summary="Search Context",
    description="Search for relevant code chunks in a context."
)
async def search_context(request: SearchContextRequest) -> SearchContextResponse:
    """Search for relevant chunks."""
    storage = get_storage()
    
    # Load context
    context_data = storage.get(request.repoContextId)
    if not context_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context not found: {request.repoContextId}"
        )
    
    # Reconstruct chunks
    chunks = [CodeChunk.from_dict(c) for c in context_data.get("chunks", [])]
    
    if not chunks:
        return SearchContextResponse(
            repoContextId=request.repoContextId,
            query=request.query,
            resultCount=0,
            results=[],
        )
    
    # Build retriever and search
    retriever = CodeRetriever(chunks)
    results = retriever.search(
        query=request.query,
        top_k=request.topK,
        file_filter=request.fileFilter,
        language_filter=request.languageFilter,
    )
    
    # Convert to response format
    chunk_results = [
        ChunkResult(
            id=r.chunk.id,
            filePath=r.chunk.file_path,
            language=r.chunk.language,
            content=r.chunk.content,
            startLine=r.chunk.start_line,
            endLine=r.chunk.end_line,
            chunkType=r.chunk.chunk_type,
            name=r.chunk.name,
            score=r.score,
            matchTerms=r.match_terms,
        )
        for r in results
    ]
    
    return SearchContextResponse(
        repoContextId=request.repoContextId,
        query=request.query,
        resultCount=len(chunk_results),
        results=chunk_results,
    )


@router.get(
    "",
    response_model=ContextListResponse,
    summary="List Contexts",
    description="List all repository contexts."
)
async def list_contexts() -> ContextListResponse:
    """List all contexts."""
    storage = get_storage()
    contexts = storage.list_all()
    
    items = [
        ContextListItem(
            id=c["id"],
            repoPath=c.get("repo_path", ""),
            fileCount=c.get("file_count", 0),
            chunkCount=c.get("chunk_count", 0),
            languages=c.get("languages", {}),
            createdAt=c.get("created_at", ""),
        )
        for c in contexts
    ]
    
    return ContextListResponse(
        contexts=items,
        total=len(items),
    )


@router.get(
    "/{context_id}",
    response_model=ContextDetailResponse,
    summary="Get Context Details",
    description="Get detailed information about a context (without chunks)."
)
async def get_context(context_id: str) -> ContextDetailResponse:
    """Get context details."""
    storage = get_storage()
    
    context_data = storage.get(context_id)
    if not context_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context not found: {context_id}"
        )
    
    return ContextDetailResponse(
        id=context_data["id"],
        repoPath=context_data.get("repo_path", ""),
        fileCount=context_data.get("file_count", 0),
        chunkCount=context_data.get("chunk_count", 0),
        totalLines=context_data.get("total_lines", 0),
        languages=context_data.get("languages", {}),
        createdAt=context_data.get("created_at", ""),
        files=context_data.get("files", []),
    )


@router.delete(
    "/{context_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Context",
    description="Delete a repository context."
)
async def delete_context(context_id: str):
    """Delete a context."""
    storage = get_storage()
    
    if not storage.exists(context_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context not found: {context_id}"
        )
    
    storage.delete(context_id)
