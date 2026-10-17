"""test_indexer.py - Unit tests for AST indexing enhancements.

Verifies:
1. self.method() calls inside a class are resolved.
2. cls.method() calls inside a class are resolved.
3. Base class methods defined in the same file are resolved via inheritance.
4. Module attribute calls (alias.func) are resolved to target file/symbol.
5. @overload stubs are skipped and do not generate duplicate symbols.
6. Calls on arbitrary variables (e.g. adapter.send) create NO edge.
7. sample_repo still yields exactly 9 symbols and exactly 4 call edges.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from indexer import index_codebase


def test_self_and_cls_call_resolved(tmp_path):
    code = '''
class Greeter:
    def helper(self):
        return "hello"
    
    @classmethod
    def factory_helper(cls):
        return "world"

    def greet(self):
        return self.helper()

    @classmethod
    def create(cls):
        return cls.factory_helper()
'''
    (tmp_path / "greeter.py").write_text(code, encoding="utf-8")
    
    stats = index_codebase(str(tmp_path), graph=None)
    assert stats["files_count"] == 1
    # Class + 4 methods = 5 symbols
    assert stats["symbols_count"] == 5
    # Two calls: greet -> helper, create -> factory_helper
    assert stats["calls_count"] == 2


def test_base_class_in_same_file_resolved(tmp_path):
    code = '''
class BaseWorker:
    def common_step(self):
        pass

class CustomWorker(BaseWorker):
    def execute(self):
        self.common_step()
'''
    (tmp_path / "worker.py").write_text(code, encoding="utf-8")
    
    stats = index_codebase(str(tmp_path), graph=None)
    assert stats["files_count"] == 1
    # 2 classes + 2 methods = 4 symbols
    assert stats["symbols_count"] == 4
    # 1 call: CustomWorker.execute -> BaseWorker.common_step
    assert stats["calls_count"] == 1


def test_module_attribute_call_resolved(tmp_path):
    utils_code = '''
def normalize(text):
    return text.strip()
'''
    app_code = '''
from . import utils

def process(data):
    return utils.normalize(data)
'''
    (tmp_path / "utils.py").write_text(utils_code, encoding="utf-8")
    (tmp_path / "app.py").write_text(app_code, encoding="utf-8")
    
    stats = index_codebase(str(tmp_path), graph=None)
    assert stats["files_count"] == 2
    assert stats["symbols_count"] == 2
    assert stats["calls_count"] == 1


def test_overload_stubs_skipped(tmp_path):
    code = '''
from typing import overload

@overload
def process(x: int) -> int: ...

@overload
def process(x: str) -> str: ...

def process(x):
    return x
'''
    (tmp_path / "overloads.py").write_text(code, encoding="utf-8")
    
    stats = index_codebase(str(tmp_path), graph=None)
    assert stats["files_count"] == 1
    # Only 1 symbol: the real implementation, not 3!
    assert stats["symbols_count"] == 1


def test_arbitrary_variable_call_creates_no_edge(tmp_path):
    code = '''
def handle(adapter, req):
    # adapter is an arbitrary argument, not self/cls or an imported module
    adapter.send(req)
'''
    (tmp_path / "handler.py").write_text(code, encoding="utf-8")
    
    stats = index_codebase(str(tmp_path), graph=None)
    assert stats["files_count"] == 1
    assert stats["symbols_count"] == 1
    # Unresolved arbitrary call -> 0 edges
    assert stats["calls_count"] == 0


def test_sample_repo_regression_contract():
    repo_root = Path(__file__).resolve().parent.parent
    sample_dir = repo_root / "sample_repo"
    assert sample_dir.exists(), "sample_repo directory must exist"

    stats = index_codebase(str(sample_dir), graph=None)
    assert stats["files_count"] == 4, f"Expected 4 files, got {stats['files_count']}"
    assert stats["symbols_count"] == 9, f"Expected 9 symbols, got {stats['symbols_count']}"
    assert stats["calls_count"] == 4, f"Expected 4 calls, got {stats['calls_count']}"
