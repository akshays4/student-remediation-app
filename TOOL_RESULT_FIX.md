# Tool Result Display Fix

## Problem
Tool results from Genie (space table queries) and Knowledge Assistant were not appearing in the tool result dropdown during streaming. Users would see the tool being called but no output data.

## Root Cause
1. **Nested Data Structures**: Tool results from MAS endpoints often come wrapped in nested structures with keys like `content`, `result`, `data`, or `output`
2. **Type Detection Issues**: The simple JSON detection (checking if string starts with `{` or `[`) wasn't handling all cases
3. **Limited Logging**: No visibility into what data was actually being received
4. **Missing Tool Names**: Tool results weren't showing which tool they came from

## Solution

### 1. Added Content Extraction Function
Created `extract_tool_result_content()` that intelligently unwraps nested tool result structures:

```python
def extract_tool_result_content(tool_content):
    """Extract the actual content from potentially nested tool result structures."""
    # Handle common nested formats from Genie and Knowledge Assistant
    if isinstance(tool_content, dict):
        # Check for common wrapper keys
        if "content" in tool_content:
            return tool_content["content"]
        elif "result" in tool_content:
            return tool_content["result"]
        elif "data" in tool_content:
            return tool_content["data"]
        elif "output" in tool_content:
            return tool_content["output"]
        return tool_content
    # ... additional handling
```

### 2. Enhanced Tool Name Tracking
Now tracks which tool produced each result:

```python
# Find the corresponding tool call to get the name
tool_name = "Unknown Tool"
for tool_call in tool_calls:
    if tool_call.get('call_id') == call_id:
        tool_name = tool_call.get('tool', 'Unknown Tool')
        break

all_messages.append({
    "role": "tool",
    "content": output,
    "tool_call_id": call_id,
    "call_id": call_id,
    "tool_name": tool_name  # Include tool name for display
})
```

### 3. Improved Rendering Logic
Enhanced `render_streaming_message()` with:

- **Better type handling**: Checks for dict, list, and string types explicitly
- **Improved JSON detection**: Strips whitespace before checking format
- **Smart text display**: Uses `st.text()` for short content, `st.text_area()` for long content
- **Tool name display**: Shows which tool the result came from
- **Debug section**: Optional "Raw Response" expander when content is extracted/transformed

### 4. Added Comprehensive Logging
```python
logger.debug(f"Tool output for call_id {call_id}: {str(output)[:500]}")
logger.debug(f"Rendering tool result for call_id {call_id}, tool: {tool_name}")
logger.debug(f"Raw content type: {type(tool_content_raw)}, length: {len(str(tool_content_raw))}")
logger.debug(f"Extracted content type: {type(tool_content)}, length: {len(str(tool_content))}")
```

## What Users See Now

### Before
```
🔧 Calling Tool: genie_space_table
📥 Input (collapsed)

📊 Tool Result
📤 Output (collapsed - EMPTY)
```

### After
```
🔧 Calling Tool: genie_space_table
📥 Input (collapsed)
  { "query": "SELECT * FROM students WHERE risk='High'" }

📊 Tool Result: genie_space_table
📤 Output (collapsed)
  [
    { "student_id": 123, "name": "John Doe", "gpa": 2.1 },
    { "student_id": 456, "name": "Jane Smith", "gpa": 2.3 }
  ]

🔍 Raw Response (Debug) (optional, if content was extracted)
```

## Content Type Handling

The improved code now handles:

1. **Direct JSON objects/arrays**: `st.json()` for interactive viewing
2. **JSON strings**: Parses and displays with `st.json()`
3. **Plain text (short)**: `st.text()` for readability
4. **Plain text (long)**: Scrollable `st.text_area()` with 300px height
5. **Empty/null**: Warning message with debug section
6. **Nested structures**: Extracts content from common wrapper patterns

## Common Tool Result Formats Supported

### Genie Space Table
```json
{
  "output": [
    { "column1": "value1", "column2": "value2" }
  ]
}
```

### Knowledge Assistant
```json
{
  "result": {
    "content": "Retrieved information...",
    "sources": [...]
  }
}
```

### Direct Results
```json
[
  { "data": "value" }
]
```

## Debugging

If tool results still don't appear:

1. **Check logs**: Look for `logger.debug` messages with "Tool output for call_id"
2. **Expand "Raw Response"**: The debug section shows what was actually received
3. **Verify tool implementation**: Ensure the tool is returning data in the expected format
4. **Check permissions**: Ensure the app has access to the data sources (Genie, KA)

## Testing

To verify the fix works:

1. Click "🤖 AI Rec" on any student
2. Click "✨ Generate AI Recommendations"
3. Watch the streaming display
4. Each tool call should be followed by a tool result with actual data
5. Expand the "📤 Output" section to see the content
6. Verify Genie table results show student data
7. Verify Knowledge Assistant results show retrieved documents

## Files Modified

- `app.py`:
  - Added `extract_tool_result_content()` function (lines 1772-1799)
  - Enhanced tool result tracking in streaming (lines 1689-1702)
  - Improved `render_streaming_message()` (lines 1832-1899)
  - Added debug logging throughout

