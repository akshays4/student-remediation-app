# Fix: Infinite Loop in AI Recommendations Streaming

## Problem
The AI recommendations page was caught in an infinite loop, continuously making requests to the MAS endpoint. Logs showed dropped connections and repeated requests happening without stopping.

## Root Causes

### 1. **Excessive UI Updates During Streaming**
```python
# BEFORE (WRONG)
for raw_event in query_endpoint_stream(SERVING_ENDPOINT, messages):
    response_area.empty()  # ❌ Called on EVERY event
    # ... process event ...
    if all_messages:
        # Re-render ALL messages on EVERY event ❌
        with response_area.container():
            for msg in all_messages:
                render_streaming_message(msg)
```

**Issues:**
- `response_area.empty()` called on every single streaming event
- All accumulated messages re-rendered on every event
- Could trigger Streamlit reruns continuously
- Excessive UI updates caused performance issues

### 2. **No Safeguard for Generating Flag**
```python
# BEFORE (WRONG)
student_data = st.session_state.ai_rec_student_data
recommendations = generate_recommendations_streaming(student_data, response_area)
st.session_state.ai_recommendations_generating = False  # ❌ Only if successful
st.rerun()
```

**Issues:**
- If streaming failed, `generating` flag stayed `True`
- Next rerun would trigger another streaming attempt
- Created infinite loop of failed attempts

### 3. **No Progress Tracking**
- No logging to track how many events were processed
- Couldn't tell if loop was stuck or just processing many events
- No visibility into streaming progress

## Solutions Implemented

### 1. **Render Only After Streaming Completes**
```python
# AFTER (CORRECT)
for raw_event in query_endpoint_stream(SERVING_ENDPOINT, messages):
    # No response_area.empty() here ✅
    # ... process and accumulate events ...
    # Don't render yet ✅

# After loop completes, render once ✅
logger.info(f"Streaming complete. Rendering {len(all_messages)} messages")
if all_messages:
    with response_area.container():
        for msg in all_messages:
            render_streaming_message(msg)
```

**Benefits:**
- UI only updates once after streaming completes
- No intermediate renders during streaming
- Prevents Streamlit from triggering unnecessary reruns
- Much better performance

**Location:** Lines 1702-1716

### 2. **Added Try-Finally Block for Flag Management**
```python
# AFTER (CORRECT)
try:
    recommendations = generate_recommendations_streaming(student_data, response_area)
    st.session_state.ai_recommendations_data = recommendations
    st.success("✅ Analysis complete!")
except Exception as gen_error:
    logger.error(f"Error generating recommendations: {gen_error}")
    st.error(f"❌ Error generating recommendations: {str(gen_error)}")
    st.session_state.ai_recommendations_data = None
finally:
    # Always set generating to False to prevent infinite loops ✅
    st.session_state.ai_recommendations_generating = False
    st.rerun()
```

**Benefits:**
- `generating` flag ALWAYS reset to `False`, even on error
- Prevents infinite loop from stuck flag
- Shows error message to user
- Logs error for debugging

**Location:** Lines 2101-2118

### 3. **Added Progress Logging**
```python
# AFTER (CORRECT)
task_type = _get_endpoint_task_type(SERVING_ENDPOINT)
logger.info(f"Starting streaming from endpoint: {SERVING_ENDPOINT}, task_type: {task_type}")

event_count = 0
for raw_event in query_endpoint_stream(SERVING_ENDPOINT, messages):
    event_count += 1
    if event_count % 10 == 0:
        logger.debug(f"Processed {event_count} streaming events, {len(all_messages)} messages accumulated")
```

**Benefits:**
- Tracks how many events are processed
- Logs progress every 10 events
- Helps identify if truly stuck or just processing many events
- Logs final count when complete

**Location:** Lines 1621-1627

## Comparison

### Before Fix:
```
┌─────────────────────────────┐
│ Start Streaming             │
└─────────────────────────────┘
           ↓
┌─────────────────────────────┐
│ For each event:             │
│ 1. Empty response area ❌    │
│ 2. Process event            │
│ 3. Re-render ALL messages ❌│
│ 4. Trigger UI update        │
└─────────────────────────────┘
           ↓ (hundreds of times)
┌─────────────────────────────┐
│ If error: flag stays True ❌│
│ → Rerun → Start again ❌     │
│ → INFINITE LOOP ❌           │
└─────────────────────────────┘
```

### After Fix:
```
┌─────────────────────────────┐
│ Start Streaming             │
│ Log: "Starting streaming"   │
└─────────────────────────────┘
           ↓
┌─────────────────────────────┐
│ For each event:             │
│ 1. Process event ✅          │
│ 2. Accumulate message ✅     │
│ 3. Log every 10 events ✅    │
│ (No rendering yet) ✅        │
└─────────────────────────────┘
           ↓ (once after all events)
┌─────────────────────────────┐
│ Streaming complete          │
│ Log: "Rendering X messages" │
│ Render all messages ONCE ✅  │
└─────────────────────────────┘
           ↓
┌─────────────────────────────┐
│ Try-Finally block           │
│ Always reset flag to False ✅│
│ Show results or error ✅     │
│ Single rerun ✅              │
└─────────────────────────────┘
```

## Log Output Now

### Expected logs during normal streaming:
```
INFO: Starting streaming from endpoint: mas-53985057-endpoint, task_type: agent/v1/responses
DEBUG: Processed 10 streaming events, 3 messages accumulated
DEBUG: Processed 20 streaming events, 7 messages accumulated
DEBUG: Processed 30 streaming events, 12 messages accumulated
INFO: Streaming complete. Rendering 15 messages
DEBUG: Rendering tool result for call_id toolu_xxx, tool: knowledge-assistant
INFO: Extracted 3 recommendations from structured data
```

### If error occurs:
```
INFO: Starting streaming from endpoint: mas-53985057-endpoint, task_type: agent/v1/responses
DEBUG: Processed 10 streaming events, 3 messages accumulated
ERROR: Error in streaming recommendations: Connection timeout
DEBUG: Traceback: ...
ERROR: Error generating recommendations: Connection timeout
# Flag set to False, stops loop ✅
```

## Testing

### Test Scenarios:
1. ✅ **Normal streaming**: Should process all events, render once at end
2. ✅ **Long streaming (100+ events)**: Should log progress every 10 events
3. ✅ **Network error during streaming**: Should catch error, reset flag, show error
4. ✅ **Dropped connection**: Should handle gracefully, not loop infinitely
5. ✅ **Empty response**: Should return empty recommendations, not crash

### How to Verify Fix:
1. Check logs for "Starting streaming" (should appear only once per generation)
2. Check for progress logs every 10 events
3. Check for "Streaming complete" log
4. Verify page doesn't make repeated requests
5. If error occurs, verify flag is reset and no loop happens

## Performance Improvements

### Before:
- 100 streaming events = 100 UI updates
- All messages re-rendered 100 times
- Slow, causes lag and potential reruns

### After:
- 100 streaming events = 1 UI update (at end)
- All messages rendered 1 time
- Fast, smooth user experience

## Files Modified

- `app.py`:
  - Line 1623: Removed `response_area.empty()` from loop
  - Lines 1621-1627: Added progress logging
  - Lines 1702-1716: Moved rendering outside loop (after streaming completes)
  - Lines 2101-2118: Added try-finally block for flag management

## Prevention

This fix prevents:
- ❌ Infinite loops from stuck generating flag
- ❌ Excessive UI updates during streaming
- ❌ Streamlit reruns triggered by intermediate renders
- ❌ Performance issues from re-rendering all messages repeatedly
- ❌ Silent failures with no error messages

And provides:
- ✅ Single render after streaming completes
- ✅ Guaranteed flag reset on success or error
- ✅ Progress logging for debugging
- ✅ Error messages shown to user
- ✅ Smooth, fast streaming experience

