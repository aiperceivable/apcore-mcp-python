"""Adapters: schema conversion, annotation mapping, error mapping, ID normalization, approval, formatter."""

from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.approval import ElicitationApprovalHandler
from apcore_mcp.adapters.errors import ErrorMapper
from apcore_mcp.adapters.formatter import MCPErrorFormatter, register_mcp_formatter
from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer

__all__ = [
    "AnnotationMapper",
    "ElicitationApprovalHandler",
    "ErrorMapper",
    "MCPErrorFormatter",
    "ModuleIDNormalizer",
    "register_mcp_formatter",
]
