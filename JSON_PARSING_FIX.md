# Fix: JSON Parsing from Markdown Code Blocks

## Problem
The AI agent successfully generated structured recommendations in JSON format, but the UI showed:
> ⚠️ AI analysis completed but no structured recommendations were generated. Try regenerating or check the logs.

### Root Cause
The agent was returning the JSON wrapped in a **markdown code block**:

```json
{
  "recommendations": [
    {
      "intervention_type": "Tutoring Referral",
      "modality": "In-Person",
      "priority": "High",
      ...
    }
  ]
}
```

But the parsing logic was expecting **raw JSON** without markdown formatting:

```python
# BEFORE (WRONG)
structured_data = json.loads(final_content)  # ❌ Fails if content has ```json ... ```
```

This caused `json.loads()` to fail because it tried to parse the markdown code fence markers along with the JSON, resulting in a `JSONDecodeError`.

## Solution Implemented

### 1. Extract JSON from Markdown Code Blocks

Added logic to detect and extract JSON from markdown code fences before parsing:

```python
# AFTER (CORRECT)
# Try to extract JSON from markdown code block if present
json_content = final_content.strip()

# Check for markdown code fence (```json ... ``` or ``` ... ```)
import re
code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
code_block_match = re.search(code_block_pattern, json_content, re.DOTALL)

if code_block_match:
    json_content = code_block_match.group(1).strip()
    logger.debug("Extracted JSON from markdown code block")

# Also check for inline code blocks (` ... `)
elif json_content.startswith('`') and json_content.endswith('`'):
    json_content = json_content.strip('`').strip()
    logger.debug("Extracted JSON from inline code block")

logger.debug(f"Attempting to parse JSON content (first 500 chars): {json_content[:500]}")
structured_data = json.loads(json_content)
```

**Location:** Lines 1738-1756

### 2. Enhanced Debug Logging

Added comprehensive logging to track JSON extraction and parsing:

#### In `generate_recommendations_streaming`:
```python
logger.debug(f"Attempting to parse JSON content (first 500 chars): {json_content[:500]}")
structured_data = json.loads(json_content)
logger.debug(f"Parsed structured data: {json.dumps(structured_data, indent=2)[:500]}")

if isinstance(structured_data, dict):
    recommendations = structured_data.get("recommendations", [])
    logger.debug(f"Structured data is dict. Keys: {structured_data.keys()}")
elif isinstance(structured_data, list):
    recommendations = structured_data
    logger.debug("Structured data is list")
else:
    recommendations = []
    logger.warning(f"Structured data is unexpected type: {type(structured_data)}")

logger.info(f"Extracted {len(recommendations)} recommendations from structured data")
if len(recommendations) > 0:
    logger.debug(f"First recommendation: {recommendations[0]}")
```

**Location:** Lines 1755-1771

#### In `show_ai_recommendations_page`:
```python
# Debug logging
logger.debug(f"Displaying recommendations. Keys: {recommendations.keys()}")
logger.debug(f"Structured recommendations count: {len(recommendations.get('structured_recommendations', []))}")
logger.debug(f"Structured recommendations: {recommendations.get('structured_recommendations', [])}")
```

**Location:** Lines 2147-2150

## How It Works

### Before Fix:
```
┌─────────────────────────────────────┐
│ Agent returns:                      │
│ ```json                             │
│ { "recommendations": [...] }        │
│ ```                                 │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ json.loads(final_content) ❌         │
│ → JSONDecodeError                   │
│ → Falls back to text format         │
│ → structured_recommendations = []   │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ UI checks:                          │
│ len(structured_recommendations) > 0 │
│ → False ❌                           │
│ → Shows warning message ❌           │
└─────────────────────────────────────┘
```

### After Fix:
```
┌─────────────────────────────────────┐
│ Agent returns:                      │
│ ```json                             │
│ { "recommendations": [...] }        │
│ ```                                 │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ Regex: r'```(?:json)?\s*\n(.*?)\n```'│
│ → Extracts content between fences ✅ │
│ → json_content = '{ "rec..." }'     │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ json.loads(json_content) ✅          │
│ → Successfully parses                │
│ → Extracts recommendations array    │
│ → structured_recommendations = [3]  │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ UI checks:                          │
│ len(structured_recommendations) > 0 │
│ → True ✅                            │
│ → Displays recommendations ✅        │
└─────────────────────────────────────┘
```

## Regex Pattern Explanation

```python
code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
```

- ` ``` ` - Matches opening code fence (three backticks)
- `(?:json)?` - Optionally matches "json" language specifier (non-capturing group)
- `\s*` - Matches optional whitespace
- `\n` - Matches newline after opening fence
- `(.*?)` - **Captures** the content (non-greedy, stops at first match)
- `\n```` - Matches closing newline and code fence

**Flags:** `re.DOTALL` - Makes `.` match newlines too (for multi-line JSON)

## Examples Handled

### Example 1: JSON with language specifier
```markdown
```json
{"recommendations": [...]}
```
```
✅ **Extracted:** `{"recommendations": [...]}`

### Example 2: JSON without language specifier
```markdown
```
{"recommendations": [...]}
```
```
✅ **Extracted:** `{"recommendations": [...]}`

### Example 3: Inline code block
```markdown
`{"recommendations": [...]}`
```
✅ **Extracted:** `{"recommendations": [...]}`

### Example 4: Raw JSON (no markdown)
```json
{"recommendations": [...]}
```
✅ **No extraction needed, parses directly**

## Expected Log Output

### Successful parsing with markdown:
```
DEBUG: Extracted JSON from markdown code block
DEBUG: Attempting to parse JSON content (first 500 chars): {"recommendations": [{"intervention_type": ...
DEBUG: Parsed structured data: {
  "recommendations": [
    {
      "intervention_type": "Tutoring Referral",
      ...
DEBUG: Structured data is dict. Keys: dict_keys(['recommendations'])
INFO: Extracted 3 recommendations from structured data
DEBUG: First recommendation: {'intervention_type': 'Tutoring Referral', 'modality': 'In-Person', ...}
DEBUG: Displaying recommendations. Keys: dict_keys(['llm_recommendations', 'structured_recommendations', ...])
DEBUG: Structured recommendations count: 3
```

### Failed parsing (falls back to text):
```
WARNING: Failed to parse final content as JSON: Expecting value: line 1 column 1 (char 0)
DEBUG: Final content was: I'll help you create recommendations...
DEBUG: Displaying recommendations. Keys: dict_keys(['llm_recommendations', 'structured_recommendations', ...])
DEBUG: Structured recommendations count: 0
WARNING: ⚠️ AI analysis completed but no structured recommendations were generated.
```

## Files Modified

- `app.py`:
  - Lines 1738-1756: Added JSON extraction from markdown code blocks
  - Lines 1759-1771: Enhanced debug logging for JSON parsing
  - Lines 2147-2150: Added debug logging for UI display

## Benefits

1. ✅ **Handles multiple formats**: Raw JSON, markdown code blocks, inline code
2. ✅ **Better error messages**: Detailed logging shows exactly what's being parsed
3. ✅ **Robust parsing**: Uses regex with `re.DOTALL` for multi-line content
4. ✅ **Backward compatible**: Still works if agent returns raw JSON
5. ✅ **Debug friendly**: Extensive logging for troubleshooting

## Testing

To verify the fix works:

1. **Generate recommendations** for a student
2. **Check logs** for:
   - "Extracted JSON from markdown code block" (indicates code fence was detected)
   - "Extracted 3 recommendations from structured data" (indicates successful parsing)
   - "Structured recommendations count: 3" (indicates UI received the data)
3. **Verify UI** shows recommendations summary instead of warning
4. **Click "Create Intervention"** button should transfer structured data correctly

## Prevention

This fix prevents:
- ❌ JSON parsing errors when agent uses markdown formatting
- ❌ False "no recommendations" warnings when data exists
- ❌ Loss of structured data due to format mismatches

And ensures:
- ✅ Markdown code blocks are properly handled
- ✅ JSON is extracted before parsing
- ✅ Comprehensive logging for debugging
- ✅ Multiple format support (raw, markdown, inline)

