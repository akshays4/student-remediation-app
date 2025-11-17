# Fix: AI Recommendations Page Going Blank After Knowledge Assistant Tool

## Problem
The AI recommendations page would go blank after receiving results from the knowledge assistant tool during streaming, causing the entire interface to crash without showing any error messages to the user.

## Root Cause
1. **No error handling in rendering loop**: When a message failed to render (due to large/malformed data), the entire page would crash
2. **Streamlit key collisions**: Multiple tool results could have the same key, causing Streamlit to fail silently
3. **Unhandled exceptions**: Errors in rendering weren't caught, causing the entire function to fail
4. **No fallback display**: When content couldn't be parsed, there was no graceful degradation

## Solution Implemented

### 1. Added Error Handling to Streaming Loop
```python
# Lines 1706-1717
if all_messages:
    try:
        with response_area.container():
            for msg in all_messages:
                try:
                    render_streaming_message(msg)
                except Exception as render_error:
                    logger.error(f"Error rendering message: {render_error}")
                    logger.debug(f"Problematic message: {str(msg)[:500]}")
                    # Continue rendering other messages
    except Exception as container_error:
        logger.error(f"Error with response container: {container_error}")
        # Don't crash, just log it
```

**Benefits:**
- Individual message failures don't crash entire page
- Errors are logged for debugging
- Rendering continues for other messages

### 2. Added Unique Keys for Tool Results
```python
# Lines 1943-1945
import hashlib
unique_key = hashlib.md5(f"{call_id}_{tool_name}".encode()).hexdigest()[:8]
```

**Usage:**
```python
st.text_area("Output", tool_content, key=f"tool_output_{unique_key}_1")
```

**Benefits:**
- Prevents Streamlit key collision errors
- Each tool result gets a unique identifier
- Multiple knowledge assistant calls won't conflict

### 3. Wrapped Tool Rendering in Try-Except
```python
# Lines 1936-2038
elif msg["role"] == "tool":
    try:
        # ... tool rendering code ...
    except Exception as tool_render_error:
        logger.error(f"Error rendering tool result: {tool_render_error}")
        with st.chat_message("assistant", avatar="⚠️"):
            st.error(f"Error displaying tool result: {tool_render_error}")
            with st.expander("Debug Info", expanded=False):
                st.code(str(msg)[:500])
```

**Benefits:**
- Tool rendering errors don't crash page
- User sees informative error message
- Debug info available for troubleshooting

### 4. Added Top-Level Error Handler
```python
# Lines 2040-2047
except Exception as msg_error:
    logger.error(f"Error in render_streaming_message: {msg_error}")
    with st.chat_message("assistant", avatar="⚠️"):
        st.error("Error rendering message")
        with st.expander("Debug", expanded=False):
            st.code(str(msg)[:500])
```

**Benefits:**
- Catches any unexpected errors
- Prevents complete page crash
- Provides debugging information

### 5. Enhanced JSON Parsing Error Handling
```python
# Lines 1989-2010
try:
    if content_stripped.startswith('{') or content_stripped.startswith('['):
        content_dict = json.loads(content_stripped)
        st.json(content_dict)
    else:
        # Plain text display
except json.JSONDecodeError:
    # Fallback to text display
except Exception as json_error:
    logger.warning(f"Error parsing tool content: {json_error}")
    st.warning("⚠️ Could not parse tool output")
    st.code(str(tool_content)[:500])
```

**Benefits:**
- Handles JSON parsing failures gracefully
- Shows partial content even if parsing fails
- User still sees tool output in some form

### 6. Added Assistant Content Error Handling
```python
# Lines 1884-1912
try:
    parsed = parse_agent_tags(msg["content"])
    # ... render content ...
except Exception as content_error:
    logger.warning(f"Error rendering assistant content: {content_error}")
    with st.chat_message("assistant", avatar="✨"):
        st.text(str(msg.get("content", ""))[:500])
```

**Benefits:**
- Assistant message errors don't crash page
- Falls back to plain text display
- Preserves at least some of the content

## Error Scenarios Handled

### Before Fix:
| Scenario | Result |
|----------|--------|
| Large tool output | ❌ Page crashes |
| Malformed JSON | ❌ Page goes blank |
| Key collision | ❌ Silent failure |
| Parse error | ❌ Complete crash |
| Unknown content type | ❌ No display |

### After Fix:
| Scenario | Result |
|----------|--------|
| Large tool output | ✅ Shows in scrollable area |
| Malformed JSON | ✅ Shows as text with warning |
| Key collision | ✅ Unique keys prevent collision |
| Parse error | ✅ Fallback to text display |
| Unknown content type | ✅ Converts to string & displays |

## What Users See Now

### When Tool Rendering Succeeds:
```
📊 Tool Result: knowledge-assistant
📤 Output
  [Tool content displayed nicely]
```

### When Tool Rendering Fails:
```
⚠️ Error displaying tool result: [error message]

Debug Info (expandable)
  [Message details for troubleshooting]
```

### When Content Can't Be Parsed:
```
⚠️ Could not parse tool output
[First 500 characters of raw content]
```

## Logging Added

All errors are now logged with details:
```python
logger.error(f"Error rendering message: {render_error}")
logger.debug(f"Problematic message: {str(msg)[:500]}")
logger.error(f"Error rendering tool result: {tool_render_error}")
logger.error(f"Tool: {msg.get('tool_name', 'unknown')}, Call ID: {msg.get('call_id', 'unknown')}")
logger.warning(f"Error parsing tool content: {json_error}")
logger.warning(f"Error showing raw response: {debug_error}")
```

## Testing

### Test Scenarios:
1. ✅ **Normal knowledge assistant response**: Should display cleanly in JSON format
2. ✅ **Large response (>1000 chars)**: Should show in scrollable text area
3. ✅ **Multiple tool calls**: Each should have unique key, no collisions
4. ✅ **Malformed JSON**: Should show warning + text fallback
5. ✅ **Unknown content type**: Should convert to string and display
6. ✅ **Rendering error**: Should show error message, not crash page

### How to Test:
1. Click "🤖 AI Rec" on a student
2. Click "✨ Generate AI Recommendations"
3. Watch streaming:
   - All tool calls should render
   - Knowledge assistant results should display
   - Page should not go blank
   - Any errors should show warning messages

## Files Modified

- `app.py`:
  - Lines 1704-1717: Added error handling to streaming loop
  - Lines 1878-2047: Wrapped entire `render_streaming_message` function with error handling
  - Lines 1936-2038: Added try-except for tool rendering
  - Lines 1943-1945: Added unique key generation
  - Lines 1989-2010: Enhanced JSON parsing with error handling
  - Lines 1884-1912: Added error handling for assistant content

## Prevention

This fix prevents:
- ❌ Page crashes from rendering errors
- ❌ Silent failures from key collisions
- ❌ Complete loss of data when parsing fails
- ❌ User frustration from blank screens

And provides:
- ✅ Graceful error messages
- ✅ Debugging information
- ✅ Partial content display when possible
- ✅ Comprehensive logging

## Debugging

If issues persist:
1. Check browser console for logs
2. Look for "Error rendering" messages in app logs
3. Check "Debug Info" expanders for problematic messages
4. Verify tool outputs are valid JSON or text
5. Check for unique_key generation issues

