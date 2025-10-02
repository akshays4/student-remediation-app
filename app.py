import streamlit as st
import psycopg
import pandas as pd
import os
# import time
from databricks import sdk
from databricks.sdk import WorkspaceClient
import plotly.express as px
from dotenv import load_dotenv
import logging
import requests
import json
from typing import Dict, List, Optional


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database configuration variables
DATABASE_REMEDIATION_DATA = os.getenv("DATABASE_REMEDIATION_DATA", "akshay_student_remediation")

# Databricks Model Serving Endpoint Configuration
SERVING_ENDPOINT = os.getenv("SERVING_ENDPOINT")

def get_user_credentials():
    """Get user authorization credentials from Streamlit headers"""
    user_email = st.context.headers.get('x-forwarded-email')
    user_token = st.context.headers.get('x-forwarded-access-token')
    
    logger.debug(f"User email: {user_email}")
    logger.debug(f"User token present: {bool(user_token)}")
    
    if not user_token:
        st.error("❌ User authorization token not found. Please ensure the app has proper user authorization scopes configured.")
        st.info("This app requires user authorization to access your data with your permissions.")
        st.stop()
    
    return user_email, user_token

logger.debug(f"DATABASE_REMEDIATION_DATA: {DATABASE_REMEDIATION_DATA}")

# Database connection setup - using user authorization with direct connections

def get_postgres_password():
    """Get PostgreSQL password using user authorization token"""
    try:
        user_email, user_token = get_user_credentials()
        logger.debug("Using user authorization token for PostgreSQL connection")
        return user_token
    except Exception as e:
        st.error(f"❌ Failed to get user authorization token: {str(e)}")
        st.stop()

def get_connection(dbname=None):
    """Get a direct connection using user authorization (no pooling to avoid timeout issues)."""
    try:
        # Use default database if none specified
        if dbname is None:
            dbname = os.getenv('PGDATABASE')
        
        user_email, user_token = get_user_credentials()
        postgres_password = get_postgres_password()
        
        # Create direct connection without pooling to avoid timeout issues
        # Use the user email from the OAuth token instead of PGUSER
        conn_string = (
            f"dbname={dbname} "
            f"user={user_email} "
            f"password={postgres_password} "
            f"host={os.getenv('PGHOST')} "
            f"port={os.getenv('PGPORT')} "
            f"sslmode={os.getenv('PGSSLMODE', 'require')} "
            f"application_name={os.getenv('PGAPPNAME')} "
            f"connect_timeout=10"
        )
        
        logger.debug(f"Creating direct connection to {dbname} for user {user_email}")
        logger.debug(f"Connection string (password hidden): dbname={dbname} user={user_email} host={os.getenv('PGHOST')} port={os.getenv('PGPORT')}")
        
        conn = psycopg.connect(conn_string)
        
        # Test the connection and log current user
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SELECT current_user, session_user")
            current_user, session_user = cur.fetchone()
            logger.debug(f"Connected as current_user: {current_user}, session_user: {session_user}")
        
        return conn
        
    except Exception as e:
        logger.error(f"Failed to get database connection: {str(e)}")
        st.error(f"❌ Database connection failed: {str(e)}")
        st.info("Please ensure you have proper permissions to access the database.")
        st.stop()


# LLM-Powered Intervention Recommendation Functions

def extract_useful_text_from_structured_response(content_list) -> Optional[str]:
    """Extract useful recommendation text from gpt-oss structured response"""
    try:
        # First, try to find the final text component from the last element
        # This handles the specific format: [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]
        if content_list and len(content_list) > 0:
            # Look for the last element with type 'text'
            for item in reversed(content_list):
                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                    logger.debug(f"Found final text component: {item['text'][:100]}...")
                    return str(item['text'])
        
        # Fallback: extract text from any element (original logic)
        useful_parts = []
        
        for item in content_list:
            if isinstance(item, dict):
                # Look for summary text in the structure
                if 'summary' in item and isinstance(item['summary'], list):
                    for summary_item in item['summary']:
                        if isinstance(summary_item, dict) and 'text' in summary_item:
                            text = summary_item['text']
                            # Clean up the reasoning text and extract actionable parts
                            cleaned_text = clean_reasoning_text(text)
                            if cleaned_text:
                                useful_parts.append(cleaned_text)
                
                # Look for direct text content
                elif 'text' in item:
                    useful_parts.append(str(item['text']))
                
                # Look for other useful fields
                elif 'content' in item:
                    useful_parts.append(str(item['content']))
        
        if useful_parts:
            return " ".join(useful_parts)
        
        return None
        
    except Exception as e:
        logger.debug(f"Error extracting structured response: {e}")
        return None

def clean_reasoning_text(text: str) -> str:
    """Clean up reasoning text to extract actionable recommendations"""
    if not text:
        return ""
    
    # Split into sentences and look for actionable content
    sentences = text.split('. ')
    useful_sentences = []
    
    # Keywords that indicate actionable content
    action_keywords = [
        'recommend', 'suggest', 'should', 'need', 'priority', 'timeline', 
        'action', 'meeting', 'tutoring', 'counseling', 'academic', 'intervention'
    ]
    
    for sentence in sentences:
        sentence = sentence.strip()
        if any(keyword in sentence.lower() for keyword in action_keywords):
            # Skip meta-reasoning sentences
            if not any(meta in sentence.lower() for meta in [
                'we need to', 'let\'s', 'probably', 'perhaps', 'but we need to choose'
            ]):
                useful_sentences.append(sentence)
    
    if useful_sentences:
        return '. '.join(useful_sentences) + '.'
    
    # Fallback: return a cleaned version of the original
    return text.replace('We need to produce structured recommendation.', '').strip()

def clean_ai_response(ai_text: str) -> str:
    """Clean AI response to remove any prompt instructions or unwanted text"""
    if not ai_text:
        return ai_text
    
    # Remove common prompt instruction leakage
    lines = ai_text.split('\n')
    clean_lines = []
    
    skip_phrases = [
        'likely academic meeting',
        'provide priority levels',
        'use numbered list',
        'double line breaks',
        'single line breaks',
        'provide actionable steps',
        'choose from these interventions',
        'format each recommendation',
        'copy exactly'
    ]
    
    for line in lines:
        line_lower = line.lower().strip()
        
        # Skip lines that contain prompt instructions
        if any(phrase in line_lower for phrase in skip_phrases):
            continue
            
        # Skip lines that are just formatting instructions
        if line_lower.startswith(('important:', 'required format:', 'use double', 'use single')):
            continue
            
        # Keep the actual content
        clean_lines.append(line)
    
    return '\n'.join(clean_lines).strip()

def format_ai_recommendations(ai_text: str) -> str:
    """Format AI recommendations with proper line breaks and structure"""
    if not ai_text:
        return ai_text
    
    # Clean up the text first
    formatted_text = ai_text.strip()
    
    # Ensure numbered items start on new lines
    formatted_text = formatted_text.replace('1.', '\n\n1.')
    formatted_text = formatted_text.replace('2.', '\n\n2.')
    formatted_text = formatted_text.replace('3.', '\n\n3.')
    
    # Clean up multiple newlines
    while '\n\n\n' in formatted_text:
        formatted_text = formatted_text.replace('\n\n\n', '\n\n')
    
    # Remove leading newlines
    formatted_text = formatted_text.lstrip('\n')
    
    return formatted_text

def format_intervention_details_for_display(ai_details: str) -> str:
    """Format AI-generated intervention details for better readability"""
    if not ai_details:
        return ai_details
    
    # Clean up the text for text area display
    formatted_text = ai_details
    
    # Remove problematic formatting that causes display issues
    formatted_text = formatted_text.replace('**', '')
    formatted_text = formatted_text.replace('---', '')
    
    # Remove equals signs and hash symbols that create visual clutter
    formatted_text = formatted_text.replace('='*50, '')
    formatted_text = formatted_text.replace('='*40, '')
    formatted_text = formatted_text.replace('='*30, '')
    formatted_text = formatted_text.replace('='*20, '')
    formatted_text = formatted_text.replace('='*10, '')
    formatted_text = formatted_text.replace('#', '')
    
    # Clean up any table formatting completely
    if '|' in formatted_text:
        lines = formatted_text.split('\n')
        clean_lines = []
        
        for line in lines:
            # Skip any line with table formatting
            if '|' in line or '---' in line:
                # Try to extract meaningful content from table rows
                if '|' in line and line.count('|') >= 2:
                    parts = [part.strip() for part in line.split('|') if part.strip()]
                    if len(parts) >= 2 and not any(header in parts[0] for header in ['#', 'Action', 'Who', 'Deadline']):
                        clean_lines.append(f"• {parts[1] if len(parts) > 1 else parts[0]}")
                continue
            else:
                clean_lines.append(line)
        
        formatted_text = '\n'.join(clean_lines)
    
    # Clean up lines that are just equals signs or dashes
    lines = formatted_text.split('\n')
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just equals signs, dashes, or pipes
        if not stripped or stripped.replace('=', '').replace('-', '').replace('|', '').strip() == '':
            continue
        clean_lines.append(line)
    
    formatted_text = '\n'.join(clean_lines)
    
    # Ensure proper spacing for numbered lists and bullet points
    formatted_text = formatted_text.replace('\n•', '\n• ')  # Ensure space after bullet
    formatted_text = formatted_text.replace('\n1.', '\n\n1.')  # Add space before numbered items
    formatted_text = formatted_text.replace('\n2.', '\n\n2.')
    formatted_text = formatted_text.replace('\n3.', '\n\n3.')
    formatted_text = formatted_text.replace('\n4.', '\n\n4.')
    formatted_text = formatted_text.replace('\n5.', '\n\n5.')
    
    # Clean up excessive newlines
    while '\n\n\n' in formatted_text:
        formatted_text = formatted_text.replace('\n\n\n', '\n\n')
    
    return formatted_text.strip()

def call_databricks_serving_endpoint(prompt: str, max_tokens: int = 500, response_format: Optional[Dict] = None) -> Optional[str]:
    """Call Databricks model serving endpoint using OpenAI-compatible client"""
    if not SERVING_ENDPOINT:
        logger.warning("Serving endpoint not configured")
        return None
    
    try:
        logger.debug(f"Calling gpt-oss endpoint: {SERVING_ENDPOINT}")
        
        w = WorkspaceClient()
        client = w.serving_endpoints.get_open_ai_client()  # OpenAI-compatible
        
        # Prepare the request parameters
        request_params = {
            "model": SERVING_ENDPOINT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7
        }
        
        # Add response_format if provided (for structured outputs)
        if response_format:
            request_params["response_format"] = response_format
        
        r = client.chat.completions.create(**request_params)
        
        logger.debug(f"Response received successfully")
        
        try:
            # Handle different content types
            content = r.choices[0].message.content
            logger.debug(f"Content type: {type(content)}, Content: {content}")
            
            if content is None:
                return ""
            elif isinstance(content, list):
                logger.debug(f"Processing list content with {len(content)} items")
                # Extract useful text from structured response
                extracted_text = extract_useful_text_from_structured_response(content)
                if extracted_text:
                    return extracted_text.strip()
                
                # Fallback: join all items
                if len(content) > 0:
                    str_items = []
                    for item in content:
                        str_items.append(str(item))
                    result = " ".join(str_items)
                    logger.debug(f"Joined result: {result}")
                    return result.strip()
                else:
                    return ""
            elif isinstance(content, str):
                logger.debug(f"Processing string content")
                return content.strip()
            else:
                logger.debug(f"Processing other type content: {type(content)}")
                # Convert other types to string
                return str(content).strip()
                
        except Exception as content_error:
            logger.error(f"Error processing response content: {content_error}")
            logger.debug(f"Raw response object: {r}")
            # Try to extract any text we can
            try:
                return str(r.choices[0].message.content or "")
            except:
                return ""
        
    except Exception as e:
        logger.error(f"Error calling serving endpoint: {str(e)}")
        logger.debug(f"Full error details: {type(e).__name__}: {e}")
        return None


def generate_intervention_recommendations(student_data: Dict) -> Dict[str, any]:
    """Generate intelligent intervention recommendations using LLM with structured output"""
    
    # Define the JSON schema for structured output
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "intervention_recommendations",
            "schema": {
                "type": "object",
                "properties": {
                    "recommendations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "intervention_type": {
                                    "type": "string",
                                    "enum": ["Academic Meeting", "Study Plan Assignment", "Tutoring Referral", 
                                           "Counseling Referral", "Financial Aid Consultation", "Career Guidance Session", 
                                           "Peer Mentoring Program", "Academic Probation Review"]
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["High", "Medium", "Low"]
                                },
                                "action": {
                                    "type": "string",
                                    "description": "Brief specific action explaining why this student needs this intervention"
                                },
                                "timeline": {
                                    "type": "string",
                                    "description": "When to implement this intervention"
                                },
                                "goal": {
                                    "type": "string",
                                    "description": "Measurable outcome specific to this student"
                                }
                            },
                            "required": ["intervention_type", "priority", "action", "timeline", "goal"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["recommendations"],
                "additionalProperties": False
            },
            "strict": True
        }
    }
    
    # Create a structured prompt
    prompt = f"""
Provide 3 concise intervention recommendations for this student. Each recommendation should directly address their specific situation.

Student: {student_data.get('full_name', 'Student')} ({student_data.get('major', 'N/A')}, {student_data.get('year_level', 'N/A')})
GPA: {student_data.get('gpa', 'N/A')} | Failing: {student_data.get('failing_grades', 0)}/{student_data.get('courses_enrolled', 0)} courses | Risk: {student_data.get('risk_category', 'N/A')}

For each recommendation:
- Choose intervention_type from: Academic Meeting, Study Plan Assignment, Tutoring Referral, Counseling Referral, Financial Aid Consultation, Career Guidance Session, Peer Mentoring Program, Academic Probation Review
- Set priority: High, Medium, or Low
- Write brief action explaining why this specific student needs this intervention
- Specify timeline for implementation
- Define measurable goal specific to this student's situation

Respond with a JSON object containing an array of 3 recommendations.
"""

    # Call the LLM with structured output
    llm_response = call_databricks_serving_endpoint(prompt, max_tokens=800, response_format=response_format)
    
    if not llm_response:
        # Return empty result if LLM fails
        return {
            "llm_recommendations": "AI recommendations are currently unavailable. Please try again later.",
            "structured_recommendations": [],
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "llm_unavailable"
        }
    
    try:
        # Parse the structured JSON response
        import json
        structured_data = json.loads(llm_response)
        recommendations = structured_data.get("recommendations", [])
        
        # Format the structured data for display
        formatted_text = ""
        for i, rec in enumerate(recommendations, 1):
            formatted_text += f"{i}. {rec['intervention_type']} - Priority: {rec['priority']}\n\n"
            formatted_text += f"Action: {rec['action']}\n\n"
            formatted_text += f"Timeline: {rec['timeline']}\n\n"
            formatted_text += f"Goal: {rec['goal']}\n\n"
            if i < len(recommendations):
                formatted_text += "\n"
        
        return {
            "llm_recommendations": formatted_text.strip(),
            "structured_recommendations": recommendations,
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "databricks_llm_structured"
        }
        
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to parse structured response: {e}")
        # Fallback to original text processing
        cleaned_response = clean_ai_response(llm_response)
        
        return {
            "llm_recommendations": cleaned_response,
            "structured_recommendations": [],
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "databricks_llm_fallback"
        }


def generate_personalized_intervention_details(intervention_type: str, student_data: Dict, priority: str) -> str:
    """Generate personalized intervention details using LLM"""
    
    prompt = f"""
Create a specific action plan for this intervention. Be direct and practical.

Student: {student_data.get('full_name', 'N/A')} ({student_data.get('major', 'N/A')}, {student_data.get('year_level', 'N/A')})
GPA: {student_data.get('gpa', 'N/A')} | Risk: {student_data.get('risk_category', 'N/A')}
Intervention: {intervention_type} (Priority: {priority})

CRITICAL: Output ONLY clean numbered lists. NO tables, NO pipes (|), NO equals signs (=), NO markdown headers (#).

Use this EXACT format:

1. Objective
Develop and implement [specific goal with measurable outcome]

2. Action Steps
• Complete [specific action 1]
• Schedule [specific action 2] 
• Implement [specific action 3]
• Follow up [specific action 4]

3. Timeline
• Week 1: [action]
• Week 2: [action]
• Ongoing: [action]

4. Resources Needed
• [Resource 1]
• [Resource 2]
• [Resource 3]

5. Success Measures
• [Measurable outcome 1]
• [Measurable outcome 2]

Keep it concise and actionable. Use simple bullet points only.
"""

    llm_response = call_databricks_serving_endpoint(prompt, max_tokens=600)
    
    if llm_response:
        return f"Priority: {priority}\n\n{llm_response}"
    else:
        return f"Priority: {priority}\n\nAI intervention details are currently unavailable. Please provide manual details for this {intervention_type}."

def parse_ai_recommendations(recommendations_data: Dict) -> Dict:
    """Parse AI recommendations data and extract structured data for form population"""
    try:
        # Check if we have structured recommendations from the new format
        if 'structured_recommendations' in recommendations_data and recommendations_data['structured_recommendations']:
            structured_recs = recommendations_data['structured_recommendations']
            
            # Convert structured format to our expected format
            recommendations = []
            for rec in structured_recs:
                recommendations.append({
                    'intervention_type': rec.get('intervention_type', ''),
                    'priority': rec.get('priority', ''),
                    'action': rec.get('action', ''),
                    'timeline': rec.get('timeline', ''),
                    'goal': rec.get('goal', '')
                })
            
            return {
                'recommendations': recommendations,
                'primary_recommendation': recommendations[0] if recommendations else None
            }
        
        # Fallback: parse from text format (legacy support)
        ai_text = recommendations_data.get('llm_recommendations', '')
        recommendations = []
        lines = ai_text.split('\n')
        current_rec = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Look for numbered recommendations (1., 2., 3.)
            if line.startswith(('1.', '2.', '3.')):
                # Save previous recommendation if exists
                if current_rec:
                    recommendations.append(current_rec)
                
                # Parse intervention type and priority
                current_rec = {}
                if ' - Priority:' in line or ' - [Priority:' in line:
                    parts = line.split(' - ')
                    if len(parts) >= 2:
                        # Extract intervention type (remove number)
                        intervention_part = parts[0].split('.', 1)[1].strip()
                        current_rec['intervention_type'] = intervention_part.strip('[]')
                        
                        # Extract priority
                        priority_part = parts[1].replace('Priority:', '').replace('[Priority:', '').strip('[]')
                        current_rec['priority'] = priority_part.split(']')[0].strip()
            
            # Look for action items
            elif line.startswith('Action:'):
                current_rec['action'] = line.replace('Action:', '').strip()
            
            # Look for timeline
            elif line.startswith('Timeline:'):
                current_rec['timeline'] = line.replace('Timeline:', '').strip()
                
            # Look for goals/objectives
            elif line.startswith(('Goal:', 'Objective:')):
                current_rec['goal'] = line.replace('Goal:', '').replace('Objective:', '').strip()
        
        # Add the last recommendation
        if current_rec:
            recommendations.append(current_rec)
        
        return {
            'recommendations': recommendations,
            'primary_recommendation': recommendations[0] if recommendations else None
        }
        
    except Exception as e:
        logger.debug(f"Error parsing AI recommendations: {e}")
        return {'recommendations': [], 'primary_recommendation': None}

def generate_meeting_details_from_ai(recommendation: Dict, student_data: Dict) -> Dict:
    """Generate meeting details based on AI recommendation and student context"""
    import datetime
    
    details = {}
    
    # Determine meeting type based on intervention and priority
    if recommendation.get('priority', '').lower() == 'high':
        details['meeting_type'] = 'In-Person'
        # Schedule within 48 hours for high priority
        details['meeting_date'] = datetime.date.today() + datetime.timedelta(days=1)
        details['meeting_time'] = datetime.time(10, 0)  # 10:00 AM
    elif recommendation.get('priority', '').lower() == 'medium':
        details['meeting_type'] = 'Virtual'
        # Schedule within 1 week for medium priority
        details['meeting_date'] = datetime.date.today() + datetime.timedelta(days=3)
        details['meeting_time'] = datetime.time(14, 0)  # 2:00 PM
    else:
        details['meeting_type'] = 'Virtual'
        # Schedule within 2 weeks for low priority
        details['meeting_date'] = datetime.date.today() + datetime.timedelta(days=7)
        details['meeting_time'] = datetime.time(15, 0)  # 3:00 PM
    
    # Generate agenda based on AI recommendation and student context
    agenda_items = []
    
    # Add student context to agenda
    risk_level = student_data.get('risk_category', 'Unknown')
    gpa = student_data.get('gpa', 'N/A')
    failing_courses = student_data.get('failing_grades', 0)
    
    agenda_items.append(f"Review academic standing: {risk_level} risk level, GPA: {gpa}")
    
    if failing_courses > 0:
        agenda_items.append(f"Address {failing_courses} failing course(s)")
    
    # Add AI recommendation action items
    if recommendation.get('action'):
        agenda_items.append(f"Action plan: {recommendation['action']}")
    
    if recommendation.get('goal'):
        agenda_items.append(f"Success goals: {recommendation['goal']}")
    
    if recommendation.get('timeline'):
        agenda_items.append(f"Timeline: {recommendation['timeline']}")
    
    # Add follow-up items
    agenda_items.append("Establish regular check-in schedule")
    agenda_items.append("Identify additional support resources needed")
    
    details['agenda'] = '\n'.join([f"• {item}" for item in agenda_items])
    
    return details



# Student Risk Management Functions


@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_student_risk_data():
    """Load student risk data from database"""
    with get_connection(DATABASE_REMEDIATION_DATA) as conn:
        query = f"""
        SELECT 
            student_id,
            full_name,
            major,
            year_level,
            gpa,
            courses_enrolled,
            failing_grades,
            risk_category,
            activity_status
        FROM {DATABASE_REMEDIATION_DATA}.public.student_risk_analysis_gold
        ORDER BY 
            CASE 
                WHEN risk_category = 'High Risk' THEN 1
                WHEN risk_category = 'Medium Risk' THEN 2
                WHEN risk_category = 'Low Risk' THEN 3
                WHEN risk_category = 'Excellent' THEN 4
                ELSE 5
            END,
            failing_grades DESC,
            gpa ASC
        """
        
        try:
            df = pd.read_sql_query(query, conn)
            return df
        except Exception as e:
            st.error(f"Error loading student data: {str(e)}")
            st.info(f"Please check that the '{DATABASE_REMEDIATION_DATA}.public.student_risk_analysis_gold' table exists and you have proper permissions.")
            return pd.DataFrame()

def list_available_tables():
    """List available tables in public schema for debugging purposes"""
    with get_connection(DATABASE_REMEDIATION_DATA) as conn:
        try:
            # Query to list tables in public schema
            query = """
            SELECT schemaname, tablename 
            FROM pg_tables 
            WHERE schemaname = 'public' 
            AND (tablename LIKE '%student%' OR tablename LIKE '%risk%')
            ORDER BY tablename
            """
            df = pd.read_sql_query(query, conn)
            return df
        except Exception as e:
            st.error(f"Error listing tables: {str(e)}")
            return pd.DataFrame()



def submit_intervention(student_id, intervention_type, details, created_by):
    """Submit intervention to database"""
    with get_connection(DATABASE_REMEDIATION_DATA) as conn:
        with conn.cursor() as cur:
            # First ensure the table exists
            create_table_query = """
            CREATE TABLE IF NOT EXISTS public.student_interventions (
                student_id VARCHAR(255),
                intervention_type VARCHAR(255),
                intervention_details TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(50) DEFAULT 'Pending',
                created_by VARCHAR(255),
                PRIMARY KEY (student_id, created_date)
            )
            """
            
            insert_query = """
            INSERT INTO public.student_interventions
            (student_id, intervention_type, intervention_details, created_by)
            VALUES (%s, %s, %s, %s)
            """
            
            try:
                # Create table if it doesn't exist
                cur.execute(create_table_query)
                # Insert the intervention
                cur.execute(insert_query, (student_id, intervention_type, details, created_by))
                conn.commit()
            except Exception as e:
                st.error(f"Error submitting intervention: {str(e)}")
                raise e

def get_risk_color(risk_category):
    """Return color based on risk category"""
    colors = {
        'High Risk': '#FF4B4B',
        'Medium Risk': '#FFA500', 
        'Low Risk': '#00CC88',
        'Excellent': '#28A745'
    }
    return colors.get(risk_category, '#808080')

def get_priority_color(priority):
    """Return color based on intervention priority"""
    colors = {
        'High': '#FF4B4B',
        'Medium': '#FFA500',
        'Low': '#00CC88'
    }
    return colors.get(priority, '#808080')

def load_scheduled_remediations():
    """Load scheduled remediations from database"""
    with get_connection(DATABASE_REMEDIATION_DATA) as conn:
        query = """
        SELECT 
            student_id,
            intervention_type,
            intervention_details,
            created_date,
            status,
            created_by
        FROM public.student_interventions
        WHERE status = 'Pending'
        ORDER BY 
            CASE 
                WHEN intervention_details LIKE '%Priority: High%' THEN 1
                WHEN intervention_details LIKE '%Priority: Medium%' THEN 2
                WHEN intervention_details LIKE '%Priority: Low%' THEN 3
                ELSE 4
            END,
            created_date DESC
        """
        
        try:
            df = pd.read_sql_query(query, conn)
            return df
        except Exception as e:
            st.error(f"Error loading scheduled remediations: {str(e)}")
            return pd.DataFrame()


# Streamlit UI
def main():
    # Page configuration
    st.set_page_config(
        page_title="Student Risk Management System",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Note: Intervention table will be created when needed
    
    # Header
    st.title("🎓 Student Risk Management System")
    st.markdown("---")
    
    # Sidebar
    st.sidebar.markdown("# 🏛️ Riverside University")
    st.sidebar.markdown("*Student Success Center*")
    
    # Initialize page in session state if not exists
    if 'page' not in st.session_state:
        st.session_state.page = "Student Risk Dashboard"
    
    # Page navigation menu
    
    # Define pages with icons
    pages = [
        {"name": "Student Risk Dashboard", "icon": "📊"},
        {"name": "Create Intervention", "icon": "📝"},
        {"name": "Scheduled Remediations", "icon": "📅"}
    ]
    
    # Create menu buttons
    for page_info in pages:
        page_name = page_info["name"]
        icon = page_info["icon"]
        
        # Check if this is the current page
        is_current = st.session_state.page == page_name
        
        # Create button with different styling for current page
        if is_current:
            # Current page - use primary button style
            if st.sidebar.button(f"{icon} {page_name}", key=f"nav_{page_name}", use_container_width=True, type="primary"):
                pass  # Already on this page
        else:
            # Other pages - use secondary button style
            if st.sidebar.button(f"{icon} {page_name}", key=f"nav_{page_name}", use_container_width=True):
                st.session_state.page = page_name
                st.rerun()
    
    # Get current page for the main content
    page = st.session_state.page
    
    if page == "Student Risk Dashboard":
        show_student_dashboard()
    elif page == "Create Intervention":
        show_create_intervention()
    elif page == "Scheduled Remediations":
        show_scheduled_remediations()

def show_student_dashboard():
    st.header("📊 Students at Risk Overview")
    
    # Add debug section in sidebar
    with st.sidebar:
        if st.checkbox("🔧 Debug Mode"):
            st.subheader("Debug Information")
            try:
                user_email, user_token = get_user_credentials()
                st.write(f"**User Email:** {user_email}")
                st.write(f"**Database:** {DATABASE_REMEDIATION_DATA}")
                st.write(f"**Schema:** public")
                st.write(f"**Auth Method:** User Authorization")
                st.write(f"**DB Host:** {os.getenv('PGHOST')}")
                st.write(f"**DB Port:** {os.getenv('PGPORT')}")
                st.write(f"**App Name:** {os.getenv('PGAPPNAME')}")
                
                st.markdown("**LLM Configuration:**")
                st.write(f"**Serving Endpoint:** {SERVING_ENDPOINT or 'Not configured'}")
                st.write(f"**Model Type:** OpenAI gpt-oss (Foundation Model API)")
                st.write(f"**Client Type:** OpenAI-compatible client")
                st.write(f"**Authentication:** Databricks SDK WorkspaceClient")
                st.write(f"**Integration:** get_open_ai_client() method")
                st.write(f"**Context Window:** 131k tokens")
                st.write(f"**Features:** Chain-of-thought reasoning, tool use")
                
                if st.button("Test LLM Endpoint"):
                    if SERVING_ENDPOINT:
                        with st.spinner("Testing LLM endpoint with user credentials..."):
                            test_response = call_databricks_serving_endpoint("Hello, this is a test.", max_tokens=50)
                            if test_response:
                                st.success("✅ LLM endpoint is working!")
                                st.write(f"**Test Response:** {test_response}")
                            else:
                                st.error("❌ LLM endpoint test failed - check permissions or endpoint configuration")
                    else:
                        st.error("❌ Serving endpoint not configured")
                
                if st.button("Test Connection"):
                    with st.spinner("Testing connection..."):
                        try:
                            with get_connection(DATABASE_REMEDIATION_DATA) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("SELECT current_user, session_user, version()")
                                    current_user, session_user, version = cur.fetchone()
                                    st.success("✅ Connection successful!")
                                    st.write(f"**Current User:** {current_user}")
                                    st.write(f"**Session User:** {session_user}")
                                    st.write(f"**PostgreSQL Version:** {version}")
                        except Exception as conn_e:
                            st.error(f"Connection test failed: {str(conn_e)}")
                
                if st.button("List Available Tables"):
                    with st.spinner("Listing tables..."):
                        tables_df = list_available_tables()
                        if not tables_df.empty:
                            st.write("**Available Tables in Public Schema:**")
                            st.dataframe(tables_df)
                        else:
                            st.write("No student/risk tables found in public schema")
            except Exception as e:
                st.error(f"Debug info error: {str(e)}")
    
    try:
        # Load data
        with st.spinner("Loading student data..."):
            df = load_student_risk_data()
        
        if df.empty:
            st.warning("No student data found.")
            st.info("💡 Try enabling Debug Mode in the sidebar to see available tables.")
            return
        
        # Summary metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            total_students = len(df)
            st.metric("Total Students", total_students)
        
        with col2:
            high_risk = len(df[df['risk_category'] == 'High Risk'])
            st.metric("High Risk", high_risk, delta=f"{high_risk/total_students*100:.1f}%")
        
        with col3:
            medium_risk = len(df[df['risk_category'] == 'Medium Risk'])
            st.metric("Medium Risk", medium_risk, delta=f"{medium_risk/total_students*100:.1f}%")
        
        with col4:
            excellent = len(df[df['risk_category'] == 'Excellent'])
            st.metric("Excellent", excellent, delta=f"{excellent/total_students*100:.1f}%")
        
        with col5:
            avg_gpa = df['gpa'].mean()
            st.metric("Average GPA", f"{avg_gpa:.2f}")
        
        # Risk distribution chart
        st.subheader("Risk Category Distribution")
        risk_counts = df['risk_category'].value_counts()
        fig = px.pie(values=risk_counts.values, names=risk_counts.index, 
                    color_discrete_map={
                        'High Risk': 'red',
                        'Medium Risk': 'orange',
                        'Low Risk': 'yellow',
                        'Excellent': 'green'
                    })
        st.plotly_chart(fig, use_container_width=True)
        
        # Filters
        st.subheader("Filter Students")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            risk_filter = st.multiselect("Risk Category", 
                                       options=df['risk_category'].unique(),
                                       default=df['risk_category'].unique())
        
        with col2:
            major_filter = st.multiselect("Major", 
                                        options=df['major'].unique(),
                                        default=df['major'].unique())
        
        with col3:
            year_filter = st.multiselect("Year Level", 
                                       options=df['year_level'].unique(),
                                       default=df['year_level'].unique())
        
        # Apply filters
        filtered_df = df[
            (df['risk_category'].isin(risk_filter)) &
            (df['major'].isin(major_filter)) &
            (df['year_level'].isin(year_filter))
        ]
        
        # Student list
        st.subheader(f"Students at Risk ({len(filtered_df)} students)")
        
        # Color key/legend
        st.markdown("**Risk Category Color Key:**")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #FF4B4B; margin-right: 8px; border-radius: 3px;"></div><span>High Risk</span></div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #FFA500; margin-right: 8px; border-radius: 3px;"></div><span>Medium Risk</span></div>', unsafe_allow_html=True)
        
        with col3:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #00CC88; margin-right: 8px; border-radius: 3px;"></div><span>Low Risk</span></div>', unsafe_allow_html=True)
        
        with col4:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #28A745; margin-right: 8px; border-radius: 3px;"></div><span>Excellent</span></div>', unsafe_allow_html=True)
        
        st.markdown("---")
        
        # Display students in a more visual way
        for idx, student in filtered_df.iterrows():
            with st.container():
                col1, col2, col3, col4 = st.columns([3, 1.5, 1.5, 2])
                
                with col1:
                    risk_color = get_risk_color(student['risk_category'])
                    st.markdown(f"""
                    <div style="padding: 10px; border-left: 4px solid {risk_color}; margin: 5px 0;">
                        <h4 style="margin: 0; color: {risk_color};">{student['full_name']}</h4>
                        <p style="margin: 0; color: gray;">ID: {student['student_id']}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.write(f"**Major:** {student['major']}")
                    st.write(f"**Year:** {student['year_level']}")
                
                with col3:
                    st.write(f"**GPA:** {student['gpa']:.2f}")
                    st.write(f"**Failing:** {student['failing_grades']}/{student['courses_enrolled']}")
                
                with col4:
                    # Stack buttons vertically for better text readability
                    if st.button(f"🤖 AI Rec", key=f"ai_btn_{student['student_id']}", help="Get AI-powered intervention recommendations", use_container_width=True):
                        with st.spinner("Generating AI recommendations..."):
                            student_dict = student.to_dict()
                            recommendations = generate_intervention_recommendations(student_dict)
                            
                            # Store recommendations in session state
                            st.session_state[f"recommendations_{student['student_id']}"] = recommendations
                            st.rerun()
                    
                    if st.button(f"Create", key=f"btn_{student['student_id']}", help="Create intervention manually", use_container_width=True):
                        st.session_state.selected_student = student['student_id']
                        st.session_state.selected_student_name = student['full_name']
                        st.session_state.selected_student_major = student['major']
                        st.session_state.selected_student_year = student['year_level']
                        st.session_state.selected_student_gpa = student['gpa']
                        st.session_state.selected_student_risk = student['risk_category']
                        st.session_state.page = "Create Intervention"
                        st.rerun()
                
                # Display AI recommendations if available
                if f"recommendations_{student['student_id']}" in st.session_state:
                    recommendations = st.session_state[f"recommendations_{student['student_id']}"]
                    
                    with st.expander(f"🤖 AI Recommendations for {student['full_name']}", expanded=True):
                        st.markdown("### AI-Generated Intervention Recommendations")
                        
                        # Display the LLM response with proper formatting and cleaning
                        cleaned_recommendations = clean_ai_response(recommendations["llm_recommendations"])
                        formatted_recommendations = format_ai_recommendations(cleaned_recommendations)
                        st.markdown(formatted_recommendations)
                        
                        # Add metadata
                        col_meta1, col_meta2 = st.columns(2)
                        with col_meta1:
                            st.caption(f"Generated: {recommendations['generated_at']}")
                        with col_meta2:
                            st.caption(f"Source: {recommendations['source']}")
                        
                        # Quick action buttons
                        st.markdown("---")
                        col_action1, col_action2, col_action3 = st.columns(3)
                        
                        with col_action1:
                            if st.button("📝 Create from AI Rec", key=f"create_ai_{student['student_id']}"):
                                # Parse AI recommendations and auto-populate form
                                parsed_ai = parse_ai_recommendations(recommendations)
                                
                                # Store student data
                                st.session_state.selected_student = student['student_id']
                                st.session_state.selected_student_name = student['full_name']
                                st.session_state.selected_student_major = student['major']
                                st.session_state.selected_student_year = student['year_level']
                                st.session_state.selected_student_gpa = student['gpa']
                                st.session_state.selected_student_risk = student['risk_category']
                                
                                # Store AI recommendations and parsed data
                                st.session_state.ai_recommendations = recommendations
                                st.session_state.parsed_ai_recommendations = parsed_ai
                                
                                # Auto-populate form fields from primary recommendation
                                if parsed_ai.get('primary_recommendation'):
                                    primary_rec = parsed_ai['primary_recommendation']
                                    
                                    # Set intervention type and priority
                                    if primary_rec.get('intervention_type'):
                                        st.session_state.ai_selected_intervention_type = primary_rec['intervention_type']
                                    if primary_rec.get('priority'):
                                        st.session_state.ai_selected_priority = primary_rec['priority']
                                    
                                    # Generate meeting details if it's an Academic Meeting
                                    if primary_rec.get('intervention_type') == 'Academic Meeting':
                                        student_dict = student.to_dict()
                                        meeting_details = generate_meeting_details_from_ai(primary_rec, student_dict)
                                        st.session_state.ai_meeting_details = meeting_details
                                    
                                    # Store AI-generated details for form population
                                    ai_details_text = f"AI Recommendation: {primary_rec.get('intervention_type', 'N/A')}\n"
                                    ai_details_text += f"Priority: {primary_rec.get('priority', 'N/A')}\n"
                                    if primary_rec.get('action'):
                                        ai_details_text += f"Action: {primary_rec['action']}\n"
                                    if primary_rec.get('timeline'):
                                        ai_details_text += f"Timeline: {primary_rec['timeline']}\n"
                                    if primary_rec.get('goal'):
                                        ai_details_text += f"Goal: {primary_rec['goal']}\n"
                                    
                                    st.session_state.ai_generated_details = ai_details_text
                                
                                st.session_state.page = "Create Intervention"
                                st.rerun()
                        
                        with col_action2:
                            if st.button("🔄 Regenerate", key=f"regen_{student['student_id']}"):
                                with st.spinner("Regenerating recommendations..."):
                                    student_dict = student.to_dict()
                                    new_recommendations = generate_intervention_recommendations(student_dict)
                                    st.session_state[f"recommendations_{student['student_id']}"] = new_recommendations
                                    st.rerun()
                        
                        with col_action3:
                            if st.button("❌ Dismiss", key=f"dismiss_{student['student_id']}"):
                                del st.session_state[f"recommendations_{student['student_id']}"]
                                st.rerun()
        
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")
        st.info("Please check your database connection and credentials.")

def show_create_intervention():
    st.header("📝 Create Student Intervention")
    
    # Check if AI recommendations are available
    ai_recommendations = st.session_state.get('ai_recommendations', None)
    if ai_recommendations:
        st.success("🤖 AI recommendations available! Use the suggestions below or create a custom intervention.")
        
        with st.expander("🤖 View AI Recommendations", expanded=True):
            cleaned_recommendations = clean_ai_response(ai_recommendations["llm_recommendations"])
            formatted_recommendations = format_ai_recommendations(cleaned_recommendations)
            st.markdown(formatted_recommendations)
            
            if st.button("🗑️ Clear AI Recommendations"):
                # Clear all AI-related session state
                for key in ['ai_recommendations', 'ai_generated_details', 'ai_selected_intervention_type', 'ai_selected_priority']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
    
    # Check if student was selected from dashboard
    if 'selected_student' in st.session_state:
        default_student_id = st.session_state.selected_student
        default_student_name = st.session_state.get('selected_student_name', '')
        student_major = st.session_state.get('selected_student_major', '')
        student_year = st.session_state.get('selected_student_year', '')
        student_gpa = st.session_state.get('selected_student_gpa', 0.0)
        student_risk = st.session_state.get('selected_student_risk', '')
        
        # Display student information
        st.success(f"Creating intervention for: **{default_student_name}** (ID: {default_student_id})")
        
        # Show student details in an info box
        col1, col2, col3 = st.columns(3)
        with col1:
            st.info(f"**Major:** {student_major}")
        with col2:
            st.info(f"**Year:** {student_year}")
        with col3:
            risk_color = get_risk_color(student_risk)
            st.markdown(f'<div style="padding: 10px; background-color: {risk_color}20; border-left: 4px solid {risk_color}; border-radius: 5px;"><strong>Risk Level:</strong> {student_risk}<br><strong>GPA:</strong> {student_gpa:.2f}</div>', unsafe_allow_html=True)
        
        # Add a button to clear the selection and start fresh
        if st.button("🔄 Clear Selection & Start Fresh"):
            for key in ['selected_student', 'selected_student_name', 'selected_student_major', 
                       'selected_student_year', 'selected_student_gpa', 'selected_student_risk',
                       'ai_recommendations', 'ai_generated_details', 'ai_selected_intervention_type', 
                       'ai_selected_priority', 'parsed_ai_recommendations', 'ai_meeting_details']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()
    else:
        default_student_id = ""
    
    # AI-Enhanced Details Generation (outside the form)
    if 'selected_student' in st.session_state:
        st.markdown("---")
        st.subheader("🤖 AI-Powered Assistance")
        
        col_ai1, col_ai2, col_ai3 = st.columns([2, 1, 1])
        
        with col_ai1:
            st.write("Generate personalized intervention details using AI based on student context.")
        
        with col_ai2:
            # Create a mini form for AI generation parameters
            with st.popover("🤖 Generate AI Details"):
                st.write("**Configure AI Generation:**")
                ai_intervention_type = st.selectbox(
                    "Intervention Type for AI",
                    [
                        "Academic Meeting",
                        "Study Plan Assignment", 
                        "Tutoring Referral",
                        "Counseling Referral",
                        "Financial Aid Consultation",
                        "Career Guidance Session",
                        "Peer Mentoring Program",
                        "Academic Probation Review"
                    ],
                    key="ai_intervention_type"
                )
                ai_priority = st.selectbox("Priority Level", ["High", "Medium", "Low"], key="ai_priority")
                
                if st.button("Generate Details", key="generate_ai_details_btn"):
                    with st.spinner("Generating AI-enhanced details..."):
                        student_data = {
                            'student_id': st.session_state.selected_student,
                            'full_name': st.session_state.get('selected_student_name', ''),
                            'major': st.session_state.get('selected_student_major', ''),
                            'year_level': st.session_state.get('selected_student_year', ''),
                            'gpa': st.session_state.get('selected_student_gpa', 0.0),
                            'risk_category': st.session_state.get('selected_student_risk', ''),
                            'courses_enrolled': 5,  # Default values - could be enhanced
                            'failing_grades': 1 if st.session_state.get('selected_student_risk') == 'High Risk' else 0
                        }
                        
                        ai_details = generate_personalized_intervention_details(
                            ai_intervention_type, student_data, ai_priority
                        )
                        
                        # Store AI-generated details and intervention type in session state
                        st.session_state['ai_generated_details'] = ai_details
                        st.session_state['ai_selected_intervention_type'] = ai_intervention_type
                        st.session_state['ai_selected_priority'] = ai_priority
                        st.success("✅ AI details generated! They will be pre-filled in the form below.")
                        st.rerun()
        
        with col_ai3:
            if 'ai_generated_details' in st.session_state:
                if st.button("🗑️ Clear AI Details"):
                    # Clear all AI-related session state
                    for key in ['ai_generated_details', 'ai_selected_intervention_type', 'ai_selected_priority']:
                        if key in st.session_state:
                            del st.session_state[key]
                    st.rerun()
        
        
        st.markdown("---")
        
    with st.form("intervention_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            student_id = st.text_input("Student ID", value=default_student_id)
            
            # Use AI-generated intervention type as default if available
            intervention_options = [
                "Academic Meeting",
                "Study Plan Assignment", 
                "Tutoring Referral",
                "Counseling Referral",
                "Financial Aid Consultation",
                "Career Guidance Session",
                "Peer Mentoring Program",
                "Academic Probation Review"
            ]
            
            default_intervention_index = 0
            if 'ai_selected_intervention_type' in st.session_state:
                ai_type = st.session_state['ai_selected_intervention_type']
                if ai_type in intervention_options:
                    default_intervention_index = intervention_options.index(ai_type)
            
            intervention_type = st.selectbox(
                "Intervention Type",
                intervention_options,
                index=default_intervention_index
            )
        
        with col2:
            # Get user email and make it uneditable
            user_email, _ = get_user_credentials()
            created_by = st.text_input("Created By (Email)", value=user_email, disabled=True)
            
            # Use AI-generated priority as default if available
            priority_options = ["High", "Medium", "Low"]
            default_priority_index = 1  # Default to Medium
            if 'ai_selected_priority' in st.session_state:
                ai_priority = st.session_state['ai_selected_priority']
                if ai_priority in priority_options:
                    default_priority_index = priority_options.index(ai_priority)
            
            priority = st.selectbox("Priority", priority_options, index=default_priority_index)
        
        # Intervention details based on type
        st.subheader("Intervention Details")
        
        # Check if we have AI-generated details to pre-populate text areas
        ai_details = st.session_state.get('ai_generated_details', '')
        
        # Show indicator if AI details are being used
        if ai_details:
            st.info("🤖 AI-generated details are pre-filled below. You can edit them as needed.")
        
        if intervention_type == "Academic Meeting":
            # Use AI-generated meeting details if available
            ai_meeting_details = st.session_state.get('ai_meeting_details', {})
            
            # Pre-select meeting type from AI recommendation
            meeting_type_options = ["In-Person", "Virtual", "Phone"]
            default_meeting_type_index = 0
            if ai_meeting_details.get('meeting_type') in meeting_type_options:
                default_meeting_type_index = meeting_type_options.index(ai_meeting_details['meeting_type'])
            
            meeting_type = st.selectbox("Meeting Type", meeting_type_options, index=default_meeting_type_index)
            
            # Pre-populate date and time from AI recommendation
            default_date = ai_meeting_details.get('meeting_date', None)
            default_time = ai_meeting_details.get('meeting_time', None)
            
            meeting_date = st.date_input("Proposed Meeting Date", value=default_date)
            meeting_time = st.time_input("Proposed Meeting Time", value=default_time)
            
            # Use AI-generated agenda if available, otherwise use general AI details
            agenda_text = ai_meeting_details.get('agenda', ai_details)
            
            # Format the agenda text for better display
            formatted_agenda = format_intervention_details_for_display(agenda_text)
            
            
            agenda = st.text_area("Meeting Agenda", 
                                value=formatted_agenda,
                                placeholder="Discuss academic performance, identify challenges, create action plan...",
                                height=200)
            details = f"Meeting Type: {meeting_type}, Date: {meeting_date}, Time: {meeting_time}, Agenda: {agenda}"
            
        elif intervention_type == "Study Plan Assignment":
            study_duration = st.selectbox("Study Plan Duration", ["2 weeks", "1 month", "1 semester"])
            focus_areas = st.multiselect("Focus Areas", ["Time Management", "Note Taking", "Test Preparation", "Research Skills", "Writing Skills"])
                
            goals = st.text_area("Specific Goals", 
                               value=format_intervention_details_for_display(ai_details),
                               placeholder="Improve GPA to 2.5, complete all assignments on time...")
            details = f"Duration: {study_duration}, Focus Areas: {', '.join(focus_areas)}, Goals: {goals}"
            
        elif intervention_type == "Tutoring Referral":
            subjects = st.text_input("Subjects Needing Tutoring")
            tutoring_type = st.selectbox("Tutoring Type", ["Individual", "Group", "Online"])
            frequency = st.selectbox("Frequency", ["Once a week", "Twice a week", "Three times a week"])
                
            tutor_notes = st.text_area("Additional Tutoring Details", 
                                     value=format_intervention_details_for_display(ai_details),
                                     placeholder="Specific tutoring requirements, learning objectives...")
            details = f"Subjects: {subjects}, Type: {tutoring_type}, Frequency: {frequency}, Additional Details: {tutor_notes}"
            
        elif intervention_type == "Counseling Referral":
            counseling_type = st.selectbox("Counseling Type", ["Academic", "Personal", "Career", "Mental Health"])
            urgency = st.selectbox("Urgency", ["Immediate", "Within a week", "Within a month"])
                
            reason = st.text_area("Reason for Referral", 
                                value=format_intervention_details_for_display(ai_details),
                                placeholder="Describe the specific concerns and referral reasons...")
            details = f"Type: {counseling_type}, Urgency: {urgency}, Reason: {reason}"
            
        else:
            # For other intervention types, use the full AI details
            details = st.text_area("Intervention Details", 
                                 value=format_intervention_details_for_display(ai_details),
                                 placeholder="Provide specific details about the intervention...")
        
        # Additional notes section
        additional_notes = st.text_area("Additional Notes", 
                                       placeholder="Any additional information or special considerations...",
                                       height=200)
        
        # Combine all details
        full_details = f"Priority: {priority}\nDetails: {details}\nAdditional Notes: {additional_notes}"
        
        submitted = st.form_submit_button("Submit Intervention", type="primary")
        
        if submitted:
            if student_id and intervention_type and created_by:
                try:
                    submit_intervention(student_id, intervention_type, full_details, created_by)
                    st.success(f"✅ Intervention created successfully for Student ID: {student_id}")
                    st.balloons()
                    
                    # Clear session state
                    for key in ['selected_student', 'selected_student_name', 'selected_student_major', 
                               'selected_student_year', 'selected_student_gpa', 'selected_student_risk',
                               'ai_recommendations', 'ai_generated_details', 'ai_selected_intervention_type', 
                               'ai_selected_priority', 'parsed_ai_recommendations', 'ai_meeting_details']:
                        if key in st.session_state:
                            del st.session_state[key]
                        
                except Exception as e:
                    st.error(f"Error submitting intervention: {str(e)}")
            else:
                st.error("Please fill in all required fields.")



def show_scheduled_remediations():
    st.header("📅 Scheduled Remediations")
    st.markdown("---")
    
    try:
        # Load scheduled remediations
        with st.spinner("Loading scheduled remediations..."):
            df = load_scheduled_remediations()
        
        if df.empty:
            st.info("📋 No scheduled remediations found.")
            st.markdown("All interventions have been completed or no interventions have been created yet.")
            return
        
        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            total_remediations = len(df)
            st.metric("Total Scheduled", total_remediations)
        
        with col2:
            high_priority = len(df[df['intervention_details'].str.contains('Priority: High', na=False)])
            st.metric("High Priority", high_priority, delta=f"{high_priority/total_remediations*100:.1f}%")
        
        with col3:
            medium_priority = len(df[df['intervention_details'].str.contains('Priority: Medium', na=False)])
            st.metric("Medium Priority", medium_priority, delta=f"{medium_priority/total_remediations*100:.1f}%")
        
        with col4:
            low_priority = len(df[df['intervention_details'].str.contains('Priority: Low', na=False)])
            st.metric("Low Priority", low_priority, delta=f"{low_priority/total_remediations*100:.1f}%")
        
        st.markdown("---")
        
        # Priority color legend
        st.markdown("**Priority Color Key:**")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #FF4B4B; margin-right: 8px; border-radius: 3px;"></div><span>High Priority</span></div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #FFA500; margin-right: 8px; border-radius: 3px;"></div><span>Medium Priority</span></div>', unsafe_allow_html=True)
        
        with col3:
            st.markdown(f'<div style="display: flex; align-items: center;"><div style="width: 20px; height: 20px; background-color: #00CC88; margin-right: 8px; border-radius: 3px;"></div><span>Low Priority</span></div>', unsafe_allow_html=True)
        
        st.markdown("---")
        
        # Display remediations
        st.subheader(f"Scheduled Interventions ({len(df)} items)")
        
        for idx, remediation in df.iterrows():
            # Extract priority from intervention details
            priority = "Medium"  # Default
            if "Priority: High" in str(remediation['intervention_details']):
                priority = "High"
            elif "Priority: Low" in str(remediation['intervention_details']):
                priority = "Low"
            
            priority_color = get_priority_color(priority)
            
            with st.container():
                col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2.5])
                
                with col1:
                    st.markdown(f"""
                    <div style="padding: 15px; border-left: 4px solid {priority_color}; margin: 10px 0; background-color: {priority_color}10; border-radius: 5px;">
                        <h4 style="margin: 0; color: {priority_color};">{remediation['intervention_type']}</h4>
                        <p style="margin: 5px 0; color: gray;"><strong>Student ID:</strong> {remediation['student_id']}</p>
                        <p style="margin: 5px 0; color: gray;"><strong>Priority:</strong> {priority}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.write(f"**Created:** {remediation['created_date'].strftime('%Y-%m-%d %H:%M')}")
                    st.write(f"**Status:** {remediation['status']}")
                
                with col3:
                    st.write(f"**Created By:** {remediation['created_by']}")
                
                with col4:
                    if st.button("View Details", key=f"view_{idx}", use_container_width=True):
                        with st.expander("Intervention Details", expanded=True):
                            st.text_area("Full Details", value=remediation['intervention_details'], height=200, disabled=True)
                    
                    if st.button("Mark Complete", key=f"complete_{idx}", use_container_width=True):
                        # Update status to completed
                        try:
                            with get_connection(DATABASE_REMEDIATION_DATA) as conn:
                                with conn.cursor() as cur:
                                    update_query = """
                                    UPDATE public.student_interventions 
                                    SET status = 'Completed' 
                                    WHERE student_id = %s AND created_date = %s
                                    """
                                    cur.execute(update_query, (remediation['student_id'], remediation['created_date']))
                                    conn.commit()
                            st.success("✅ Intervention marked as completed!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error updating intervention: {str(e)}")
        
    except Exception as e:
        st.error(f"Error loading scheduled remediations: {str(e)}")
        st.info("Please check your database connection and permissions.")

if __name__ == "__main__":
    main() 