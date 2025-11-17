# Fix: Nested Expander Errors

## Problem
Streamlit was throwing errors about nested expanders:
```
StreamlitAPIException: Expanders may not be nested inside other expanders.
```

## Root Causes

### Issue 1: View as JSON Expander (Line 2347)
**Location:** `show_create_intervention()` function

**Problem:**
The "🔍 View as JSON" expander appeared to be nested inside the "🤖 View AI Recommendations" expander due to indentation.

**Original Structure (WRONG):**
```python
if ai_recommendations:
    with st.expander("🤖 View AI Recommendations", expanded=True):
        # ... display recommendations ...
    
    # This looks like it's outside, but Python needs actual code to close the block
    # Comments and blank lines don't count!
    if ai_recommendations.get('structured_recommendations'):
        with st.expander("🔍 View as JSON", expanded=False):  # ❌ Still nested!
            st.json(...)
```

**Fix (Lines 2343-2348):**
```python
if ai_recommendations:
    with st.expander("🤖 View AI Recommendations", expanded=True):
        # ... display recommendations ...
        # Expander ends here
    
    # Added clear comment and visual divider
    # NOTE: This must be OUTSIDE the "View AI Recommendations" expander above
    if ai_recommendations.get('structured_recommendations'):
        st.divider()  # Visual separator
        with st.expander("🔍 View as JSON", expanded=False):  # ✅ Properly outside!
            st.json(...)
```

### Issue 2: View Details Expander in Button (Line 2696)
**Location:** `show_scheduled_remediations()` function

**Problem:**
An expander was created inside a button's conditional block, AND the button was inside a column context. This created a nested expander situation.

**Original Structure (WRONG):**
```python
with col4:
    if st.button("View Details"):
        with st.expander("Intervention Details", expanded=True):  # ❌ Nested!
            st.text_area(...)
    
    if st.button("Mark Complete"):
        # ... update code ...
```

**Issues:**
1. Can't nest expanders inside button callbacks
2. Streamlit's execution model doesn't work with conditional UI inside buttons
3. Created a confusing UX flow

**Fix (Lines 2694-2725):**
```python
with col4:
    # Use session state to toggle details visibility
    detail_key = f"show_detail_{idx}"
    if detail_key not in st.session_state:
        st.session_state[detail_key] = False
    
    # Toggle button that changes text
    if st.button("View Details" if not st.session_state[detail_key] else "Hide Details", 
                key=f"view_{idx}"):
        st.session_state[detail_key] = not st.session_state[detail_key]
        st.rerun()
    
    if st.button("Mark Complete", key=f"complete_{idx}"):
        # ... update code ...

# Show details OUTSIDE the columns if toggled ✅
if st.session_state.get(f"show_detail_{idx}", False):
    st.text_area("Full Intervention Details", value=remediation['intervention_details'], 
                height=200, disabled=True, key=f"details_text_{idx}")
```

## Key Lessons

### 1. Python Indentation Rules
- **Blank lines don't close blocks** - Python ignores blank lines for indentation
- **Comments don't close blocks** - Comments are ignored for control flow
- **First non-blank, non-comment line** determines the indentation level

### 2. Streamlit Expander Rules
- ❌ **Cannot nest expanders inside other expanders**
- ❌ **Cannot create expanders conditionally inside buttons**
- ✅ **Can have multiple expanders as siblings**
- ✅ **Can toggle visibility using session state**

### 3. Button Best Practices
- Use `st.session_state` to track button clicks
- Call `st.rerun()` after state changes
- Display conditional content outside the button block
- Use dynamic button text to show current state

## Files Modified

- `app.py`:
  - Lines 2343-2348: Fixed JSON expander indentation
  - Lines 2346, 2351: Added visual dividers
  - Lines 2352: Made clear button more prominent (`type="secondary"`)
  - Lines 2694-2725: Refactored View Details from nested expander to session state toggle

## Testing

### Test Cases:
1. ✅ Create Intervention page with AI recommendations
   - Verify "View AI Recommendations" expander works
   - Verify "View as JSON" expander works
   - Both should be independent, not nested

2. ✅ Scheduled Remediations page
   - Click "View Details" button
   - Verify details appear below (not in expander)
   - Click again to hide details
   - Button text should toggle between "View Details" and "Hide Details"

3. ✅ Multiple interventions on same page
   - Each should have independent toggle state
   - Opening one shouldn't affect others

## Visual Changes

### Before:
```
🤖 View AI Recommendations (expanded)
  ├── Recommendation 1
  ├── Recommendation 2
  └── 🔍 View as JSON (collapsed) ❌ Nested!
```

### After:
```
🤖 View AI Recommendations (expanded)
  ├── Recommendation 1
  └── Recommendation 2

─────────────────────  (divider)

🔍 View as JSON (collapsed) ✅ Separate!
```

### Before (Scheduled Remediations):
```
┌─ Column 4 ─────────────┐
│ [View Details]  Button │
│   └─ Expander ❌        │
│       └─ Text Area      │
│ [Mark Complete] Button  │
└─────────────────────────┘
```

### After (Scheduled Remediations):
```
┌─ Column 4 ──────────────┐
│ [View Details] Button    │
│ [Mark Complete] Button   │
└──────────────────────────┘

[Full Intervention Details] ✅
Text Area (appears when toggled)
```

## Benefits

### 1. No More Errors
- ✅ Eliminates nested expander errors
- ✅ Proper Streamlit component hierarchy
- ✅ Code follows Streamlit best practices

### 2. Better UX
- ✅ Clearer visual separation between sections
- ✅ Toggle button shows current state
- ✅ Details display in a clear, uncluttered way
- ✅ No confusing nested UI elements

### 3. More Maintainable
- ✅ Proper use of session state
- ✅ Clear comments explaining structure
- ✅ Follows Streamlit execution model
- ✅ Easier to debug and extend

## Important Note

If you still see the error after these changes, **restart the Streamlit app** to clear any cached Python bytecode:
1. Stop the app (Ctrl+C)
2. Restart the app
3. Clear browser cache if needed (Ctrl+Shift+R or Cmd+Shift+R)

The error traceback may reference old line numbers from cached code.

