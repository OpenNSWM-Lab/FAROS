"""
Code Evaluation API Endpoints

Provides endpoints for:
- Static code evaluation
- Dynamic code evaluation
- Combined evaluation with scoring
"""

import uuid
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.code.eval.static_eval import StaticEvaluator, StaticEvalResult
from app.code.eval.dynamic_eval import DynamicEvaluator, DynamicEvalResult, ExecutionStatus
from app.code.eval.scoring import EvalScorer, EvalScore
from app.storage.code_eval_storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/eval", tags=["code_eval"])


# Request/Response Models

class EvalCodeRequest(BaseModel):
    """Request to evaluate code."""
    code: str = Field(..., description="Code to evaluate")
    language: str = Field(default="python", description="Programming language")
    filename: str = Field(default="<code>", description="Filename for diagnostics")
    checkRisks: bool = Field(default=True, description="Check for risky patterns")
    checkLint: bool = Field(default=True, description="Run lint checks")


class EvalRepoRequest(BaseModel):
    """Request to evaluate a repository."""
    repoPath: str = Field(..., description="Path to repository")
    runTests: bool = Field(default=True, description="Run tests if found")
    commands: List[str] = Field(default=[], max_length=20, description="Custom commands to run")
    timeout: int = Field(default=60, ge=5, le=300, description="Timeout per command")


class DiagnosticResponse(BaseModel):
    """A diagnostic message."""
    severity: str
    message: str
    file: Optional[str]
    line: Optional[int]
    code: Optional[str]


class StaticEvalResponse(BaseModel):
    """Static evaluation response."""
    passed: bool
    syntaxValid: bool
    errorCount: int
    warningCount: int
    infoCount: int
    diagnostics: List[DiagnosticResponse]
    risks: List[dict]


class CommandResultResponse(BaseModel):
    """Command execution result."""
    command: str
    status: str
    exitCode: Optional[int]
    stdout: str
    stderr: str
    durationMs: int


class DynamicEvalResponse(BaseModel):
    """Dynamic evaluation response."""
    status: str
    testsFound: bool
    testsPassed: int
    testsFailed: int
    testsSkipped: int
    commands: List[CommandResultResponse]
    summary: str


class ScoreResponse(BaseModel):
    """Evaluation score."""
    overall: float
    staticScore: float
    dynamicScore: Optional[float]
    dimensions: dict
    grade: str


class FullEvalResponse(BaseModel):
    """Full evaluation response."""
    id: str
    createdAt: str
    static: StaticEvalResponse
    dynamic: Optional[DynamicEvalResponse]
    score: ScoreResponse


# Endpoints

@router.post(
    "/static",
    response_model=StaticEvalResponse,
    summary="Static Code Evaluation",
    description="Evaluate code statically (syntax, lint, risks)."
)
async def evaluate_static(request: EvalCodeRequest) -> StaticEvalResponse:
    """Evaluate code statically."""
    evaluator = StaticEvaluator()
    
    result = evaluator.evaluate(
        code=request.code,
        language=request.language,
        filename=request.filename,
        check_risks=request.checkRisks,
        check_lint=request.checkLint,
    )
    
    return StaticEvalResponse(
        passed=result.passed,
        syntaxValid=result.syntax_valid,
        errorCount=result.error_count,
        warningCount=result.warning_count,
        infoCount=result.info_count,
        diagnostics=[
            DiagnosticResponse(
                severity=d.severity.value,
                message=d.message,
                file=d.file,
                line=d.line,
                code=d.code,
            )
            for d in result.diagnostics
        ],
        risks=result.risks,
    )


@router.post(
    "/dynamic",
    response_model=DynamicEvalResponse,
    summary="Dynamic Code Evaluation",
    description="Evaluate code dynamically (run tests, commands)."
)
async def evaluate_dynamic(request: EvalRepoRequest) -> DynamicEvalResponse:
    """Evaluate code dynamically."""
    import os
    
    if not os.path.isdir(request.repoPath):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository path does not exist: {request.repoPath}"
        )
    
    evaluator = DynamicEvaluator(timeout=request.timeout)
    
    result = evaluator.evaluate(
        repo_path=request.repoPath,
        commands=request.commands if request.commands else None,
        run_tests=request.runTests,
        timeout=request.timeout,
    )
    
    return DynamicEvalResponse(
        status=result.status.value,
        testsFound=result.tests_found,
        testsPassed=result.tests_passed,
        testsFailed=result.tests_failed,
        testsSkipped=result.tests_skipped,
        commands=[
            CommandResultResponse(
                command=c.command,
                status=c.status.value,
                exitCode=c.exit_code,
                stdout=c.stdout,
                stderr=c.stderr,
                durationMs=c.duration_ms,
            )
            for c in result.commands
        ],
        summary=result.summary,
    )


@router.post(
    "",
    response_model=FullEvalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Full Code Evaluation",
    description="Run both static and dynamic evaluation with scoring."
)
async def evaluate_full(
    code: Optional[str] = None,
    repoPath: Optional[str] = None,
    language: str = "python",
    runTests: bool = True,
    timeout: int = 60,
) -> FullEvalResponse:
    """Run full evaluation."""
    import os
    
    if not code and not repoPath:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'code' or 'repoPath' must be provided"
        )
    
    eval_id = f"eval_{uuid.uuid4().hex[:12]}"
    created_at = datetime.utcnow().isoformat()
    storage = get_storage()
    
    # Static evaluation
    static_evaluator = StaticEvaluator()
    
    if code:
        static_result = static_evaluator.evaluate(code, language)
    elif repoPath:
        # Evaluate main files in repo
        if not os.path.isdir(repoPath):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Repository path does not exist: {repoPath}"
            )
        
        # Find and evaluate Python files
        all_diagnostics = []
        all_risks = []
        syntax_valid = True
        
        for root, dirs, files in os.walk(repoPath):
            # Skip common excluded dirs
            dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', 'venv', '.venv']]
            
            for f in files[:20]:  # Limit files
                if f.endswith('.py'):
                    file_path = os.path.join(root, f)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as fp:
                            content = fp.read()
                        result = static_evaluator.evaluate(content, "python", f)
                        all_diagnostics.extend(result.diagnostics)
                        all_risks.extend(result.risks)
                        if not result.syntax_valid:
                            syntax_valid = False
                    except Exception:
                        pass
        
        static_result = StaticEvalResult(
            passed=syntax_valid and len([d for d in all_diagnostics if d.severity.value == "error"]) == 0,
            syntax_valid=syntax_valid,
            error_count=len([d for d in all_diagnostics if d.severity.value == "error"]),
            warning_count=len([d for d in all_diagnostics if d.severity.value == "warning"]),
            info_count=len([d for d in all_diagnostics if d.severity.value == "info"]),
            diagnostics=all_diagnostics[:50],  # Limit
            risks=all_risks[:20],
        )
    
    # Dynamic evaluation
    dynamic_result = None
    dynamic_response = None
    
    if repoPath and runTests:
        dynamic_evaluator = DynamicEvaluator(timeout=timeout)
        dynamic_result = dynamic_evaluator.evaluate(repoPath, run_tests=True, timeout=timeout)
        
        dynamic_response = DynamicEvalResponse(
            status=dynamic_result.status.value,
            testsFound=dynamic_result.tests_found,
            testsPassed=dynamic_result.tests_passed,
            testsFailed=dynamic_result.tests_failed,
            testsSkipped=dynamic_result.tests_skipped,
            commands=[
                CommandResultResponse(
                    command=c.command,
                    status=c.status.value,
                    exitCode=c.exit_code,
                    stdout=c.stdout[:1000],
                    stderr=c.stderr[:1000],
                    durationMs=c.duration_ms,
                )
                for c in dynamic_result.commands
            ],
            summary=dynamic_result.summary,
        )
    
    # Scoring
    scorer = EvalScorer()
    score = scorer.score(static_result, dynamic_result)
    
    # Build response
    static_response = StaticEvalResponse(
        passed=static_result.passed,
        syntaxValid=static_result.syntax_valid,
        errorCount=static_result.error_count,
        warningCount=static_result.warning_count,
        infoCount=static_result.info_count,
        diagnostics=[
            DiagnosticResponse(
                severity=d.severity.value,
                message=d.message,
                file=d.file,
                line=d.line,
                code=d.code,
            )
            for d in static_result.diagnostics[:30]
        ],
        risks=static_result.risks[:10],
    )
    
    score_response = ScoreResponse(
        overall=score.overall,
        staticScore=score.static_score,
        dynamicScore=score.dynamic_score,
        dimensions=score.dimensions,
        grade=score.grade,
    )
    
    # Save to storage
    eval_data = {
        "id": eval_id,
        "created_at": created_at,
        "code": code[:1000] if code else None,
        "repo_path": repoPath,
        "language": language,
        "static": static_result.to_dict(),
        "dynamic": dynamic_result.to_dict() if dynamic_result else None,
        "score": score.to_dict(),
    }
    storage.save(eval_id, eval_data)
    
    return FullEvalResponse(
        id=eval_id,
        createdAt=created_at,
        static=static_response,
        dynamic=dynamic_response,
        score=score_response,
    )


@router.get(
    "/{eval_id}",
    response_model=FullEvalResponse,
    summary="Get Evaluation",
    description="Get evaluation by ID."
)
async def get_evaluation(eval_id: str) -> FullEvalResponse:
    """Get evaluation by ID."""
    storage = get_storage()
    data = storage.get(eval_id)
    
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation not found: {eval_id}"
        )
    
    # Reconstruct response from stored data
    static_data = data.get("static", {})
    dynamic_data = data.get("dynamic")
    score_data = data.get("score", {})
    
    static_response = StaticEvalResponse(
        passed=static_data.get("passed", False),
        syntaxValid=static_data.get("syntax_valid", False),
        errorCount=static_data.get("error_count", 0),
        warningCount=static_data.get("warning_count", 0),
        infoCount=static_data.get("info_count", 0),
        diagnostics=[
            DiagnosticResponse(**d) for d in static_data.get("diagnostics", [])[:30]
        ],
        risks=static_data.get("risks", [])[:10],
    )
    
    dynamic_response = None
    if dynamic_data:
        dynamic_response = DynamicEvalResponse(
            status=dynamic_data.get("status", "skipped"),
            testsFound=dynamic_data.get("tests_found", False),
            testsPassed=dynamic_data.get("tests_passed", 0),
            testsFailed=dynamic_data.get("tests_failed", 0),
            testsSkipped=dynamic_data.get("tests_skipped", 0),
            commands=[
                CommandResultResponse(**c) for c in dynamic_data.get("commands", [])
            ],
            summary=dynamic_data.get("summary", ""),
        )
    
    score_response = ScoreResponse(
        overall=score_data.get("overall", 0),
        staticScore=score_data.get("static_score", 0),
        dynamicScore=score_data.get("dynamic_score"),
        dimensions=score_data.get("dimensions", {}),
        grade=score_data.get("grade", "F"),
    )
    
    return FullEvalResponse(
        id=data["id"],
        createdAt=data.get("created_at", ""),
        static=static_response,
        dynamic=dynamic_response,
        score=score_response,
    )
