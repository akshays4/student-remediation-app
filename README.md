# Student Risk Management System

A comprehensive Streamlit application for managing student academic risk assessment and intervention tracking. This system helps educational institutions identify at-risk students and manage remediation efforts effectively.

## 🎯 Features

- **Student Risk Dashboard**: Visual overview of students categorized by risk levels (High, Medium, Low, Excellent)
- **🤖 AI-Powered Recommendations**: LLM-generated intervention suggestions using Databricks model serving endpoints
- **Interactive Analytics**: Risk distribution charts and filtering capabilities
- **Intervention Management**: Create and track student interventions with priority levels
- **Personalized AI Details**: Generate detailed, customized intervention plans using AI
- **Scheduled Remediations**: Monitor pending interventions and mark them as completed
- **User Authorization**: Secure access using OAuth tokens and user-specific permissions
- **Real-time Data**: Direct PostgreSQL database integration with caching for performance

## 🏗️ Architecture

The application consists of:
- **Frontend**: Streamlit web interface with interactive dashboards
- **Backend**: PostgreSQL database for data storage
- **AI Layer**: Databricks model serving endpoints for LLM-powered recommendations
- **Authentication**: OAuth-based user authorization
- **Data Processing**: Pandas for data manipulation and Plotly for visualizations

## 📋 Prerequisites

- Python 3.8 or higher
- PostgreSQL database with student risk data
- Databricks workspace with model serving endpoint
- User OAuth credentials configured
- Databricks model serving endpoint for LLM inference

## 🚀 Local Development Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd student-remediation-app
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the project root with the following variables:

```env
# Database Configuration
DATABASE_REMEDIATION_DATA=your_database_name
PGHOST=your_postgres_host
PGPORT=5432
PGDATABASE=your_database_name
PGSSLMODE=require
PGAPPNAME=student-remediation-app

# Databricks LLM Configuration
SERVING_ENDPOINT=your_model_serving_endpoint_name
DATABRICKS_HOST=your_databricks_workspace_url
DATABRICKS_TOKEN=your_databricks_access_token

# Optional: For local development
PGUSER=your_username
PGPASSWORD=your_password
```

### 4. Database Setup

Ensure your PostgreSQL database contains the required table:

```sql
-- Main student risk analysis table
CREATE TABLE IF NOT EXISTS public.student_risk_analysis_gold (
    student_id VARCHAR(255) PRIMARY KEY,
    full_name VARCHAR(255),
    major VARCHAR(255),
    year_level VARCHAR(50),
    gpa DECIMAL(3,2),
    courses_enrolled INTEGER,
    failing_grades INTEGER,
    risk_category VARCHAR(50),
    activity_status VARCHAR(50)
);

-- Interventions table (created automatically by the app)
CREATE TABLE IF NOT EXISTS public.student_interventions (
    student_id VARCHAR(255),
    intervention_type VARCHAR(255),
    intervention_details TEXT,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) DEFAULT 'Pending',
    created_by VARCHAR(255),
    PRIMARY KEY (student_id, created_date)
);
```

### 5. Run the Application

```bash
streamlit run app.py
```

The application will be available at `http://localhost:8501`

## 🤖 AI-Powered Features

### LLM-Generated Intervention Recommendations

The application now includes intelligent intervention recommendations powered by Databricks model serving endpoints:

#### Features:
- **Smart Recommendations**: Click the "🤖 AI Rec" button next to any student to get personalized intervention suggestions
- **Contextual Analysis**: AI considers student's GPA, major, year level, risk category, and academic performance
- **Multiple Options**: Provides 3 prioritized intervention recommendations with specific action items
- **Personalized Details**: Generate detailed intervention plans using the "🤖 AI-Enhanced Details" button
- **Fallback System**: Rule-based recommendations when LLM is unavailable

#### How It Works:
1. **Student Context**: AI analyzes comprehensive student profile data
2. **Expert Prompting**: Uses academic advisor expertise prompts for relevant recommendations
3. **Structured Output**: Provides actionable recommendations with priorities and timelines
4. **Integration**: Seamlessly integrates with existing intervention workflow

#### Configuration:
```env
SERVING_ENDPOINT=your_model_endpoint_name
DATABRICKS_HOST=your_workspace.cloud.databricks.com
DATABRICKS_TOKEN=dapi-your-access-token
```

## 🚀 Databricks Deployment

### Step 1: Prepare Your Databricks Environment

1. **Create a Databricks Workspace** (if not already available)
2. **Set up a Databricks cluster** with Python runtime
3. **Configure your Lakebase instance** for data storage

### Step 2: Add Lakebase Instance to App Resources

1. **Navigate to your Databricks workspace**
2. **Go to Data > Databases** and ensure your database is accessible
3. **Configure connection parameters** in your Databricks environment

### Step 3: Upload Application Files

```bash
# Using Databricks CLI (install if needed: pip install databricks-cli)
databricks configure --token

# Upload application files
databricks fs cp app.py dbfs:/FileStore/apps/student-remediation/
databricks fs cp requirements.txt dbfs:/FileStore/apps/student-remediation/
databricks fs cp app.yaml dbfs:/FileStore/apps/student-remediation/
```

### Step 4: Create Databricks App Resource

1. **Create a new App in Databricks Apps**:
   ```yaml
   # app.yaml configuration for Databricks
   name: student-remediation-app
   command: ["streamlit", "run", "app.py"]
   resources:
     - name: lakebase-instance
       type: lakebase
       config:
         database_name: ${DATABASE_REMEDIATION_DATA}
         host: ${PGHOST}
         port: ${PGPORT}
         ssl_mode: require
   ```

2. **Configure Environment Variables** in Databricks Apps:
   ```
   DATABASE_REMEDIATION_DATA=your_database_name
   PGHOST=your_lakebase_host
   PGPORT=5432
   PGSSLMODE=require
   PGAPPNAME=student-remediation-app
   SERVING_ENDPOINT=your_model_serving_endpoint
   DATABRICKS_HOST=your_workspace.cloud.databricks.com
   DATABRICKS_TOKEN=your_databricks_token
   ```

### Step 5: Deploy the Application

1. **Using Databricks Apps UI**:
   - Navigate to "Apps" in your Databricks workspace
   - Click "Create App"
   - Upload your application files
   - Configure the app.yaml settings
   - Add the Lakebase instance as a resource
   - Deploy the application

2. **Using Databricks CLI**:
   ```bash
   # Deploy the app
   databricks apps create --source-code-path /path/to/your/app
   
   # Or update existing app
   databricks apps update --app-id your-app-id --source-code-path /path/to/your/app
   ```

### Step 6: Configure User Authorization

1. **Set up OAuth in Databricks**:
   - Configure OAuth providers in your workspace
   - Set up user permissions for database access
   - Ensure users have appropriate scopes for data access

2. **Configure App Permissions**:
   - Grant users access to the deployed app
   - Set up proper database permissions
   - Test user authorization flow

### Step 7: Access Your Deployed App

Once deployed, your app will be available at:
```
https://<databricks-workspace-url>/apps/<app-id>
```

## 📊 Database Schema

### Required Tables

#### student_risk_analysis_gold
- `student_id` (VARCHAR): Unique student identifier
- `full_name` (VARCHAR): Student's full name
- `major` (VARCHAR): Academic major
- `year_level` (VARCHAR): Academic year (Freshman, Sophomore, etc.)
- `gpa` (DECIMAL): Current GPA
- `courses_enrolled` (INTEGER): Number of enrolled courses
- `failing_grades` (INTEGER): Number of failing grades
- `risk_category` (VARCHAR): Risk level (High Risk, Medium Risk, Low Risk, Excellent)
- `activity_status` (VARCHAR): Student activity status

#### student_interventions (Auto-created)
- `student_id` (VARCHAR): Student identifier
- `intervention_type` (VARCHAR): Type of intervention
- `intervention_details` (TEXT): Detailed intervention information
- `created_date` (TIMESTAMP): Creation timestamp
- `status` (VARCHAR): Intervention status (Pending, Completed)
- `created_by` (VARCHAR): User who created the intervention

## 🔧 Configuration Options

### Risk Categories
- **High Risk**: Students requiring immediate attention
- **Medium Risk**: Students needing monitoring
- **Low Risk**: Students with minor concerns
- **Excellent**: High-performing students

### Intervention Types
- Academic Meeting
- Study Plan Assignment
- Tutoring Referral
- Counseling Referral
- Financial Aid Consultation
- Career Guidance Session
- Peer Mentoring Program
- Academic Probation Review

## 🛠️ Troubleshooting

### Common Issues

1. **Database Connection Errors**:
   - Verify environment variables are set correctly
   - Check database connectivity and permissions
   - Ensure SSL configuration matches your database setup

2. **Authentication Issues**:
   - Verify OAuth configuration in Databricks
   - Check user permissions for database access
   - Ensure proper scopes are configured

3. **Missing Data**:
   - Verify the `student_risk_analysis_gold` table exists
   - Check data permissions for the authenticated user
   - Use Debug Mode in the sidebar to troubleshoot

### Debug Mode

The application includes a built-in debug mode accessible from the sidebar:
- Test database connections
- View available tables
- Check user authentication status
- Verify environment configuration

## 📈 Performance Considerations

- **Caching**: Data is cached for 5 minutes to improve performance
- **Connection Management**: Direct connections are used to avoid timeout issues
- **Query Optimization**: Queries are optimized for the expected data volume

## 🔒 Security

- **User Authorization**: OAuth-based authentication with token validation
- **Database Security**: SSL connections and user-specific permissions
- **Data Privacy**: User-specific data access based on authorization tokens

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📞 Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the troubleshooting section above

## 🔄 Version History

- **v1.0.0**: Initial release with core functionality
  - Student risk dashboard
  - Intervention management
  - Databricks deployment support
