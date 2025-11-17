# Feature: Individual Create Intervention Buttons

## Overview
Added individual "Create Intervention" buttons to each AI recommendation, allowing users to directly create an intervention from a specific recommendation with all fields pre-populated.

## Changes Made

### 1. AI Recommendations Page - Enhanced Display (Lines 2196-2257)

**Before:**
- Displayed recommendations as raw JSON
- Single generic "Create Intervention from These Recommendations" button
- Used first recommendation by default

**After:**
- Beautiful formatted cards for each recommendation
- Individual "📝 Create Intervention" button per recommendation
- Direct navigation with pre-populated data

### New Display Structure:

```
✨ Recommended Interventions
Choose one of the following AI-recommended interventions to create

───────────────────────────────────────

📌 Recommendation 1
Intervention Type: Tutoring Referral    Priority: 🔴 High

Action:
Tutoring Referral shows the highest GPA improvement...

Timeline: Implement within 1 week, with weekly recurring sessions

Goal: Improve current failing course grade to passing...

[📝 Create Intervention]

───────────────────────────────────────

📌 Recommendation 2
[... similar layout ...]

───────────────────────────────────────

📌 Recommendation 3
[... similar layout ...]

───────────────────────────────────────

🔍 View as JSON (collapsed)
```

### 2. Individual Button Logic (Lines 2228-2249)

Each recommendation card has its own button that:

```python
if st.button(f"📝 Create Intervention", key=f"create_from_rec_{idx}", type="primary"):
    # 1. Store the selected recommendation
    st.session_state.selected_recommendation = rec
    st.session_state.selected_recommendation_index = idx
    
    # 2. Store student data
    st.session_state.selected_student = st.session_state.ai_rec_student_id
    st.session_state.selected_student_name = st.session_state.ai_rec_student_name
    # ... (more student fields)
    
    # 3. Store full recommendations context
    st.session_state.ai_recommendations = recommendations
    
    # 4. Navigate to Create Intervention page
    st.session_state.page = "Create Intervention"
    st.rerun()
```

### 3. Create Intervention Page - Auto-Population (Lines 2452-2516)

Added logic to detect when a specific recommendation was selected and pre-fill all form fields:

#### Detection and Notification:
```python
if 'selected_recommendation' in st.session_state:
    selected_rec = st.session_state.selected_recommendation
    rec_index = st.session_state.get('selected_recommendation_index', 0)
    
    st.success(f"📌 Using Recommendation #{rec_index} to create intervention")
```

#### Field Pre-Population:
```python
# 1. Intervention Type & Priority
st.session_state.ai_selected_intervention_type = selected_rec.get('intervention_type', '')
st.session_state.ai_selected_priority = selected_rec.get('priority', 'Medium')

# 2. Formatted Details Text
ai_details_text = f"🤖 AI-Generated Recommendation #{rec_index}\n\n"
ai_details_text += f"Intervention Type: {selected_rec.get('intervention_type')}\n"
ai_details_text += f"Priority Level: {selected_rec.get('priority')}\n\n"
ai_details_text += f"Recommended Action:\n{selected_rec['action']}\n\n"
ai_details_text += f"Timeline: {selected_rec['timeline']}\n\n"
ai_details_text += f"Goal: {selected_rec['measurable_goal']}\n\n"
ai_details_text += f"Best Practices:\n{selected_rec['best_practices']}"

st.session_state.ai_generated_details = ai_details_text
```

#### Special Handling for Academic Meetings (Lines 2484-2507):
```python
if selected_rec.get('intervention_type') == 'Academic Meeting':
    from datetime import datetime, timedelta
    
    # Parse timeline for date suggestion
    timeline = selected_rec.get('timeline', '')
    if 'within 1 week' in timeline.lower():
        suggested_date = datetime.now().date() + timedelta(days=2)
    elif 'within 3 days' in timeline.lower():
        suggested_date = datetime.now().date() + timedelta(days=2)
    else:
        suggested_date = datetime.now().date() + timedelta(days=7)
    
    # Determine modality from recommendation
    modality = selected_rec.get('modality', 'In-Person')
    
    st.session_state.ai_meeting_details = {
        'meeting_type': modality,
        'meeting_date': suggested_date,
        'meeting_time': datetime.strptime("10:00", "%H:%M").time(),
        'agenda': ai_details_text
    }
```

#### Clear Button (Lines 2509-2516):
```python
if st.button("🗑️ Clear Selected Recommendation"):
    del st.session_state.selected_recommendation
    del st.session_state.selected_recommendation_index
    del st.session_state.ai_generated_details
    if 'ai_meeting_details' in st.session_state:
        del st.session_state.ai_meeting_details
    st.rerun()
```

### 4. Removed Legacy Button (Lines 2266-2283)

Removed the old generic "Create Intervention from These Recommendations" button since it's been replaced by individual buttons.

**Before:** 3 buttons (Create/Regenerate/Back)
**After:** 2 buttons (Regenerate/Back)

## User Flow

### Complete Flow:

```
1. Student Risk Dashboard
   ↓ Click "🤖 AI Rec"
   
2. AI Recommendations Page
   ↓ Generate recommendations (with streaming thought process)
   
3. View 3 Formatted Recommendations
   Each with its own "Create Intervention" button
   ↓ Click button on desired recommendation (e.g., #2)
   
4. Create Intervention Page
   ✅ All fields pre-populated from Recommendation #2:
      - Intervention Type dropdown: "Peer Mentoring Program"
      - Priority dropdown: "Medium"
      - Details text area: Full formatted text with action, timeline, goal
      - Meeting details (if applicable): Date, time, modality
   
5. User Reviews & Edits (if needed)
   ↓ Submit
   
6. Intervention Created!
```

## Benefits

### 1. Better User Experience
✅ **Clear Choice**: Users can see all recommendations and choose the best one
✅ **No Confusion**: Individual buttons make it clear which recommendation they're selecting
✅ **Faster Workflow**: One click goes straight to pre-filled form
✅ **Visual Clarity**: Beautiful formatted cards with priority indicators

### 2. More Flexible
✅ **Multiple Options**: Can create interventions from any recommendation, not just the first
✅ **Easy Comparison**: All recommendations visible at once
✅ **Informed Decision**: See full details before committing

### 3. Better Data Flow
✅ **Specific Selection**: System knows exactly which recommendation was chosen
✅ **Complete Context**: All recommendation data transferred
✅ **Audit Trail**: Can track which recommendations are actually used

## Technical Details

### Session State Keys Used:
- `selected_recommendation`: The full recommendation object
- `selected_recommendation_index`: Which recommendation number (1, 2, or 3)
- `ai_selected_intervention_type`: Pre-selected intervention type
- `ai_selected_priority`: Pre-selected priority level
- `ai_generated_details`: Formatted text for details field
- `ai_meeting_details`: Meeting-specific details (date, time, modality)

### Data Structure:
```python
selected_recommendation = {
    "intervention_type": "Tutoring Referral",
    "modality": "In-Person",
    "priority": "High",
    "action": "Tutoring Referral shows the highest GPA improvement...",
    "timeline": "Implement within 1 week, with weekly recurring sessions",
    "measurable_goal": "Improve current failing course grade to passing...",
    "best_practices": "Match with subject-qualified tutors..."
}
```

## Files Modified

- `app.py`:
  - Lines 2196-2257: Enhanced recommendations display with individual buttons
  - Lines 2228-2249: Individual button click handlers
  - Lines 2255-2257: Moved JSON view to collapsible expander
  - Lines 2452-2516: Auto-population logic on Create Intervention page
  - Lines 2484-2507: Special handling for Academic Meeting recommendations
  - Lines 2509-2516: Clear selected recommendation button
  - Lines 2266-2283: Simplified action buttons (removed old create button)

## Testing Checklist

- [ ] Generate AI recommendations for a student
- [ ] Verify all 3 recommendations display with formatting
- [ ] Click "Create Intervention" on Recommendation #1
  - [ ] Verify fields are pre-populated with #1's data
  - [ ] Verify success message shows "Using Recommendation #1"
- [ ] Go back, click "Create Intervention" on Recommendation #2
  - [ ] Verify fields are pre-populated with #2's data (different from #1)
- [ ] Go back, click "Create Intervention" on Recommendation #3
  - [ ] Verify fields are pre-populated with #3's data
- [ ] For an "Academic Meeting" recommendation:
  - [ ] Verify meeting date is suggested based on timeline
  - [ ] Verify modality is set from recommendation
  - [ ] Verify agenda includes all recommendation details
- [ ] Click "Clear Selected Recommendation"
  - [ ] Verify fields reset to defaults
  - [ ] Verify success message disappears
- [ ] Verify "View as JSON" expander shows raw data
- [ ] Verify "Regenerate" and "Back to Dashboard" buttons still work

## Future Enhancements

1. **Recommendation Comparison**: Add ability to compare recommendations side-by-side
2. **Recommendation Rating**: Allow users to rate recommendations after use
3. **Usage Analytics**: Track which recommendations are most commonly selected
4. **Recommendation Modification**: Allow editing recommendation details before creating intervention
5. **Multiple Interventions**: Create multiple interventions from different recommendations
6. **Recommendation History**: Show history of recommendations for each student

