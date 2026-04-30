"""ElicitationApprovalHandler: bridges MCP elicitation to apcore's approval system."""

from __future__ import annotations

import json
import logging

from apcore.approval import ApprovalHandler, ApprovalRequest, ApprovalResult

from apcore_mcp.helpers import MCP_ELICIT_KEY

logger = logging.getLogger(__name__)


class ElicitationApprovalHandler(ApprovalHandler):
    """Bridges MCP elicitation to apcore's approval system.

    Uses the MCP elicit callback (injected into Context.data) to present
    approval requests to the human user via the MCP client.
    """

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        """Request approval via MCP elicitation.

        Extracts the elicit callback from ``request.context.data``, builds
        an approval message, and maps the elicit response to an
        ``ApprovalResult``.

        Args:
            request: The approval request containing module_id, description,
                arguments, and context.

        Returns:
            ApprovalResult with status "approved" or "rejected".
        """
        # Extract elicit callback from context
        context = request.context
        data = getattr(context, "data", None) if context is not None else None
        if data is None:
            return ApprovalResult(status="rejected", reason="No context available for elicitation")

        elicit_callback = data.get(MCP_ELICIT_KEY)
        if elicit_callback is None:
            return ApprovalResult(status="rejected", reason="No elicitation callback available")

        # Build approval message
        message = (
            f"Approval required for tool: {request.module_id}\n\n"
            f"{request.description}\n\n"
            f"Arguments: {json.dumps(request.arguments)}"
        )

        try:
            result = await elicit_callback(message)
        except Exception:
            logger.debug("Elicitation approval request failed", exc_info=True)
            return ApprovalResult(status="rejected", reason="Elicitation request failed")

        if result is None:
            return ApprovalResult(status="rejected", reason="Elicitation returned no response")

        action = result.get("action") if isinstance(result, dict) else getattr(result, "action", None)

        if action == "accept":
            return ApprovalResult(status="approved")
        else:
            return ApprovalResult(status="rejected", reason=f"User action: {action}")

    async def check_approval(self, approval_id: str) -> ApprovalResult:
        """Check status of an existing approval.

        Phase B (async polling) is not supported via MCP elicitation since
        elicitation is stateless.

        Args:
            approval_id: The approval ID to check.

        Returns:
            Always returns rejected since Phase B is not supported.
        """
        return ApprovalResult(status="rejected", reason="Phase B not supported via MCP elicitation")
