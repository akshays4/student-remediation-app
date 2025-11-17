from mlflow.deployments import get_deploy_client
from databricks.sdk import WorkspaceClient
import json
import uuid
import logging

logging.basicConfig(
    format="%(levelname)s [%(asctime)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG
)

def _get_endpoint_task_type(endpoint_name: str) -> str:
    """Get the task type of a serving endpoint."""
    try:
        w = WorkspaceClient()
        ep = w.serving_endpoints.get(endpoint_name)
        return ep.task if ep.task else "chat/completions"
    except Exception:
        return "chat/completions"

def _convert_to_responses_format(messages):
    """Convert chat messages to ResponsesAgent API format."""
    input_messages = []
    for msg in messages:
        if msg["role"] == "user":
            input_messages.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            # Handle assistant messages with tool calls
            if msg.get("tool_calls"):
                # Add function calls
                for tool_call in msg["tool_calls"]:
                    input_messages.append({
                        "type": "function_call",
                        "id": tool_call["id"],
                        "call_id": tool_call["id"],
                        "name": tool_call["function"]["name"],
                        "arguments": tool_call["function"]["arguments"]
                    })
                # Add assistant message if it has content
                if msg.get("content"):
                    input_messages.append({
                        "type": "message",
                        "id": msg.get("id", str(uuid.uuid4())),
                        "content": [{"type": "output_text", "text": msg["content"]}],
                        "role": "assistant"
                    })
            else:
                # Regular assistant message
                input_messages.append({
                    "type": "message",
                    "id": msg.get("id", str(uuid.uuid4())),
                    "content": [{"type": "output_text", "text": msg["content"]}],
                    "role": "assistant"
                })
        elif msg["role"] == "tool":
            input_messages.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id"),
                "output": msg["content"]
            })
    return input_messages

def query_endpoint_stream(endpoint_name: str, messages: list[dict[str, str]]):
    """Stream responses from serving endpoint."""
    logger = logging.getLogger(__name__)
    task_type = _get_endpoint_task_type(endpoint_name)
    
    if task_type == "agent/v1/responses":
        return _query_responses_endpoint_stream(endpoint_name, messages)
    else:
        return _query_chat_endpoint_stream(endpoint_name, messages)

def _query_chat_endpoint_stream(endpoint_name: str, messages: list[dict[str, str]]):
    """Invoke an endpoint that implements chat completions and stream the response"""
    logger = logging.getLogger(__name__)
    client = get_deploy_client("databricks")

    # Prepare input payload
    inputs = {
        "messages": messages,
        "databricks_options": {"return_trace": True}
    }

    try:
        for chunk in client.predict_stream(endpoint=endpoint_name, inputs=inputs):
            if "choices" in chunk:
                yield chunk
            elif "delta" in chunk:
                yield chunk
    except Exception as e:
        logger.error(
            f"Error in streaming chat endpoint call:\n"
            f"Endpoint: {endpoint_name}\n"
            f"Error: {str(e)}"
        )
        raise

def _query_responses_endpoint_stream(endpoint_name: str, messages: list[dict[str, str]]):
    """Stream responses from agent/v1/responses endpoints using MLflow deployments client."""
    logger = logging.getLogger(__name__)
    client = get_deploy_client("databricks")
    
    input_messages = _convert_to_responses_format(messages)
    
    # Prepare input payload for ResponsesAgent
    inputs = {
        "input": input_messages,
        "context": {},
        "stream": True,
        "databricks_options": {"return_trace": True}
    }

    try:
        for event_data in client.predict_stream(endpoint=endpoint_name, inputs=inputs):
            # Just yield the raw event data, let caller handle the parsing
            yield event_data
    except Exception as e:
        logger.error(
            f"Error in streaming responses endpoint call:\n"
            f"Endpoint: {endpoint_name}\n"
            f"Error: {str(e)}"
        )
        raise

