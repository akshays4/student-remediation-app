import streamlit as st
import pandas as pd
import os
# import psycopg2
from sqlalchemy import create_engine, event, text
from dotenv import load_dotenv
import plotly.express as px
# import plotly.graph_objects as go
from databricks.sdk import WorkspaceClient
import uuid
# import time
import os


DATABASE_SYNCED_DATA = os.getenv("DATABASE_SYNCED_DATA")
DATABASE_REMEDIATION_DATA = os.getenv("DATABASE_REMEDIATION_DATA")
SECRET_PAT = os.getenv("SECRET_PAT")

# Load environment variables
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="Student Risk Management System",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Database connection configuration
@st.cache_resource
def init_connection(dbname="akshay_university_sample"):
    """Initialize PostgreSQL connection with OAuth token"""
    try:
        # Database connection parameters from LakeBase
        username = "akshay.s@databricks.com"
        host = "instance-28bcf5bd-6c2c-4209-a642-71ea5d78dda5.database.cloud.databricks.com"
        port = 5432
        database = dbname
        
        # Get OAuth token from environment or use Databricks SDK
        if not SECRET_PAT:
            oauth_token = os.getenv("DATABRICKS_OAUTH_TOKEN")
        else:
            oauth_token = SECRET_PAT
        
        if not oauth_token:
            # Try to use Databricks SDK to generate token
            try:
                w = WorkspaceClient()
                instance_name = "instance-28bcf5bd-6c2c-4209-a642-71ea5d78dda5"
                cred = w.database.generate_database_credential(
                    request_id=str(uuid.uuid4()), 
                    instance_names=[instance_name]
                )
                oauth_token = cred.token
            except Exception as sdk_error:
                st.error(f"Failed to generate OAuth token via SDK: {str(sdk_error)}")
                st.error("Please set DATABRICKS_OAUTH_TOKEN environment variable")
                st.stop()
        
        # Create connection string with OAuth token
        connection_string = f"postgresql://{username}:{oauth_token}@{host}:{port}/{database}?sslmode=require"
        engine = create_engine(connection_string)
        
        # Test the connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        return engine
        
    except Exception as e:
        st.error(f"Failed to initialize database connection: {str(e)}")
        st.error("Please check your database credentials and network connectivity")
        st.stop()

@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_student_risk_data():
    """Load student risk data from database"""
    engine = init_connection(dbname="akshay_university_sample")
    
    query = """
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
    FROM student_risk_analysis_gold
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
        df = pd.read_sql_query(query, engine)
        return df
    except Exception as e:
        st.error(f"Error loading student data: {str(e)}")
        return pd.DataFrame()

def create_intervention_table():
    """Create interventions table if it doesn't exist"""
    engine = init_connection(dbname="akshay_student_remediation")
    
    create_table_query = """
    CREATE TABLE IF NOT EXISTS student_interventions (
        student_id VARCHAR(255),
        intervention_type VARCHAR(255),
        intervention_details TEXT,
        created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status VARCHAR(50) DEFAULT 'Pending',
        created_by VARCHAR(255),
        PRIMARY KEY (student_id, created_date)
    )
    """
    
    try:
        with engine.connect() as conn:
            conn.execute(text(create_table_query))
            conn.commit()
    except Exception as e:
        st.error(f"Error creating interventions table: {str(e)}")

def submit_intervention(student_id, intervention_type, details, created_by):
    """Submit intervention to database"""
    engine = init_connection(dbname="akshay_student_remediation")
    
    insert_query = """
    INSERT INTO student_interventions
    (student_id, intervention_type, intervention_details, created_by)
    VALUES (:student_id, :intervention_type, :details, :created_by)
    """
    
    try:
        with engine.connect() as conn:
            conn.execute(text(insert_query), {
                'student_id': student_id,
                'intervention_type': intervention_type,
                'details': details,
                'created_by': created_by
            })
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

def main():
    # Initialize intervention table
    create_intervention_table()
    
    # Header
    st.title("🎓 Student Risk Management System")
    st.markdown("---")
    
    # Sidebar
    st.sidebar.title("Navigation")
    
    # Initialize page in session state if not exists
    if 'page' not in st.session_state:
        st.session_state.page = "Student Risk Dashboard"
    
    # Page selection with session state
    page = st.sidebar.selectbox(
        "Choose a page", 
        ["Student Risk Dashboard", "Create Intervention"],
        index=0 if st.session_state.page == "Student Risk Dashboard" else 1,
        key="page_selector"
    )
    
    # Update session state when page changes
    if page != st.session_state.page:
        st.session_state.page = page
    
    if page == "Student Risk Dashboard":
        st.header("📊 Students at Risk Overview")
        
        try:
            # Load data
            with st.spinner("Loading student data..."):
                df = load_student_risk_data()
            
            if df.empty:
                st.warning("No student data found.")
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
                            'High Risk': '#FF4B4B',
                            'Medium Risk': '#FFA500',
                            'Low Risk': '#00CC88',
                            'Excellent': '#28A745'
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
                    col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
                    
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
                        if st.button(f"Create Intervention", key=f"btn_{student['student_id']}"):
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
    
    elif page == "Create Intervention":
        st.header("📝 Create Student Intervention")
        
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
                           'selected_student_year', 'selected_student_gpa', 'selected_student_risk']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
        else:
            default_student_id = ""
            
        with st.form("intervention_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                student_id = st.text_input("Student ID", value=default_student_id)
                intervention_type = st.selectbox(
                    "Intervention Type",
                    [
                        "Academic Meeting",
                        "Study Plan Assignment", 
                        "Tutoring Referral",
                        "Counseling Referral",
                        "Financial Aid Consultation",
                        "Career Guidance Session",
                        "Peer Mentoring Program",
                        "Academic Probation Review"
                    ]
                )
            
            with col2:
                created_by = st.text_input("Created By (Your Name)")
                priority = st.selectbox("Priority", ["High", "Medium", "Low"])
            
            # Intervention details based on type
            st.subheader("Intervention Details")
            
            if intervention_type == "Academic Meeting":
                meeting_type = st.selectbox("Meeting Type", ["In-Person", "Virtual", "Phone"])
                meeting_date = st.date_input("Proposed Meeting Date")
                meeting_time = st.time_input("Proposed Meeting Time")
                agenda = st.text_area("Meeting Agenda", placeholder="Discuss academic performance, identify challenges, create action plan...")
                details = f"Meeting Type: {meeting_type}, Date: {meeting_date}, Time: {meeting_time}, Agenda: {agenda}"
                
            elif intervention_type == "Study Plan Assignment":
                study_duration = st.selectbox("Study Plan Duration", ["2 weeks", "1 month", "1 semester"])
                focus_areas = st.multiselect("Focus Areas", ["Time Management", "Note Taking", "Test Preparation", "Research Skills", "Writing Skills"])
                goals = st.text_area("Specific Goals", placeholder="Improve GPA to 2.5, complete all assignments on time...")
                details = f"Duration: {study_duration}, Focus Areas: {', '.join(focus_areas)}, Goals: {goals}"
                
            elif intervention_type == "Tutoring Referral":
                subjects = st.text_input("Subjects Needing Tutoring")
                tutoring_type = st.selectbox("Tutoring Type", ["Individual", "Group", "Online"])
                frequency = st.selectbox("Frequency", ["Once a week", "Twice a week", "Three times a week"])
                details = f"Subjects: {subjects}, Type: {tutoring_type}, Frequency: {frequency}"
                
            elif intervention_type == "Counseling Referral":
                counseling_type = st.selectbox("Counseling Type", ["Academic", "Personal", "Career", "Mental Health"])
                urgency = st.selectbox("Urgency", ["Immediate", "Within a week", "Within a month"])
                reason = st.text_area("Reason for Referral")
                details = f"Type: {counseling_type}, Urgency: {urgency}, Reason: {reason}"
                
            else:
                details = st.text_area("Intervention Details", placeholder="Provide specific details about the intervention...")
            
            # Additional notes
            additional_notes = st.text_area("Additional Notes", placeholder="Any additional information or special considerations...")
            
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
                                   'selected_student_year', 'selected_student_gpa', 'selected_student_risk']:
                            if key in st.session_state:
                                del st.session_state[key]
                            
                    except Exception as e:
                        st.error(f"Error submitting intervention: {str(e)}")
                else:
                    st.error("Please fill in all required fields.")

if __name__ == "__main__":
    main()
