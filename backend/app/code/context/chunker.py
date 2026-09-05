"""
Code Chunker - Splits code files into retrievable chunks.

Inspired by CodeFuse-CGM's code graph node extraction.
Supports function/class-level chunking for Python and TypeScript.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

# Maximum chunk size in characters
MAX_CHUNK_SIZE = 4000
# Minimum chunk size to avoid tiny fragments
MIN_CHUNK_SIZE = 50
# Overlap between chunks for context continuity
CHUNK_OVERLAP = 100


@dataclass
class CodeChunk:
    """A chunk of code for retrieval."""
    id: str  # chunk_<hash>
    file_path: str  # Relative path
    language: str
    content: str
    start_line: int
    end_line: int
    chunk_type: str  # "function", "class", "module", "block"
    name: Optional[str] = None  # Function/class name if applicable
    parent_name: Optional[str] = None  # Parent class name if method
    tokens_estimate: int = 0  # Rough token count
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "language": self.language,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chunk_type": self.chunk_type,
            "name": self.name,
            "parent_name": self.parent_name,
            "tokens_estimate": self.tokens_estimate,
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "CodeChunk":
        return CodeChunk(
            id=data["id"],
            file_path=data["file_path"],
            language=data["language"],
            content=data["content"],
            start_line=data["start_line"],
            end_line=data["end_line"],
            chunk_type=data["chunk_type"],
            name=data.get("name"),
            parent_name=data.get("parent_name"),
            tokens_estimate=data.get("tokens_estimate", 0),
        )


class CodeChunker:
    """
    Chunks code files into retrievable units.
    
    Strategies:
    1. Function/class-level chunking for Python/TypeScript
    2. Fixed-size chunking with overlap for other languages
    3. Respects natural boundaries (blank lines, comments)
    """
    
    def __init__(
        self,
        max_chunk_size: int = MAX_CHUNK_SIZE,
        min_chunk_size: int = MIN_CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
    
    def _generate_chunk_id(self, file_path: str, start_line: int, content: str) -> str:
        """Generate unique chunk ID."""
        hash_input = f"{file_path}:{start_line}:{content[:100]}"
        hash_val = hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest()[:12]
        return f"chunk_{hash_val}"
    
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation (chars / 4)."""
        return len(text) // 4
    
    def _chunk_python(self, content: str, file_path: str) -> List[CodeChunk]:
        """Chunk Python code by functions and classes."""
        chunks = []
        lines = content.split('\n')
        
        # Patterns for Python constructs
        class_pattern = re.compile(r'^class\s+(\w+)')
        func_pattern = re.compile(r'^(\s*)def\s+(\w+)')
        
        current_chunk_lines = []
        current_start = 1
        current_type = "module"
        current_name = None
        current_parent = None
        indent_stack = []  # Track indentation levels
        
        def flush_chunk():
            nonlocal current_chunk_lines, current_start, current_type, current_name, current_parent
            if current_chunk_lines:
                content_str = '\n'.join(current_chunk_lines)
                if len(content_str.strip()) >= self.min_chunk_size:
                    chunk = CodeChunk(
                        id=self._generate_chunk_id(file_path, current_start, content_str),
                        file_path=file_path,
                        language="python",
                        content=content_str,
                        start_line=current_start,
                        end_line=current_start + len(current_chunk_lines) - 1,
                        chunk_type=current_type,
                        name=current_name,
                        parent_name=current_parent,
                        tokens_estimate=self._estimate_tokens(content_str),
                    )
                    chunks.append(chunk)
            current_chunk_lines = []
        
        for i, line in enumerate(lines, start=1):
            # Check for class definition
            class_match = class_pattern.match(line)
            if class_match:
                flush_chunk()
                current_start = i
                current_type = "class"
                current_name = class_match.group(1)
                current_parent = None
                indent_stack = [("class", current_name, 0)]
            
            # Check for function definition
            func_match = func_pattern.match(line)
            if func_match:
                indent = len(func_match.group(1))
                func_name = func_match.group(2)
                
                # Determine if this is a method or standalone function
                if indent_stack and indent > 0:
                    parent = indent_stack[-1][1] if indent_stack[-1][0] == "class" else None
                else:
                    parent = None
                    flush_chunk()
                    current_start = i
                
                if not indent_stack or indent == 0:
                    flush_chunk()
                    current_start = i
                    current_type = "function"
                    current_name = func_name
                    current_parent = parent
            
            current_chunk_lines.append(line)
            
            # Check if chunk is getting too large
            current_content = '\n'.join(current_chunk_lines)
            if len(current_content) > self.max_chunk_size:
                flush_chunk()
                current_start = i + 1
                current_type = "block"
                current_name = None
                current_parent = None
        
        # Flush remaining
        flush_chunk()
        
        return chunks
    
    def _chunk_typescript(self, content: str, file_path: str) -> List[CodeChunk]:
        """Chunk TypeScript/JavaScript code by functions and classes."""
        chunks = []
        lines = content.split('\n')
        
        # Patterns for TS/JS constructs
        class_pattern = re.compile(r'^\s*(export\s+)?(class|interface)\s+(\w+)')
        func_pattern = re.compile(r'^\s*(export\s+)?(async\s+)?function\s+(\w+)')
        arrow_pattern = re.compile(r'^\s*(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(')
        
        current_chunk_lines = []
        current_start = 1
        current_type = "module"
        current_name = None
        brace_count = 0
        
        def flush_chunk():
            nonlocal current_chunk_lines, current_start, current_type, current_name
            if current_chunk_lines:
                content_str = '\n'.join(current_chunk_lines)
                if len(content_str.strip()) >= self.min_chunk_size:
                    chunk = CodeChunk(
                        id=self._generate_chunk_id(file_path, current_start, content_str),
                        file_path=file_path,
                        language="typescript",
                        content=content_str,
                        start_line=current_start,
                        end_line=current_start + len(current_chunk_lines) - 1,
                        chunk_type=current_type,
                        name=current_name,
                        tokens_estimate=self._estimate_tokens(content_str),
                    )
                    chunks.append(chunk)
            current_chunk_lines = []
        
        for i, line in enumerate(lines, start=1):
            # Check for class/interface
            class_match = class_pattern.match(line)
            if class_match:
                flush_chunk()
                current_start = i
                current_type = "class"
                current_name = class_match.group(3)
            
            # Check for function
            func_match = func_pattern.match(line)
            if func_match:
                flush_chunk()
                current_start = i
                current_type = "function"
                current_name = func_match.group(3)
            
            # Check for arrow function
            arrow_match = arrow_pattern.match(line)
            if arrow_match:
                flush_chunk()
                current_start = i
                current_type = "function"
                current_name = arrow_match.group(3)
            
            current_chunk_lines.append(line)
            
            # Check if chunk is getting too large
            current_content = '\n'.join(current_chunk_lines)
            if len(current_content) > self.max_chunk_size:
                flush_chunk()
                current_start = i + 1
                current_type = "block"
                current_name = None
        
        # Flush remaining
        flush_chunk()
        
        return chunks
    
    def _chunk_generic(self, content: str, file_path: str, language: str) -> List[CodeChunk]:
        """Generic fixed-size chunking with overlap."""
        chunks = []
        lines = content.split('\n')
        
        current_chunk_lines = []
        current_start = 1
        
        for i, line in enumerate(lines, start=1):
            current_chunk_lines.append(line)
            current_content = '\n'.join(current_chunk_lines)
            
            if len(current_content) >= self.max_chunk_size:
                # Create chunk
                chunk = CodeChunk(
                    id=self._generate_chunk_id(file_path, current_start, current_content),
                    file_path=file_path,
                    language=language,
                    content=current_content,
                    start_line=current_start,
                    end_line=i,
                    chunk_type="block",
                    tokens_estimate=self._estimate_tokens(current_content),
                )
                chunks.append(chunk)
                
                # Keep overlap lines for next chunk
                overlap_lines = max(1, self.chunk_overlap // 50)  # Rough line estimate
                current_chunk_lines = current_chunk_lines[-overlap_lines:]
                current_start = i - overlap_lines + 1
        
        # Flush remaining
        if current_chunk_lines:
            content_str = '\n'.join(current_chunk_lines)
            if len(content_str.strip()) >= self.min_chunk_size:
                chunk = CodeChunk(
                    id=self._generate_chunk_id(file_path, current_start, content_str),
                    file_path=file_path,
                    language=language,
                    content=content_str,
                    start_line=current_start,
                    end_line=current_start + len(current_chunk_lines) - 1,
                    chunk_type="block",
                    tokens_estimate=self._estimate_tokens(content_str),
                )
                chunks.append(chunk)
        
        return chunks
    
    def chunk_file(self, file_path: str, content: str, language: str) -> List[CodeChunk]:
        """
        Chunk a single file.
        
        Args:
            file_path: Relative path to file
            content: File content
            language: Programming language
            
        Returns:
            List of CodeChunk objects
        """
        if not content.strip():
            return []
        
        # Use language-specific chunking
        if language == "python":
            return self._chunk_python(content, file_path)
        elif language in ("typescript", "javascript"):
            return self._chunk_typescript(content, file_path)
        else:
            return self._chunk_generic(content, file_path, language)
    
    def chunk_files(self, files: List[Tuple[str, str, str]]) -> List[CodeChunk]:
        """
        Chunk multiple files.
        
        Args:
            files: List of (file_path, content, language) tuples
            
        Returns:
            List of all CodeChunk objects
        """
        all_chunks = []
        for file_path, content, language in files:
            try:
                chunks = self.chunk_file(file_path, content, language)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.warning(f"Failed to chunk {file_path}: {e}")
        
        logger.info(f"Created {len(all_chunks)} chunks from {len(files)} files")
        return all_chunks
