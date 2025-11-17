import streamlit as st
import psycopg
import pandas as pd
import os
# import time
from databricks import sdk
from databricks.sdk import WorkspaceClient
from databricks_ai_bridge import ModelServingUserCredentials
import plotly.express as px
from dotenv import load_dotenv
import logging
import requests
import json
from typing import Dict, List, Optional
from model_serving_utils import query_endpoint_stream, _get_endpoint_task_type
from mlflow.types.responses import ResponsesAgentStreamEvent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database configuration variables
DATABASE_REMEDIATION_DATA = os.getenv("DATABASE_REMEDIATION_DATA", "akshay_student_remediation_db")

# Databricks Model Serving Endpoint Configuration
SERVING_ENDPOINT = os.getenv("SERVING_ENDPOINT")

def get_user_credentials():
    """Get user authorization credentials from Streamlit headers"""
    user_email = st.context.headers.get('x-forwarded-email')
    user_token = st.context.headers.get('x-forwarded-access-token')
    
    logger.info(f"User email: {user_email}")
    logger.info(f"User token present: {bool(user_token)}")
    
    if not user_token:
        st.error("❌ User authorization token not found. Please ensure the app has proper user authorization scopes configured.")
        st.info("This app requires user authorization to access your data with your permissions.")
        st.stop()
    
    return user_email, user_token

logger.info(f"DATABASE_REMEDIATION_DATA: {DATABASE_REMEDIATION_DATA}")

# Database connection setup - using user authorization with direct connections

def get_postgres_password():
    """Get PostgreSQL password using user authorization token"""
    try:
        user_email, user_token = get_user_credentials()
        logger.info("Using user authorization token for PostgreSQL connection")
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
        
        logger.info(f"Creating direct connection to {dbname} for user {user_email}")
        logger.info(f"Connection string (password hidden): dbname={dbname} user={user_email} host={os.getenv('PGHOST')} port={os.getenv('PGPORT')}")
        
        conn = psycopg.connect(conn_string)
        
        # Test the connection and log current user
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SELECT current_user, session_user")
            current_user, session_user = cur.fetchone()
            logger.info(f"Connected as current_user: {current_user}, session_user: {session_user}")
        
        return conn
        
    except Exception as e:
        logger.error(f"Failed to get database connection: {str(e)}")
        st.error(f"❌ Database connection failed: {str(e)}")
        st.info("Please ensure you have proper permissions to access the database.")
        st.stop()


# LLM-Powered Intervention Recommendation Functions

def extract_useful_text_from_structured_response(content_list) -> Optional[str]:
    """Extract useful recommendation text from multi-agent-supervisor structured response"""
    try:
        # First, try to find the final text component from the last element
        # This handles the specific format: [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]
        if content_list and len(content_list) > 0:
            # Look for the last element with type 'text'
            for item in reversed(content_list):
                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                    logger.info(f"Found final text component: {item['text'][:100]}...")
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
        logger.info(f"Error extracting structured response: {e}")
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

def call_databricks_serving_endpoint(prompt: str, max_tokens: int = 500, response_format: Optional[Dict] = None) -> Optional[Dict]:
    """Call Databricks multi-agent-supervisor endpoint using OBO authentication
    
    The multi-agent-supervisor (MAS) endpoint has access to:
    - Genie: For data retrieval and analysis
    - KA endpoint: For knowledge augmentation
    
    Uses On-Behalf-Of (OBO) authentication with ModelServingUserCredentials to call
    the endpoint with the current user's permissions.
    
    MAS uses a different API schema than OpenAI format:
    - 'input' instead of 'messages'
    - 'max_output_tokens' instead of 'max_tokens'
    
    Returns:
        Dict with keys:
        - 'content': The final response text
        - 'thinking_process': List of thinking/reasoning stages
        - 'tool_calls': List of tool use steps
        - 'raw_response': The raw response data for debugging
    """
    if not SERVING_ENDPOINT:
        logger.warning("Serving endpoint not configured")
        return None
    
    try:
        logger.info(f"Calling multi-agent-supervisor endpoint: {SERVING_ENDPOINT}")
        
        # Get user credentials for logging
        user_email, _ = get_user_credentials()
        logger.info(f"Using OBO authentication for user: {user_email}")
        
        # Configure WorkspaceClient with OBO authentication
        # This automatically uses the user's OAuth token from the request headers
        user_client = WorkspaceClient(credentials_strategy=ModelServingUserCredentials())
        
        logger.info(f"WorkspaceClient configured with ModelServingUserCredentials (OBO)")
        logger.info(f"MAS endpoint host: {user_client.config.host}")
        
        # Prepare the request payload using MAS-specific schema
        # MAS expects 'input' as an array, we'll pass the messages in the format it understands
        dataframe_records = [{
            "input": [{"role": "user", "content": prompt}],
            "max_output_tokens": max_tokens,
            "temperature": 0.7
        }]
        
        # Add response format handling if needed for structured outputs
        # MAS may need this in a different format - we'll pass it as custom_inputs
        if response_format:
            logger.info(f"Including response_format in custom_inputs")
            dataframe_records[0]["custom_inputs"] = {
                "response_format": response_format
            }
        
        logger.info(f"MAS request payload: {json.dumps(dataframe_records, indent=2)}")
        
        # Call the serving endpoint using the SDK with OBO authentication
        logger.info(f"Calling serving endpoint with OBO credentials...")
        
        try:
            response = user_client.serving_endpoints.query(
                name=SERVING_ENDPOINT,
                dataframe_records=dataframe_records
            )
            
            logger.info(f"Response received successfully from MAS")
            logger.info(f"Response type: {type(response)}")
            logger.info(f"Response object: {response}")
            
        except Exception as query_error:
            logger.error(f"Error calling serving endpoint query: {str(query_error)}")
            logger.info(f"Full query error details: {type(query_error).__name__}: {query_error}")
            
            # Check if it's a permissions issue
            if "no permissions" in str(query_error).lower() or "403" in str(query_error):
                logger.warning("User lacks permissions to access the MAS endpoint")
                return None
            raise
        
        # Parse the response
        # The SDK returns a response object, extract the content
        if hasattr(response, 'predictions') and response.predictions:
            logger.info(f"Found predictions in response: {len(response.predictions)} items")
            response_data = response.predictions
        elif hasattr(response, 'choices') and response.choices:
            logger.info(f"Found choices in response: {len(response.choices)} items")
            response_data = response.choices
        else:
            # Try to convert response to dict/json
            logger.info("Converting response to dict")
            response_data = response.__dict__ if hasattr(response, '__dict__') else str(response)
        
        logger.info(f"Response data type: {type(response_data)}")
        logger.info(f"Response data preview: {str(response_data)[:500]}...")
        
        # Extract content from MAS response
        content = None
        
        # Try common response patterns
        if isinstance(response_data, list) and len(response_data) > 0:
            # Response is a list, process the first element
            first_item = response_data[0]
            logger.info(f"Processing first item from list: {type(first_item)}")
            
            if isinstance(first_item, dict):
                # Try common keys
                if 'text' in first_item:
                    content = first_item['text']
                elif 'content' in first_item:
                    content = first_item['content']
                elif 'output' in first_item:
                    content = first_item['output']
                elif 'response' in first_item:
                    content = first_item['response']
                elif 'message' in first_item:
                    msg = first_item['message']
                    if isinstance(msg, dict) and 'content' in msg:
                        content = msg['content']
                    else:
                        content = str(msg)
                else:
                    content = str(first_item)
            else:
                content = str(first_item)
            
            logger.info(f"Extracted content from list format")
        
        elif isinstance(response_data, dict):
            # Try common response keys
            if 'predictions' in response_data and len(response_data['predictions']) > 0:
                content = response_data['predictions'][0]
                logger.info(f"Extracted content from 'predictions' field")
            elif 'choices' in response_data and len(response_data['choices']) > 0:
                first_choice = response_data['choices'][0]
                if isinstance(first_choice, dict) and 'message' in first_choice:
                    content = first_choice['message'].get('content', str(first_choice))
                else:
                    content = str(first_choice)
                logger.info(f"Extracted content from 'choices' format")
            elif 'output' in response_data:
                content = response_data['output']
                logger.info(f"Extracted content from 'output' field")
            elif 'text' in response_data:
                content = response_data['text']
                logger.info(f"Extracted content from 'text' field")
            elif 'response' in response_data:
                content = response_data['response']
                logger.info(f"Extracted content from 'response' field")
            else:
                content = str(response_data)
                logger.info(f"Converting entire dict to string")
        
        elif isinstance(response_data, str):
            content = response_data
            logger.info(f"Response is already a string")
        
        else:
            content = str(response_data)
            logger.info(f"Converting response to string")
        
        logger.info(f"Extracted content type: {type(content)}")
        logger.info(f"Content preview: {str(content)[:200] if content else 'None'}...")
        
        # Initialize result structure
        result = {
            'content': '',
            'thinking_process': [],
            'tool_calls': [],
            'raw_response': response_data
        }
        
        # Extract thinking process and tool calls from the full response
        thinking_stages = []
        tool_use_steps = []
        final_content = ''
        
        # Process the content based on type
        if content is None:
            logger.warning("MAS returned None content")
            return result
        
        elif isinstance(content, list):
            logger.info(f"Processing streaming response with {len(content)} message blocks")
            logger.info(f"Full content list: {content}")
            
            # Process messages sequentially, similar to streaming chat messages
            # The response is a stream of messages/blocks that need to be processed in order
            for idx, message_block in enumerate(content):
                logger.info(f"Processing block {idx}: type={type(message_block)}, preview={str(message_block)[:200]}")
                
                if not isinstance(message_block, dict):
                    # Handle string blocks as direct content
                    if isinstance(message_block, str) and message_block.strip():
                        final_content = message_block
                        logger.info(f"Found string block: {message_block[:100]}")
                    continue
                
                # Extract block type and role
                block_type = message_block.get('type', '')
                block_role = message_block.get('role', '')
                
                # === Pattern 1: Handle thinking/reasoning blocks ===
                if block_type in ['thinking', 'reasoning']:
                    thinking_content = message_block.get('text', message_block.get('content', ''))
                    if thinking_content:
                        thinking_stages.append({
                            'step': len(thinking_stages) + 1,
                            'content': str(thinking_content)
                        })
                        logger.info(f"✓ Thinking stage {len(thinking_stages)}: {str(thinking_content)[:100]}")
                
                # === Pattern 2: Handle tool_use blocks (tool calls) ===
                elif block_type == 'tool_use':
                    tool_name = message_block.get('name', 'Unknown Tool')
                    tool_input = message_block.get('input', {})
                    tool_id = message_block.get('id', '')
                    
                    tool_use_steps.append({
                        'step': len(tool_use_steps) + 1,
                        'tool': tool_name,
                        'input': tool_input,
                        'output': None,  # Will be populated when we find the result
                        'call_id': tool_id
                    })
                    logger.info(f"✓ Tool call {len(tool_use_steps)}: {tool_name} (id: {tool_id})")
                
                # === Pattern 3: Handle tool_result/function_call_output blocks ===
                elif block_type in ['tool_result', 'function_call_output', 'tool_call_output']:
                    call_id = message_block.get('call_id', message_block.get('tool_call_id', ''))
                    tool_output = message_block.get('output', message_block.get('content', ''))
                    
                    # Match with corresponding tool call
                    matched = False
                    for tool_step in tool_use_steps:
                        if tool_step.get('call_id') == call_id:
                            tool_step['output'] = tool_output
                            matched = True
                            logger.info(f"✓ Matched output for tool call {call_id}: {str(tool_output)[:100]}")
                            break
                    
                    if not matched and tool_output:
                        # Orphan tool output (e.g., handoff messages)
                        tool_use_steps.append({
                            'step': len(tool_use_steps) + 1,
                            'tool': 'System (handoff)',
                            'input': '',
                            'output': tool_output,
                            'call_id': call_id
                        })
                        logger.info(f"✓ Orphan tool output: {str(tool_output)[:100]}")
                
                # === Pattern 4: Handle text blocks (final content) ===
                elif block_type == 'text':
                    text_content = message_block.get('text', message_block.get('content', ''))
                    if text_content and str(text_content).strip():
                        # Always take the LAST text block as final content (overwrite previous)
                        final_content = str(text_content).strip()
                        logger.info(f"✓ Text block: {final_content[:100]}")
                
                # === Pattern 5: Handle role-based message blocks (assistant/tool) ===
                elif block_role in ['assistant', 'tool']:
                    msg_content = message_block.get('content', '')
                    
                    # Check if content is a list (nested blocks)
                    if isinstance(msg_content, list):
                        logger.info(f"Processing nested content list with {len(msg_content)} items")
                        for nested_item in msg_content:
                            if isinstance(nested_item, dict):
                                nested_type = nested_item.get('type', '')
                                
                                # Recursively handle nested thinking
                                if nested_type in ['thinking', 'reasoning']:
                                    nested_text = nested_item.get('text', nested_item.get('content', ''))
                                    if nested_text:
                                        thinking_stages.append({
                                            'step': len(thinking_stages) + 1,
                                            'content': str(nested_text)
                                        })
                                
                                # Recursively handle nested tool use
                                elif nested_type == 'tool_use':
                                    tool_use_steps.append({
                                        'step': len(tool_use_steps) + 1,
                                        'tool': nested_item.get('name', 'Unknown'),
                                        'input': nested_item.get('input', {}),
                                        'output': None,
                                        'call_id': nested_item.get('id', '')
                                    })
                                
                                # Recursively handle nested text (final content)
                                elif nested_type == 'text':
                                    nested_text = nested_item.get('text', nested_item.get('content', ''))
                                    if nested_text and str(nested_text).strip():
                                        final_content = str(nested_text).strip()
                    
                    # Check if content is a string
                    elif isinstance(msg_content, str) and msg_content.strip():
                        final_content = msg_content.strip()
                        logger.info(f"✓ Role-based content: {final_content[:100]}")
                    
                    # Also check for tool_calls in assistant messages
                    if 'tool_calls' in message_block and message_block['tool_calls']:
                        for tool_call in message_block['tool_calls']:
                            fn_data = tool_call.get('function', {})
                            tool_use_steps.append({
                                'step': len(tool_use_steps) + 1,
                                'tool': fn_data.get('name', 'Unknown'),
                                'input': fn_data.get('arguments', {}),
                                'output': None,
                                'call_id': tool_call.get('id', '')
                            })
            
            # Final validation: ensure we have content
            if not final_content:
                logger.warning("No final content found after processing all blocks")
                # Try structured extraction as last resort
                extracted_text = extract_useful_text_from_structured_response(content)
                if extracted_text:
                    final_content = extracted_text
                    logger.info(f"✓ Fallback extraction: {extracted_text[:100]}")
                else:
                    # Ultimate fallback: concatenate all text blocks
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text = block.get('text', block.get('content', ''))
                            if text and str(text).strip():
                                text_parts.append(str(text).strip())
                    final_content = '\n\n'.join(text_parts) if text_parts else json.dumps(content)
                    logger.info(f"✓ Ultimate fallback: {final_content[:100]}")
            
            logger.info(f"Processing complete: {len(thinking_stages)} thinking stages, {len(tool_use_steps)} tool calls, final content length: {len(final_content)}")
            
        elif isinstance(content, dict):
            logger.info(f"Processing dict content from MAS")
            
            # Extract thinking/reasoning if present
            if 'thinking' in content or 'reasoning' in content:
                thinking_data = content.get('thinking', content.get('reasoning'))
                if isinstance(thinking_data, list):
                    thinking_stages = [{'step': i+1, 'content': str(t)} for i, t in enumerate(thinking_data)]
                elif thinking_data:
                    thinking_stages = [{'step': 1, 'content': str(thinking_data)}]
            
            # Extract tool calls if present
            if 'tool_calls' in content:
                tool_calls_data = content.get('tool_calls', [])
                if isinstance(tool_calls_data, list):
                    tool_use_steps = [
                        {
                            'step': i+1,
                            'tool': tc.get('tool_name', tc.get('name', 'Unknown')),
                            'input': tc.get('input', tc.get('arguments', '')),
                            'output': tc.get('output', tc.get('result', ''))
                        }
                        for i, tc in enumerate(tool_calls_data)
                    ]
            
            # Extract final content
            if 'text' in content:
                final_content = str(content['text'])
            elif 'content' in content:
                final_content = str(content['content'])
            elif 'output' in content:
                final_content = str(content['output'])
            elif 'response' in content:
                final_content = str(content['response'])
            else:
                # Return JSON string representation
                final_content = json.dumps(content)
        
        elif isinstance(content, str):
            logger.info(f"Processing string content from MAS")
            final_content = content
        else:
            logger.info(f"Processing other type content from MAS: {type(content)}")
            final_content = str(content)
        
        # Populate result
        result['content'] = final_content.strip() if final_content else ''
        result['thinking_process'] = thinking_stages
        result['tool_calls'] = tool_use_steps
        
        logger.info(f"Extracted {len(thinking_stages)} thinking stages and {len(tool_use_steps)} tool calls")
        
        return result
        
    except Exception as e:
        logger.error(f"Error calling multi-agent-supervisor endpoint: {str(e)}")
        logger.info(f"Full error details: {type(e).__name__}: {e}")
        import traceback
        logger.info(f"Traceback: {traceback.format_exc()}")
        # Return empty dict structure instead of None
        return {
            'content': '',
            'thinking_process': [],
            'tool_calls': [],
            'raw_response': None
        }


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
- Choose intervention modality from: In-Person, Virtual, Phone
- Set priority: High, Medium, or Low
- Write brief action explaining why this specific student needs this intervention based on the suggested intervention type and modality historical performance data
- Specify timeline for implementation
- Define measurable goal specific to this student's situation
- Add some best practices for each intervention type and modality suggested based on the unstructured best practices documents available. 

Respond with a JSON object containing an array of 3 recommendations and the information above.
"""

    # Call the LLM with structured output
    llm_response = call_databricks_serving_endpoint(prompt, max_tokens=800, response_format=response_format)
    
    # Check if response is valid
    if not llm_response or not isinstance(llm_response, dict):
        logger.error(f"Invalid LLM response: type={type(llm_response)}, value={llm_response}")
        # Return empty result if LLM fails
        return {
            "llm_recommendations": "AI recommendations are currently unavailable. Please try again later.",
            "structured_recommendations": [],
            "thinking_process": [],
            "tool_calls": [],
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "llm_unavailable"
        }
    
    # Extract content, thinking, and tool calls from response
    response_content = llm_response.get('content', '')
    thinking_process = llm_response.get('thinking_process', [])
    tool_calls = llm_response.get('tool_calls', [])
    
    # If no content, return error
    if not response_content or not response_content.strip():
        logger.warning("LLM response has no content")
        return {
            "llm_recommendations": "AI returned an empty response. Please try again.",
            "structured_recommendations": [],
            "thinking_process": thinking_process,
            "tool_calls": tool_calls,
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "llm_empty_response"
        }
    
    try:
        # Parse the structured JSON response
        import json
        structured_data = json.loads(response_content)
        
        # Handle both dict and list formats
        if isinstance(structured_data, dict):
            recommendations = structured_data.get("recommendations", [])
        elif isinstance(structured_data, list):
            # If it's already a list, assume it's the recommendations array
            recommendations = structured_data
        else:
            logger.warning(f"Unexpected structured_data type: {type(structured_data)}")
            recommendations = []
        
        # Validate we have recommendations
        if not recommendations or not isinstance(recommendations, list):
            logger.warning(f"No valid recommendations found: {recommendations}")
            raise ValueError("No valid recommendations in response")
        
        # Format the structured data for display
        formatted_text = ""
        for i, rec in enumerate(recommendations, 1):
            if not isinstance(rec, dict):
                logger.warning(f"Recommendation {i} is not a dict: {type(rec)}")
                continue
                
            formatted_text += f"{i}. {rec.get('intervention_type', 'Unknown')} - Priority: {rec.get('priority', 'Medium')}\n\n"
            formatted_text += f"Action: {rec.get('action', 'N/A')}\n\n"
            formatted_text += f"Timeline: {rec.get('timeline', 'N/A')}\n\n"
            formatted_text += f"Goal: {rec.get('goal', 'N/A')}\n\n"
            if i < len(recommendations):
                formatted_text += "\n"
        
        return {
            "llm_recommendations": formatted_text.strip(),
            "structured_recommendations": recommendations,
            "thinking_process": thinking_process,
            "tool_calls": tool_calls,
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "multi_agent_supervisor_structured"
        }
        
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Failed to parse structured response: {e}")
        logger.info(f"Response content that failed: {response_content[:500]}")
        # Fallback to original text processing
        cleaned_response = clean_ai_response(response_content)
        
        return {
            "llm_recommendations": cleaned_response,
            "structured_recommendations": [],
            "thinking_process": thinking_process,
            "tool_calls": tool_calls,
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "multi_agent_supervisor_fallback"
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
    
    if llm_response and isinstance(llm_response, dict):
        # Extract content from response
        response_content = llm_response.get('content', '')
        if response_content and response_content.strip():
            return f"Priority: {priority}\n\n{response_content}"
    
    # Fallback if no valid response
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
        logger.info(f"Error parsing AI recommendations: {e}")
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
        {"name": "AI Recommendations", "icon": "🤖"},
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
    elif page == "AI Recommendations":
        show_ai_recommendations_page()
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
                st.write(f"**Model Type:** Multi-Agent-Supervisor (MAS)")
                st.write(f"**Agent Capabilities:** Genie (data retrieval), KA endpoint (knowledge augmentation)")
                st.write(f"**Client Type:** Databricks SDK (serving_endpoints.query)")
                st.write(f"**Authentication:** On-Behalf-Of (OBO) with ModelServingUserCredentials")
                st.write(f"**API Format:** dataframe_records with input/max_output_tokens")
                st.write(f"**Features:** Multi-agent orchestration, tool calling, data-grounded responses")
                st.info("ℹ️ Using OBO authentication automatically handles user permissions for the serving endpoint.")
                
                if st.button("Test MAS Endpoint"):
                    if SERVING_ENDPOINT:
                        with st.spinner("Testing Multi-Agent-Supervisor endpoint with OBO authentication..."):
                            test_response = call_databricks_serving_endpoint("Hello, this is a test.", max_tokens=50)
                            if test_response:
                                st.success("✅ Multi-Agent-Supervisor endpoint is working!")
                                if isinstance(test_response, dict):
                                    st.write(f"**Test Response:** {test_response.get('content', str(test_response))}")
                                else:
                                    st.write(f"**Test Response:** {test_response}")
                            else:
                                st.error("❌ MAS endpoint test failed - check permissions or endpoint configuration")
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
        
        # Student list with dynamic count
        total_students = len(df)
        filtered_students = len(filtered_df)
        
        if filtered_students == total_students:
            st.subheader(f"Students at Risk ({filtered_students} students)")
        else:
            st.subheader(f"Students at Risk ({filtered_students}/{total_students} students)")
            if filtered_students < total_students:
                st.info(f"📋 Showing {filtered_students} of {total_students} students based on current filters")
        
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
        
        # Add sorting functionality
        st.subheader("Sort Options")
        col1, col2 = st.columns(2)
        
        with col1:
            sort_by = st.selectbox("Sort by:", 
                                 options=["Risk Level", "Surname", "GPA (Low to High)", "GPA (High to Low)", 
                                         "Failing Courses", "Student ID"],
                                 index=0)
        
        with col2:
            sort_order = st.selectbox("Order:", 
                                    options=["Default", "Ascending", "Descending"],
                                    index=0)
        
        # Apply sorting
        def extract_surname(full_name):
            """Extract surname from full name"""
            if pd.isna(full_name) or not full_name:
                return ""
            parts = str(full_name).strip().split()
            return parts[-1] if parts else ""
        
        # Add surname column for sorting
        filtered_df = filtered_df.copy()
        filtered_df['surname'] = filtered_df['full_name'].apply(extract_surname)
        
        # Sort the dataframe based on selection
        if sort_by == "Risk Level":
            # Default risk-based sorting
            risk_order = {'High Risk': 1, 'Medium Risk': 2, 'Low Risk': 3, 'Excellent': 4}
            filtered_df['risk_order'] = filtered_df['risk_category'].map(risk_order)
            if sort_order == "Ascending":
                filtered_df = filtered_df.sort_values(['risk_order', 'failing_grades'], ascending=[True, False])
            elif sort_order == "Descending":
                filtered_df = filtered_df.sort_values(['risk_order', 'failing_grades'], ascending=[False, True])
            else:
                filtered_df = filtered_df.sort_values(['risk_order', 'failing_grades'], ascending=[True, False])
        elif sort_by == "Surname":
            ascending = True if sort_order != "Descending" else False
            filtered_df = filtered_df.sort_values('surname', ascending=ascending)
        elif sort_by == "GPA (Low to High)":
            filtered_df = filtered_df.sort_values('gpa', ascending=True)
        elif sort_by == "GPA (High to Low)":
            filtered_df = filtered_df.sort_values('gpa', ascending=False)
        elif sort_by == "Failing Courses":
            ascending = False if sort_order != "Ascending" else True
            filtered_df = filtered_df.sort_values('failing_grades', ascending=ascending)
        elif sort_by == "Student ID":
            ascending = True if sort_order != "Descending" else False
            filtered_df = filtered_df.sort_values('student_id', ascending=ascending)
        
        st.markdown("---")
        
        # Display students in a more visual way
        for idx, student in filtered_df.iterrows():
            with st.container():
                col1, col2, col3 = st.columns([3, 2.5, 2])
                
                with col1:
                    risk_color = get_risk_color(student['risk_category'])
                    st.markdown(f"""
                    <div style="padding: 10px; border-left: 4px solid {risk_color}; margin: 5px 0;">
                        <h4 style="margin: 0; color: {risk_color};">{student['full_name']}</h4>
                        <p style="margin: 0; color: gray;">ID: {student['student_id']}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    # Combined student details in a single column
                    st.markdown(f"""
                    **Academic Details:**  
                    📚 Major: {student['major']}  
                    🎓 Year: {student['year_level']}  
                    📊 GPA: {student['gpa']:.2f}  
                    ⚠️ Failing: {student['failing_grades']}/{student['courses_enrolled']} courses  
                    🎯 Risk: {student['risk_category']}
                    """)
                
                with col3:
                    # Stack buttons vertically for better text readability
                    if st.button(f"🤖 AI Rec", key=f"ai_btn_{student['student_id']}", help="Get AI-powered intervention recommendations", use_container_width=True):
                        # Store student data and navigate to AI Recommendations page
                        st.session_state.ai_rec_student_id = student['student_id']
                        st.session_state.ai_rec_student_name = student['full_name']
                        st.session_state.ai_rec_student_major = student['major']
                        st.session_state.ai_rec_student_year = student['year_level']
                        st.session_state.ai_rec_student_gpa = student['gpa']
                        st.session_state.ai_rec_student_risk = student['risk_category']
                        st.session_state.ai_rec_student_failing = student['failing_grades']
                        st.session_state.ai_rec_student_enrolled = student['courses_enrolled']
                        st.session_state.ai_rec_student_data = student.to_dict()
                        st.session_state.page = "AI Recommendations"
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
                
        
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")
        st.info("Please check your database connection and credentials.")

def generate_recommendations_streaming(student_data: Dict, response_area):
    """Generate recommendations with streaming display."""
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
    
    # Create prompt
    prompt = f"""
Provide 3 concise intervention recommendations for this student. Each recommendation should directly address their specific situation.

Student: {student_data.get('full_name', 'Student')} ({student_data.get('major', 'N/A')}, {student_data.get('year_level', 'N/A')})
GPA: {student_data.get('gpa', 'N/A')} | Failing: {student_data.get('failing_grades', 0)}/{student_data.get('courses_enrolled', 0)} courses | Risk: {student_data.get('risk_category', 'N/A')}

For each recommendation:
- Choose intervention_type from: Academic Meeting, Study Plan Assignment, Tutoring Referral, Counseling Referral, Financial Aid Consultation, Career Guidance Session, Peer Mentoring Program, Academic Probation Review
- Choose intervention modality from: In-Person, Virtual, Phone
- Set priority: High, Medium, or Low
- Write brief action explaining why this specific student needs this intervention based on the suggested intervention type and modality historical performance data
- Specify timeline for implementation
- Define measurable goal specific to this student's situation
- Add some best practices for each intervention type and modality suggested based on the unstructured best practices documents available. 

Respond with a JSON object containing an array of 3 recommendations and the information above.
"""
    
    # Prepare messages
    messages = [{"role": "user", "content": prompt}]
    
    # Track all messages and tool calls
    all_messages = []
    thinking_stages = []
    tool_calls = []
    final_content = ""
    
    try:
        task_type = _get_endpoint_task_type(SERVING_ENDPOINT)
        logger.info(f"Starting streaming from endpoint: {SERVING_ENDPOINT}, task_type: {task_type}")
        
        event_count = 0
        messages_since_last_render = 0
        RENDER_EVERY_N_MESSAGES = 5  # Update display every 5 messages
        
        for raw_event in query_endpoint_stream(SERVING_ENDPOINT, messages):
            event_count += 1
            if event_count % 10 == 0:  # Log every 10 events to track progress
                logger.info(f"Processed {event_count} streaming events, {len(all_messages)} messages accumulated")
            # Extract databricks request_id if available
            if "databricks_output" in raw_event:
                req_id = raw_event["databricks_output"].get("databricks_request_id")
            
            # Parse using MLflow streaming event types
            if "type" in raw_event:
                event = ResponsesAgentStreamEvent.model_validate(raw_event)
                
                if hasattr(event, 'item') and event.item:
                    item = event.item  # This is a dict
                    
                    if item.get("type") == "message":
                        # Extract text content from message
                        content_parts = item.get("content", [])
                        for content_part in content_parts:
                            if content_part.get("type") == "output_text":
                                text = content_part.get("text", "")
                                if text:
                                    final_content += text
                                    all_messages.append({
                                        "role": "assistant",
                                        "content": text
                                    })
                                    messages_since_last_render += 1
                        
                    elif item.get("type") == "function_call":
                        # Tool call
                        call_id = item.get("call_id")
                        function_name = item.get("name")
                        arguments = item.get("arguments", "")
                        
                        tool_calls.append({
                            'call_id': call_id,
                            'tool': function_name,
                            'input': arguments,
                            'output': None
                        })
                        
                        all_messages.append({
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": function_name,
                                    "arguments": arguments
                                }
                            }]
                        })
                        messages_since_last_render += 1
                        
                    elif item.get("type") == "function_call_output":
                        # Tool call output/result
                        call_id = item.get("call_id")
                        output = item.get("output", "")
                        
                        # Log the output for debugging
                        logger.info(f"Tool output for call_id {call_id}: {str(output)[:500]}")
                        
                        # Match with tool call
                        for tool_call in tool_calls:
                            if tool_call.get('call_id') == call_id:
                                tool_call['output'] = output
                                break
                        
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
                            "call_id": call_id,  # Include call_id for reference
                            "tool_name": tool_name  # Include tool name for display
                        })
                        messages_since_last_render += 1
            
            # Render accumulated messages every N messages for progressive display
            if messages_since_last_render >= RENDER_EVERY_N_MESSAGES and all_messages:
                logger.info(f"Rendering {len(all_messages)} messages (progressive update)")
                try:
                    with response_area.container():
                        for msg in all_messages:
                            try:
                                render_streaming_message(msg)
                            except Exception as render_error:
                                logger.error(f"Error rendering message: {render_error}")
                                # Continue rendering other messages
                except Exception as container_error:
                    logger.error(f"Error with response container: {container_error}")
                messages_since_last_render = 0  # Reset counter
        
        # After streaming completes, render final update (to catch any remaining messages)
        logger.info(f"Streaming complete. Processed {event_count} events, {len(all_messages)} messages, {len(final_content)} chars of final content")
        logger.info(f"Final content preview (first 200 chars): {final_content[:200]}")
        logger.info(f"Final content preview (last 500 chars): {final_content[-500:]}")
        
        # Final render to display any remaining messages not yet shown
        if all_messages and messages_since_last_render > 0:
            logger.info(f"Final render: {len(all_messages)} total messages")
            try:
                with response_area.container():
                    for msg in all_messages:
                        try:
                            render_streaming_message(msg)
                        except Exception as render_error:
                            logger.error(f"Error rendering message: {render_error}")
                            logger.info(f"Problematic message: {str(msg)[:500]}")
                            # Continue rendering other messages
            except Exception as container_error:
                logger.error(f"Error with response container: {container_error}")
                # Don't crash, just log it
        
        # Parse final JSON content
        try:
            if not final_content or not final_content.strip():
                logger.warning("No final content received from streaming endpoint")
                return {
                    "llm_recommendations": "No response received from the AI model",
                    "structured_recommendations": [],
                    "thinking_process": thinking_stages,
                    "tool_calls": tool_calls,
                    "all_messages": all_messages,
                    "student_context": student_data,
                    "generated_at": pd.Timestamp.now().isoformat(),
                    "source": "multi_agent_supervisor_streaming_empty"
                }
            
            # Try to extract JSON from markdown code block if present
            json_content = final_content.strip()
            logger.info(f"Total final_content length: {len(json_content)} chars")
            logger.info(f"Final content ends with (last 200 chars): ...{json_content[-200:]}")
            
            # Check for markdown code fence (```json ... ``` or ``` ... ```)
            import re
            code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
            code_block_match = re.search(code_block_pattern, json_content, re.DOTALL)
            
            if code_block_match:
                json_content = code_block_match.group(1).strip()
                logger.info(f"✓ Extracted JSON from markdown code block, length: {len(json_content)} chars")
                logger.info(f"Extracted JSON preview: {json_content[:200]}")
            else:
                logger.warning("✗ No markdown code block found in final_content")
                # Try to find JSON object directly
                json_object_pattern = r'\{[\s\S]*"recommendations"[\s\S]*\}'
                json_object_match = re.search(json_object_pattern, json_content)
                if json_object_match:
                    json_content = json_object_match.group(0).strip()
                    logger.info(f"✓ Found JSON object directly, length: {len(json_content)} chars")
                else:
                    logger.warning("✗ Could not find JSON object in final_content either")
            
            # Also check for inline code blocks (` ... `)
            if not code_block_match and json_content.startswith('`') and json_content.endswith('`'):
                json_content = json_content.strip('`').strip()
                logger.info("Extracted JSON from inline code block")
            
            logger.info(f"Attempting to parse JSON content (first 300 chars): {json_content[:300]}")
            structured_data = json.loads(json_content)
            logger.info(f"Parsed structured data: {json.dumps(structured_data, indent=2)[:500]}")
            
            if isinstance(structured_data, dict):
                recommendations = structured_data.get("recommendations", [])
                logger.info(f"Structured data is dict. Keys: {structured_data.keys()}")
            elif isinstance(structured_data, list):
                recommendations = structured_data
                logger.info("Structured data is list")
            else:
                recommendations = []
                logger.warning(f"Structured data is unexpected type: {type(structured_data)}")
            
            logger.info(f"Extracted {len(recommendations)} recommendations from structured data")
            if len(recommendations) > 0:
                logger.info(f"First recommendation: {recommendations[0]}")
            
            # Format for display
            formatted_text = ""
            for i, rec in enumerate(recommendations, 1):
                if isinstance(rec, dict):
                    formatted_text += f"{i}. {rec.get('intervention_type', 'Unknown')} - Priority: {rec.get('priority', 'Medium')}\n\n"
                    formatted_text += f"Action: {rec.get('action', 'N/A')}\n\n"
                    formatted_text += f"Timeline: {rec.get('timeline', 'N/A')}\n\n"
                    formatted_text += f"Goal: {rec.get('goal', 'N/A')}\n\n"
            
            return {
                "llm_recommendations": formatted_text.strip() if formatted_text else final_content,
                "structured_recommendations": recommendations,
                "thinking_process": thinking_stages,
                "tool_calls": tool_calls,
                "all_messages": all_messages,
                "student_context": student_data,
                "generated_at": pd.Timestamp.now().isoformat(),
                "source": "multi_agent_supervisor_streaming"
            }
        
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse final content as JSON: {e}")
            logger.info(f"Final content was: {final_content[:500]}")
            # Fallback to text format
            return {
                "llm_recommendations": final_content,
                "structured_recommendations": [],
                "thinking_process": thinking_stages,
                "tool_calls": tool_calls,
                "all_messages": all_messages,
                "student_context": student_data,
                "generated_at": pd.Timestamp.now().isoformat(),
                "source": "multi_agent_supervisor_streaming_text"
            }
    
    except Exception as e:
        logger.error(f"Error in streaming recommendations: {str(e)}")
        import traceback
        logger.info(f"Traceback: {traceback.format_exc()}")
        
        # Return error
        return {
            "llm_recommendations": f"Error generating recommendations: {str(e)}",
            "structured_recommendations": [],
            "thinking_process": [],
            "tool_calls": [],
            "all_messages": [],
            "student_context": student_data,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "error"
        }


def parse_agent_tags(content):
    """Parse and extract agent thinking and handoff tags from content.
    
    Returns:
        dict with:
            - cleaned_content: Content with tags removed
            - thinking_blocks: List of thinking content
            - agent_names: List of agent names from <name> tags
    """
    import re
    
    if not isinstance(content, str):
        return {
            "cleaned_content": content,
            "thinking_blocks": [],
            "agent_names": []
        }
    
    thinking_blocks = []
    agent_names = []
    
    # Extract <think> blocks
    think_pattern = r'<think>(.*?)</think>'
    think_matches = re.findall(think_pattern, content, re.DOTALL | re.IGNORECASE)
    for match in think_matches:
        thinking_blocks.append(match.strip())
    
    # Extract <name> tags (agent handoffs)
    name_pattern = r'<name>(.*?)</name>'
    name_matches = re.findall(name_pattern, content, re.DOTALL | re.IGNORECASE)
    for match in name_matches:
        agent_names.append(match.strip())
    
    # Clean content by removing tags
    cleaned = content
    cleaned = re.sub(think_pattern, '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(name_pattern, '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # Clean up extra whitespace
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)
    cleaned = cleaned.strip()
    
    return {
        "cleaned_content": cleaned,
        "thinking_blocks": thinking_blocks,
        "agent_names": agent_names
    }


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
        # If no wrapper, return as is
        return tool_content
    elif isinstance(tool_content, str):
        # Try to parse as JSON to check for nested structure
        try:
            parsed = json.loads(tool_content.strip())
            if isinstance(parsed, dict):
                # Recursively extract
                return extract_tool_result_content(parsed)
            return parsed
        except json.JSONDecodeError:
            # Not JSON, return as is
            return tool_content
    else:
        return tool_content


def render_streaming_message(msg):
    """Render a single streaming message."""
    try:
        if msg["role"] == "assistant":
            # Handle content
            if msg.get("content"):
                try:
                    # Parse the content for agent tags
                    parsed = parse_agent_tags(msg["content"])
                    
                    # Show agent handoffs with emoji
                    if parsed["agent_names"]:
                        with st.chat_message("assistant", avatar="🔄"):
                            for agent_name in parsed["agent_names"]:
                                st.markdown(f"**🤖 Handing off to:** `{agent_name}`")
                    
                    # Show thinking blocks in a collapsible section
                    if parsed["thinking_blocks"]:
                        with st.chat_message("assistant", avatar="💭"):
                            with st.expander("💡 Agent Thought Process", expanded=False):
                                for idx, thinking in enumerate(parsed["thinking_blocks"], 1):
                                    if len(parsed["thinking_blocks"]) > 1:
                                        st.markdown(f"**Thought {idx}:**")
                                    st.markdown(thinking)
                                    if idx < len(parsed["thinking_blocks"]):
                                        st.markdown("---")
                    
                    # Show cleaned content if there's anything left
                    if parsed["cleaned_content"]:
                        with st.chat_message("assistant", avatar="✨"):
                            st.markdown(parsed["cleaned_content"])
                except Exception as content_error:
                    logger.warning(f"Error rendering assistant content: {content_error}")
                    with st.chat_message("assistant", avatar="✨"):
                        st.text(str(msg.get("content", ""))[:500])
            
            # Handle tool calls
            if "tool_calls" in msg and msg["tool_calls"]:
                for call in msg["tool_calls"]:
                    fn_name = call["function"]["name"]
                    args = call["function"]["arguments"]
                    
                    with st.chat_message("assistant", avatar="🔧"):
                        st.markdown(f"**Calling Tool:** `{fn_name}`")
                        
                        # Try to parse arguments as JSON
                        try:
                            if isinstance(args, str):
                                args_dict = json.loads(args)
                            else:
                                args_dict = args
                            
                            with st.expander("📥 Input", expanded=False):
                                st.json(args_dict)
                        except:
                            with st.expander("📥 Input", expanded=False):
                                st.code(str(args))
    
        elif msg["role"] == "tool":
            try:
                # Display tool response
                tool_content_raw = msg["content"]
                call_id = msg.get("call_id", "unknown")
                tool_name = msg.get("tool_name", "Unknown Tool")
                
                # Create a unique key for this tool result to avoid Streamlit key collisions
                import hashlib
                unique_key = hashlib.md5(f"{call_id}_{tool_name}".encode()).hexdigest()[:8]
                
                # Extract actual content from potentially nested structures
                tool_content = extract_tool_result_content(tool_content_raw)
                
                # Parse and clean agent tags from tool content if it's a string
                if isinstance(tool_content, str):
                    parsed_tool = parse_agent_tags(tool_content)
                    tool_content = parsed_tool["cleaned_content"]
                    
                    # If there were thinking blocks in the tool output, show them
                    if parsed_tool["thinking_blocks"]:
                        with st.chat_message("assistant", avatar="💭"):
                            with st.expander("💡 Tool's Thought Process", expanded=False):
                                for thinking in parsed_tool["thinking_blocks"]:
                                    st.markdown(thinking)
                
                with st.chat_message("assistant", avatar="📊"):
                    st.markdown(f"**Tool Result:** `{tool_name}`")
                    
                    with st.expander("📤 Output", expanded=False):
                        # Log what we're trying to render
                        logger.info(f"Rendering tool result for call_id {call_id}, tool: {tool_name}")
                        logger.info(f"Raw content type: {type(tool_content_raw)}, length: {len(str(tool_content_raw))}")
                        logger.info(f"Extracted content type: {type(tool_content)}, length: {len(str(tool_content))}")
                        
                        # Handle different content types
                        if tool_content is None or tool_content == "":
                            st.warning("⚠️ No output received from tool")
                            # Show raw content for debugging if available
                            if tool_content_raw:
                                with st.expander("🔍 Raw Response (Debug)", expanded=False):
                                    st.code(str(tool_content_raw))
                        elif isinstance(tool_content, dict):
                            # Already a dictionary - display as JSON
                            st.json(tool_content)
                        elif isinstance(tool_content, list):
                            # Already a list - display as JSON
                            st.json(tool_content)
                        elif isinstance(tool_content, str):
                            # String content - try to display nicely
                            content_stripped = tool_content.strip()
                            
                            # Try to parse as JSON first
                            try:
                                if content_stripped.startswith('{') or content_stripped.startswith('['):
                                    content_dict = json.loads(content_stripped)
                                    st.json(content_dict)
                                else:
                                    # Plain text content
                                    if len(tool_content) < 1000:
                                        st.text(tool_content)
                                    else:
                                        # Use scrollable text area for long content
                                        st.text_area("Output", tool_content, height=300, disabled=True, key=f"tool_output_{unique_key}_1")
                            except json.JSONDecodeError:
                                # Not JSON, display as text
                                if len(tool_content) < 1000:
                                    st.text(tool_content)
                                else:
                                    st.text_area("Output", tool_content, height=300, disabled=True, key=f"tool_output_{unique_key}_2")
                            except Exception as json_error:
                                # Handle any JSON parsing errors
                                logger.warning(f"Error parsing tool content: {json_error}")
                                st.warning("⚠️ Could not parse tool output")
                                st.code(str(tool_content)[:500])
                        else:
                            # Unknown type, convert to string and display
                            content_str = str(tool_content)
                            if len(content_str) < 1000:
                                st.text(content_str)
                            else:
                                st.text_area("Output", content_str, height=300, disabled=True, key=f"tool_output_{unique_key}_3")
                        
                        # If we extracted content differently, show raw for debugging
                        if str(tool_content_raw) != str(tool_content) and tool_content_raw:
                            with st.expander("🔍 Raw Response (Debug)", expanded=False):
                                try:
                                    if isinstance(tool_content_raw, (dict, list)):
                                        st.json(tool_content_raw)
                                    else:
                                        st.code(str(tool_content_raw))
                                except Exception as debug_error:
                                    logger.warning(f"Error showing raw response: {debug_error}")
                                    st.text("Could not display raw response")
            
            except Exception as tool_render_error:
                # Catch any errors in tool rendering to prevent page crash
                logger.error(f"Error rendering tool result: {tool_render_error}")
                logger.error(f"Tool: {msg.get('tool_name', 'unknown')}, Call ID: {msg.get('call_id', 'unknown')}")
                with st.chat_message("assistant", avatar="⚠️"):
                    st.error(f"Error displaying tool result: {tool_render_error}")
                    with st.expander("Debug Info", expanded=False):
                        st.code(str(msg)[:500])
    
    except Exception as msg_error:
        # Catch any top-level rendering errors
        logger.error(f"Error in render_streaming_message: {msg_error}")
        logger.info(f"Message role: {msg.get('role', 'unknown')}")
        with st.chat_message("assistant", avatar="⚠️"):
            st.error("Error rendering message")
            with st.expander("Debug", expanded=False):
                st.code(str(msg)[:500])


def show_ai_recommendations_page():
    """Show AI recommendations with streaming thought process"""
    st.header("🤖 AI-Powered Intervention Recommendations")
    
    # Check if we have student data
    if 'ai_rec_student_id' not in st.session_state:
        st.warning("No student selected. Please return to the dashboard and select a student.")
        if st.button("← Back to Dashboard"):
            st.session_state.page = "Student Risk Dashboard"
            st.rerun()
        return
    
    # Display student information
    st.subheader(f"Student: {st.session_state.ai_rec_student_name}")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Student ID", st.session_state.ai_rec_student_id)
    with col2:
        st.metric("Major", st.session_state.ai_rec_student_major)
    with col3:
        st.metric("Year", st.session_state.ai_rec_student_year)
    with col4:
        risk_color = get_risk_color(st.session_state.ai_rec_student_risk)
        st.markdown(f'<div style="padding: 10px; background-color: {risk_color}20; border-left: 4px solid {risk_color}; border-radius: 5px;"><strong>Risk:</strong> {st.session_state.ai_rec_student_risk}<br><strong>GPA:</strong> {st.session_state.ai_rec_student_gpa:.2f}</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Initialize or check for recommendations
    if 'ai_recommendations_data' not in st.session_state:
        st.session_state.ai_recommendations_data = None
        st.session_state.ai_recommendations_generating = False
    
    # Generate recommendations button
    if not st.session_state.ai_recommendations_generating and st.session_state.ai_recommendations_data is None:
        if st.button("✨ Generate AI Recommendations", type="primary", use_container_width=True):
            st.session_state.ai_recommendations_generating = True
            st.rerun()
    
    # Show streaming process
    if st.session_state.ai_recommendations_generating:
        st.markdown("### 🔄 AI Analysis in Progress...")
        st.markdown("*Watch as the AI agent analyzes the student's situation*")
        
        # Create a container for streaming messages
        response_area = st.empty()
        
        # Show initial message
        with response_area.container():
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown("_Initiating analysis..._")
        
        try:
            # Call the streaming endpoint
            student_data = st.session_state.ai_rec_student_data
            recommendations = generate_recommendations_streaming(student_data, response_area)
            
            # Store the recommendations
            st.session_state.ai_recommendations_data = recommendations
            
            # Display completion message
            st.success("✅ Analysis complete!")
        except Exception as gen_error:
            logger.error(f"Error generating recommendations: {gen_error}")
            st.error(f"❌ Error generating recommendations: {str(gen_error)}")
            st.session_state.ai_recommendations_data = None
        finally:
            # Always set generating to False to prevent infinite loops
            st.session_state.ai_recommendations_generating = False
            st.rerun()
    
    # Display results if we have them
    if st.session_state.ai_recommendations_data:
        recommendations = st.session_state.ai_recommendations_data
        
        # Debug logging
        logger.info(f"Displaying recommendations. Keys: {recommendations.keys()}")
        logger.info(f"Structured recommendations count: {len(recommendations.get('structured_recommendations', []))}")
        logger.info(f"Structured recommendations: {recommendations.get('structured_recommendations', [])}")
        
        # Display summary of structured recommendations
        if recommendations.get('structured_recommendations') and len(recommendations.get('structured_recommendations', [])) > 0:
            st.markdown("### ✨ Recommended Interventions")
            st.markdown("*Choose one of the following AI-recommended interventions to create*")
            st.markdown("---")
            
            structured_recs = recommendations['structured_recommendations']
            
            # Display each recommendation as a formatted card with action button
            for idx, rec in enumerate(structured_recs, 1):
                st.markdown(f"### 📌 Recommendation {idx}")
                
                # Create columns for type and priority
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(f"**Intervention Type:** {rec.get('intervention_type', 'N/A')}")
                with col2:
                    priority = rec.get('priority', 'Medium')
                    priority_color = {'High': '🔴', 'Medium': '🟡', 'Low': '🟢'}.get(priority, '⚪')
                    st.markdown(f"**Priority:** {priority_color} {priority}")
                
                # Display details
                if rec.get('action'):
                    st.markdown(f"**Action:**  \n{rec['action']}")
                
                if rec.get('timeline'):
                    st.markdown(f"**Timeline:** {rec['timeline']}")
                
                if rec.get('measurable_goal') or rec.get('goal'):
                    goal = rec.get('measurable_goal') or rec.get('goal')
                    st.markdown(f"**Goal:** {goal}")
                
                # Add button to create intervention from this recommendation
                button_col1, button_col2 = st.columns([1, 3])
                with button_col1:
                    if st.button(f"📝 Create Intervention", key=f"create_from_rec_{idx}", type="primary", use_container_width=True):
                        # Store the selected recommendation and student data
                        st.session_state.selected_recommendation = rec
                        st.session_state.selected_recommendation_index = idx
                        
                        # Store student data for the intervention
                        st.session_state.selected_student = st.session_state.ai_rec_student_id
                        st.session_state.selected_student_name = st.session_state.ai_rec_student_name
                        st.session_state.selected_student_major = st.session_state.ai_rec_student_major
                        st.session_state.selected_student_year = st.session_state.ai_rec_student_year
                        st.session_state.selected_student_gpa = st.session_state.ai_rec_student_gpa
                        st.session_state.selected_student_risk = st.session_state.ai_rec_student_risk
                        
                        # Store AI recommendations for the Create Intervention page
                        st.session_state.ai_recommendations = recommendations
                        
                        # Navigate to Create Intervention page
                        st.session_state.page = "Create Intervention"
                        st.rerun()
                
                # Add separator between recommendations
                if idx < len(structured_recs):
                    st.markdown("---")
            
            # Option to view raw JSON
            with st.expander("🔍 View as JSON", expanded=False):
                st.json(structured_recs)
        
        elif st.session_state.ai_recommendations_data:
            # Recommendations were generated but couldn't be parsed
            st.warning("⚠️ AI analysis completed but no structured recommendations were generated. Try regenerating or check the logs.")
            if recommendations.get('llm_recommendations'):
                with st.expander("📄 View Raw Response", expanded=False):
                    st.text(recommendations['llm_recommendations'])
    
    # Action buttons
    st.markdown("---")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        if st.button("🔄 Regenerate", use_container_width=True):
            st.session_state.ai_recommendations_data = None
            st.session_state.ai_recommendations_generating = False
            st.rerun()
    
    with col2:
        if st.button("← Back to Dashboard", use_container_width=True):
            # Clear AI recommendations data
            st.session_state.ai_recommendations_data = None
            st.session_state.ai_recommendations_generating = False
            st.session_state.page = "Student Risk Dashboard"
            st.rerun()

def show_create_intervention():
    st.header("📝 Create Student Intervention")
    
    # Check if AI recommendations are available
    ai_recommendations = st.session_state.get('ai_recommendations', None)
    if ai_recommendations:
        st.success("🤖 AI recommendations available! Use the suggestions below or create a custom intervention.")
        
        with st.expander("🤖 View AI Recommendations", expanded=True):
            # Display ONLY the structured recommendations in a formatted way
            if ai_recommendations.get('structured_recommendations') and len(ai_recommendations.get('structured_recommendations', [])) > 0:
                recommendations = ai_recommendations['structured_recommendations']
                
                # Display each recommendation as a formatted card
                for idx, rec in enumerate(recommendations, 1):
                    st.markdown(f"### 📌 Recommendation {idx}")
                    
                    # Create columns for type and priority
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.markdown(f"**Intervention Type:** {rec.get('intervention_type', 'N/A')}")
                    with col2:
                        priority = rec.get('priority', 'Medium')
                        priority_color = {'High': '🔴', 'Medium': '🟡', 'Low': '🟢'}.get(priority, '⚪')
                        st.markdown(f"**Priority:** {priority_color} {priority}")
                    
                    # Display details
                    if rec.get('action'):
                        st.markdown(f"**Action:**  \n{rec['action']}")
                    
                    if rec.get('timeline'):
                        st.markdown(f"**Timeline:** {rec['timeline']}")
                    
                    if rec.get('measurable_goal'):
                        st.markdown(f"**Measurable Goal:** {rec['measurable_goal']}")
                    elif rec.get('goal'):
                        st.markdown(f"**Goal:** {rec['goal']}")
                    
                    if rec.get('best_practices'):
                        st.markdown(f"**Best Practices:**  \n{rec['best_practices']}")
                    
                    # Add separator between recommendations
                    if idx < len(recommendations):
                        st.markdown("---")
            else:
                st.warning("⚠️ No structured recommendations available. Please regenerate the recommendations.")
        
        # Optional: Show raw JSON in a separate expander (outside the main expander to avoid nesting)
        # NOTE: This must be OUTSIDE the "View AI Recommendations" expander above
        if ai_recommendations.get('structured_recommendations') and len(ai_recommendations.get('structured_recommendations', [])) > 0:
            st.divider()  # Visual separator between expanders
            with st.expander("🔍 View as JSON", expanded=False):
                st.json(ai_recommendations['structured_recommendations'])
        
        # Clear button
        st.markdown("---")
        if st.button("🗑️ Clear AI Recommendations", type="secondary"):
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
    
    # Check if a specific recommendation was selected and pre-populate fields
    if 'selected_recommendation' in st.session_state:
        selected_rec = st.session_state.selected_recommendation
        rec_index = st.session_state.get('selected_recommendation_index', 0)
        
        st.success(f"📌 Using Recommendation #{rec_index} to create intervention")
        
        # Pre-populate AI recommendation fields
        st.session_state.ai_selected_intervention_type = selected_rec.get('intervention_type', '')
        st.session_state.ai_selected_priority = selected_rec.get('priority', 'Medium')
        
        # Format recommendation details for the intervention details field
        ai_details_text = f"🤖 AI-Generated Recommendation #{rec_index}\n\n"
        ai_details_text += f"Intervention Type: {selected_rec.get('intervention_type', 'N/A')}\n"
        ai_details_text += f"Priority Level: {selected_rec.get('priority', 'N/A')}\n\n"
        
        if selected_rec.get('action'):
            ai_details_text += f"Recommended Action:\n{selected_rec['action']}\n\n"
        
        if selected_rec.get('timeline'):
            ai_details_text += f"Timeline: {selected_rec['timeline']}\n\n"
        
        if selected_rec.get('measurable_goal'):
            ai_details_text += f"Measurable Goal: {selected_rec['measurable_goal']}\n\n"
        elif selected_rec.get('goal'):
            ai_details_text += f"Goal: {selected_rec['goal']}\n\n"
        
        if selected_rec.get('best_practices'):
            ai_details_text += f"Best Practices:\n{selected_rec['best_practices']}"
        
        st.session_state.ai_generated_details = ai_details_text
        
        # For Academic Meeting, generate meeting-specific details
        if selected_rec.get('intervention_type') == 'Academic Meeting':
            from datetime import datetime, timedelta
            
            # Parse timeline for date suggestion
            timeline = selected_rec.get('timeline', '')
            if 'within 1 week' in timeline.lower() or 'immediate' in timeline.lower():
                suggested_date = datetime.now().date() + timedelta(days=2)
            elif 'within 2 weeks' in timeline.lower():
                suggested_date = datetime.now().date() + timedelta(days=7)
            elif 'within 3 days' in timeline.lower():
                suggested_date = datetime.now().date() + timedelta(days=2)
            else:
                suggested_date = datetime.now().date() + timedelta(days=7)
            
            # Determine modality from recommendation if specified
            modality = selected_rec.get('modality', 'In-Person')
            
            st.session_state.ai_meeting_details = {
                'meeting_type': modality,
                'meeting_date': suggested_date,
                'meeting_time': datetime.strptime("10:00", "%H:%M").time(),
                'agenda': ai_details_text
            }
        
        # Clear the selected recommendation so it doesn't persist
        if st.button("🗑️ Clear Selected Recommendation"):
            del st.session_state.selected_recommendation
            del st.session_state.selected_recommendation_index
            del st.session_state.ai_generated_details
            if 'ai_meeting_details' in st.session_state:
                del st.session_state.ai_meeting_details
            st.rerun()
    
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
        
        # Additional notes section - pre-fill with selected or top recommendation if available
        default_additional_notes = ""
        
        # Check if a specific recommendation was selected, otherwise use the first one
        recommendation_to_use = None
        rec_label = ""
        
        if 'selected_recommendation' in st.session_state:
            # Use the specifically selected recommendation
            recommendation_to_use = st.session_state.selected_recommendation
            rec_label = f"Recommendation #{st.session_state.get('selected_recommendation_index', 1)}"
        elif ai_recommendations and ai_recommendations.get('structured_recommendations'):
            # Use the first recommendation as default
            recommendation_to_use = ai_recommendations['structured_recommendations'][0]
            rec_label = "Top Priority Recommendation"
        
        if recommendation_to_use:
            # Format the recommendation details for Additional Notes
            default_additional_notes = f"🤖 AI-Generated {rec_label}\n\n"
            default_additional_notes += f"Intervention Type: {recommendation_to_use.get('intervention_type', 'N/A')}\n"
            default_additional_notes += f"Priority: {recommendation_to_use.get('priority', 'N/A')}\n\n"
            
            if recommendation_to_use.get('action'):
                default_additional_notes += f"Recommended Action:\n{recommendation_to_use['action']}\n\n"
            
            if recommendation_to_use.get('timeline'):
                default_additional_notes += f"Timeline: {recommendation_to_use['timeline']}\n\n"
            
            if recommendation_to_use.get('measurable_goal'):
                default_additional_notes += f"Measurable Goal:\n{recommendation_to_use['measurable_goal']}\n\n"
            elif recommendation_to_use.get('goal'):
                default_additional_notes += f"Goal:\n{recommendation_to_use['goal']}\n\n"
            
            if recommendation_to_use.get('best_practices'):
                default_additional_notes += f"Best Practices:\n{recommendation_to_use['best_practices']}"
        
        additional_notes = st.text_area("Additional Notes", 
                                       value=default_additional_notes,
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
                    # Store the key for toggling details
                    detail_key = f"show_detail_{idx}"
                    if detail_key not in st.session_state:
                        st.session_state[detail_key] = False
                    
                    if st.button("View Details" if not st.session_state[detail_key] else "Hide Details", 
                                key=f"view_{idx}", use_container_width=True):
                        st.session_state[detail_key] = not st.session_state[detail_key]
                        st.rerun()
                    
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
                
                # Show details outside the columns if toggled
                if st.session_state.get(f"show_detail_{idx}", False):
                    st.text_area("Full Intervention Details", value=remediation['intervention_details'], 
                                height=200, disabled=True, key=f"details_text_{idx}")
        
    except Exception as e:
        st.error(f"Error loading scheduled remediations: {str(e)}")
        st.info("Please check your database connection and permissions.")

if __name__ == "__main__":
    main() 