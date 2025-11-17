# Streaming Implementation for AI Recommendations

## Overview
This document describes the implementation of real-time streaming for the Multi-Agent Supervisor (MAS) endpoint in the student remediation app.

## Changes Made

### 1. New File: `model_serving_utils.py`
Created a utility module for handling model serving endpoint calls with streaming support:

- **`_get_endpoint_task_type()`**: Determines the endpoint type (agent/v1/responses or chat/completions)
- **`_convert_to_responses_format()`**: Converts chat messages to ResponsesAgent API format
- **`query_endpoint_stream()`**: Main streaming function that yields events as they arrive
- **`_query_responses_endpoint_stream()`**: Handles streaming for ResponsesAgent endpoints using MLflow deployment client

### 2. Updated: `app.py`

#### New Imports
- `from model_serving_utils import query_endpoint_stream, _get_endpoint_task_type`
- `from mlflow.types.responses import ResponsesAgentStreamEvent`

#### New Functions

**`generate_recommendations_streaming(student_data, response_area)`**
- Generates recommendations with real-time streaming display
- Processes streaming events from the MAS endpoint
- Handles three event types:
  - `message`: Final text content from the AI
  - `function_call`: Tool/function calls made by the AI
  - `function_call_output`: Results from tool executions
- Returns a structured dictionary with:
  - `llm_recommendations`: Formatted recommendation text
  - `structured_recommendations`: Parsed JSON recommendations
  - `tool_calls`: List of all tool calls with inputs/outputs
  - `all_messages`: Complete message history for replay
  - `student_context`: Student data used for the recommendation

**`render_streaming_message(msg)`**
- Renders individual streaming messages in chat format
- Handles assistant messages with content
- Displays tool calls with collapsible input sections
- Shows tool results with collapsible output sections
- Uses appropriate avatars (✨ for responses, 🔧 for tools, 📊 for results)

#### Updated Functions

**`show_ai_recommendations_page()`**
- Now uses `generate_recommendations_streaming()` instead of batch generation
- Displays streaming events in real-time as they arrive
- Shows a "View Full Results" button after streaming completes
- Results page displays all messages from the streaming session
- Maintains backward compatibility with fallback to old display method

### 3. Updated: `requirements.txt`
Added `mlflow>=2.10.0` for MLflow streaming types and deployment client.

## How It Works

### Streaming Flow

1. **User clicks "Generate AI Recommendations"**
   - Sets `ai_recommendations_generating = True`
   - Triggers a rerun to show streaming UI

2. **Streaming begins**
   - Creates a `response_area` container for live updates
   - Calls `generate_recommendations_streaming()` with student data
   - Function starts streaming from the MAS endpoint

3. **Events are processed in real-time**
   - Each event from the endpoint is validated using `ResponsesAgentStreamEvent`
   - Function calls are displayed as they're made
   - Tool results are shown as they're received
   - Final recommendations are accumulated

4. **Streaming completes**
   - All messages are stored in `st.session_state`
   - User can view the complete conversation
   - "Create Intervention" button uses the final recommendations

### Message Types

The implementation handles three main message types from the MAS endpoint:

#### 1. Function Call (Tool Use)
```json
{
  "type": "function_call",
  "call_id": "call_123",
  "name": "get_historical_data",
  "arguments": "{\"student_id\": 12345}"
}
```

Rendered as:
```
🔧 Calling Tool: `get_historical_data`
📥 Input (collapsible)
  { "student_id": 12345 }
```

#### 2. Function Call Output (Tool Result)
```json
{
  "type": "function_call_output",
  "call_id": "call_123",
  "output": "{ ... historical data ... }"
}
```

Rendered as:
```
📊 Tool Result
📤 Output (collapsible)
  { ... historical data ... }
```

#### 3. Message (AI Response)
```json
{
  "type": "message",
  "content": [
    {
      "type": "output_text",
      "text": "Based on the analysis..."
    }
  ]
}
```

Rendered as:
```
✨ Based on the analysis...
```

## Benefits

1. **Transparency**: Users can see the AI's reasoning process in real-time
2. **Trust**: Viewing tool calls and data retrieval builds confidence in recommendations
3. **Debugging**: Easier to identify issues with data retrieval or agent logic
4. **User Experience**: Engaging interface that shows progress rather than a loading spinner
5. **Auditability**: Complete message history is preserved for review

## Testing

To test the streaming implementation:

1. Navigate to "Student Risk Dashboard"
2. Click "🤖 AI Rec" for any student
3. Click "✨ Generate AI Recommendations"
4. Observe:
   - Tool calls appearing as they're made
   - Tool results showing data retrieved
   - Final recommendations displaying after all processing

Expected behavior:
- You should see multiple tool calls (handoffs, data retrieval, etc.)
- Each tool call should have an input section
- Each tool result should have an output section
- Final recommendations should appear last with the ✨ avatar

## Architecture

```
User Action
    ↓
show_ai_recommendations_page()
    ↓
generate_recommendations_streaming()
    ↓
query_endpoint_stream() (from model_serving_utils)
    ↓
MLflow Deployment Client
    ↓
Databricks Serving Endpoint (MAS)
    ↓
[Streaming Events]
    ↓
ResponsesAgentStreamEvent parsing
    ↓
render_streaming_message() for each event
    ↓
Display in Streamlit UI
```

## Error Handling

The implementation includes comprehensive error handling:

1. **Streaming errors**: Falls back to error message in structured format
2. **JSON parsing errors**: Handles non-JSON responses gracefully
3. **Missing events**: Continues processing even if some events are malformed
4. **Network issues**: Logs detailed error information for debugging

## Future Enhancements

Potential improvements for future iterations:

1. **Thinking stages**: Add support for explicit thinking/reasoning events
2. **Streaming text**: Character-by-character streaming of final responses
3. **Real-time metrics**: Show token usage, latency, etc.
4. **Pause/Resume**: Allow users to pause streaming if needed
5. **Export transcript**: Download the full conversation as JSON or markdown

