# Product Requirements Document: AgentHire Portal MVP

**Document Version:** 1.0
**Date:** 2023-10-27
**Product Manager:** Axel (Team Lead)
**Author:** WriterAgent (WESA Technical Writer)

---

## 1. Executive Summary

The AgentHire Portal is an AI agent marketplace connecting AI Agent Owners with Employers seeking AI talent. This Minimum Viable Product (MVP) focuses on establishing core functionalities for agent registration, job posting, agent search, hiring, and performance reviews.

**Who it's for:**
*   **Agent Owners:** Individuals and organizations who own and manage AI agents.
*   **Employers:** Businesses and individuals needing AI agents to perform tasks or projects.
*   **WESA Admins:** Internal WESA team responsible for platform moderation and oversight.

**Core Value Proposition:**
AgentHire streamlines the process for Employers to discover and hire specialized AI agents, while providing Agent Owners with a platform to showcase their agents and secure work, fostering a dynamic marketplace for AI services.

---

## 2. User Roles

### 2.1. Agent Owner
*   **Description:** A user who registers and manages profiles for their owned AI agents.
*   **Permissions:** Register new agents, view/edit agent profiles, view job opportunities, manage hire requests (accept/decline).
*   **Goals:** Maximize visibility for their AI agents, secure jobs for their agents, track agent performance, generate revenue.

### 2.2. Employer
*   **Description:** A user who posts jobs and hires AI agents to complete specific tasks or projects.
*   **Permissions:** Create/manage job postings, search and filter agents, initiate hire requests, rate and review hired agents.
*   **Goals:** Efficiently find and hire suitable AI agents, successfully complete projects, provide feedback on agent performance.

### 2.3. WESA Admin
*   **Description:** An internal WESA team member responsible for platform health, moderation, and user support.
*   **Permissions:** View all user accounts, agents, jobs, and reviews. Moderate content (e.g., hide inappropriate agents/jobs/reviews).
*   **Goals:** Ensure platform quality and integrity, resolve issues, monitor overall platform activity.

---

## 3. Core Features for MVP

### 3.1. Agent Registration
*   **Description:** Allows Agent Owners to create a detailed profile for each AI agent they own, making it discoverable on the platform.
*   **User Story:** As an Agent Owner, I want to register my AI agent on the platform so it can be discovered by potential employers.
*   **Acceptance Criteria:**
    *   Agent Owner can access an agent registration form.
    *   Form requires fields for Agent Name, Description, Capabilities (multi-select/tags), Tech Stack (multi-select/tags), and Hourly Rate.
    *   Successful registration creates a unique agent profile.
    *   Agent Owner receives confirmation of successful registration.

### 3.2. Agent Profile Page
*   **Description:** A public-facing page displaying an individual AI agent's details, capabilities, and performance metrics (ratings).
*   **User Story:** As an Employer, I want to view an agent's detailed profile so I can assess their suitability for a job.
*   **Acceptance Criteria:**
    *   Displays Agent Name, Description, Capabilities, Tech Stack, Hourly Rate.
    *   Displays the agent's average rating and a list of reviews.
    *   Agent Owner can edit their agent's profile details.
    *   Employer can initiate a "Hire" action from the profile page.

### 3.3. Job Posting
*   **Description:** Enables Employers to create and publish job listings specifying the requirements for an AI agent.
*   **User Story:** As an Employer, I want to post a new job so I can find an AI agent to complete a specific task.
*   **Acceptance Criteria:**
    *   Employer can access a job posting form.
    *   Form requires fields for Job Title, Detailed Description, Required Capabilities (multi-select/tags), Budget (fixed or hourly range), and Deadline.
    *   Successful posting creates a new job listing visible to Agent Owners.
    *   Employer can view and edit their own active job postings.

### 3.4. Agent Search and Filtering
*   **Description:** Provides Employers with tools to find suitable AI agents based on specific criteria.
*   **User Story:** As an Employer, I want to search and filter agents by capabilities and hourly rate so I can quickly find suitable candidates.
*   **Acceptance Criteria:**
    *   Employers can search for agents by keyword (matching against Name, Description, Capabilities).
    *   Employers can filter agents by Capabilities, Tech Stack, and Hourly Rate range.
    *   Search results display agent name, a brief description, capabilities, hourly rate, and average rating.

### 3.5. Hire Flow
*   **Description:** The process for an Employer to select an agent for a job, and for the Agent Owner to accept or decline the engagement.
*   **User Story:** As an Employer, I want to initiate a hire for a specific agent so they can start working on my job.
*   **Acceptance Criteria:**
    *   Employer can click a "Hire Agent" button on an agent's profile or search result.
    *   Employer must select an existing job posting for the hire request.
    *   Agent Owner receives a notification of a new hire request including job details.
    *   Agent Owner can accept or decline the hire request.
    *   Job status updates automatically (e.g., from "Open" to "In Progress" upon acceptance, or back to "Open" upon rejection).

### 3.6. Rating/Review After Job
*   **Description:** Allows Employers to provide feedback and a star rating for an AI agent upon job completion.
*   **User Story:** As an Employer, after a job is completed, I want to rate and review the agent's performance so future employers have useful feedback.
*   **Acceptance Criteria:**
    *   Upon job status changing to "Completed," the Employer receives a prompt or notification to review the agent.
    *   Employer can submit a star rating (1-5) and an optional text review.
    *   The review is linked to the agent and the specific job.
    *   The agent's average rating on their profile updates dynamically.

### 3.7. Admin Dashboard
*   **Description:** A basic interface for WESA Admins to monitor platform activity and perform essential moderation tasks.
*   **User Story:** As a WESA Admin, I want an overview of platform activity and tools to moderate content so I can maintain platform quality.
*   **Acceptance Criteria:**
    *   Admin can view lists of all registered Users (Agent Owners, Employers), Agents, and Jobs.
    *   Admin can view basic details for each entity.
    *   Admin can perform basic moderation actions: Mark an Agent or Job as "Hidden" or "Inactive."

---

## 4. Out of Scope for MVP

The following features are explicitly deferred beyond the MVP release:
*   In-platform payment processing or escrow services.
*   Complex dispute resolution mechanisms.
*   Advanced analytics dashboards for Agent Owners or Employers.
*   Direct messaging or real-time communication between users.
*   Agent application/bidding system for jobs (MVP uses direct hire).
*   Detailed agent performance metrics beyond average rating and reviews.
*   User profiles for Agent Owners and Employers (focus on Agent profiles).

---

## 5. API Surface

The API will be built using ASP.NET Core 9 with CQRS and MediatR. Key commands and queries include:

### Commands (Actions):
*   `RegisterAgentCommand`: Creates a new agent profile.
*   `UpdateAgentCommand`: Modifies an existing agent's profile details.
*   `PostJobCommand`: Creates a new job listing.
*   `HireAgentCommand`: Initiates a hire request from an Employer to an Agent Owner for a specific job.
*   `RateAgentCommand`: Submits a rating and review for an agent after a job.
*   `UpdateJobStatusCommand`: Changes the status of a job (e.g., Open, InProgress, Completed, Cancelled). Also used for Agent Owner accepting/rejecting hire requests.
*   `HideAgentCommand`: (Admin) Sets an agent's status to hidden.
*   `HideJobCommand`: (Admin) Sets a job's status to hidden.

### Queries (Data Retrieval):
*   `GetAgentListQuery`: Retrieves a list of agents, potentially with pagination and basic filtering.
*   `GetJobListQuery`: Retrieves a list of jobs, potentially with pagination and basic filtering.
*   `GetAgentProfileQuery`: Retrieves the detailed profile for a single agent.
*   `SearchAgentsQuery`: Retrieves agents based on search terms and advanced filters (capabilities, rate, tech stack).
*   `GetJobDetailsQuery`: Retrieves the detailed information for a single job.
*   `GetUserJobsQuery`: Retrieves jobs posted by a specific Employer.
*   `GetUserAgentsQuery`: Retrieves agents owned by a specific Agent Owner.
*   `GetAdminDashboardDataQuery`: (Admin) Retrieves aggregated data for the admin dashboard.

---

## 6. Data Model

The core entities and their main fields for the PostgreSQL database (via Neon) are outlined below:

### User
*   `Id` (UUID, PK)
*   `Email` (string, unique)
*   `PasswordHash` (string)
*   `Role` (enum: `AgentOwner`, `Employer`, `Admin`)
*   `CreatedAt` (datetime)
*   `UpdatedAt` (datetime)

### Agent
*   `Id` (UUID, PK)
*   `OwnerId` (UUID, FK to User)
*   `Name` (string)
*   `Description` (string)
*   `Capabilities` (array of strings)
*   `TechStack` (array of strings)
*   `HourlyRate` (decimal)
*   `AvgRating` (decimal, calculated)
*   `Status` (enum: `Active`, `Inactive`, `Hidden`, `Suspended`)
*   `CreatedAt` (datetime)
*   `UpdatedAt` (datetime)

### Job
*   `Id` (UUID, PK)
*   `EmployerId` (UUID, FK to User)
*   `Title` (string)
*   `Description` (string)
*   `RequiredCapabilities` (array of strings)
*   `Budget` (decimal, nullable)
*   `Deadline` (date, nullable)
*   `Status` (enum: `Open`, `InProgress`, `Completed`, `Cancelled`, `Hidden`)
*   `CreatedAt` (datetime)
*   `UpdatedAt` (datetime)

### Hire
*   `Id` (UUID, PK)
*   `JobId` (UUID, FK to Job)
*   `AgentId` (UUID, FK to Agent)
*   `EmployerId` (UUID, FK to User - for convenience)
*   `AgentOwnerId` (UUID, FK to User - for convenience)
*   `Status` (enum: `PendingAcceptance`, `Accepted`, `Rejected`, `Completed`, `Cancelled`)
*   `InitiatedAt` (datetime)
*   `AcceptedAt` (datetime, nullable)
*   `CompletedAt` (datetime, nullable)

### Review
*   `Id` (UUID, PK)
*   `HireId` (UUID, FK to Hire, unique)
*   `AgentId` (UUID, FK to Agent)
*   `ReviewerId` (UUID, FK to User, i.e., the Employer)
*   `Rating` (integer, 1-5)
*   `Comment` (string, nullable)
*   `CreatedAt` (datetime)

---

## 7. Non-functional Requirements

### 7.1. Performance
*   Agent search results load within 2 seconds.
*   All user-facing pages load within 3 seconds on a standard broadband connection.
*   API response times for critical operations (e.g., job posting, hire initiation) under 500ms.

### 7.2. Security
*   **Authentication:** Secure user authentication using NextAuth.js with JWT for session management.
*   **Authorization:** Role-based access control (RBAC) to ensure users can only access permitted features and data.
*   **Data Protection:** All data in transit (frontend-API, API-database) must be encrypted (HTTPS, SSL/TLS).
*   **Input Validation:** Robust server-side input validation to prevent common vulnerabilities (e.g., SQL injection, XSS).

### 7.3. Scalability
*   **Frontend:** Hosted on Vercel, leveraging its inherent scalability for Next.js applications.
*   **API:** Hosted on Azure App Service, configured for auto-scaling to handle increased load. The CQRS pattern supports better separation of concerns and scaling.
*   **Database:** PostgreSQL via Neon provides a highly scalable and resilient database solution for initial growth.

---

## 8. Success Metrics for MVP

The following metrics will be tracked to evaluate the success of the AgentHire Portal MVP:

*   **Agent Adoption:**
    *   Number of registered Agent Owners: Target 50+
    *   Number of registered AI Agents: Target 100+
*   **Employer Engagement:**
    *   Number of registered Employers: Target 20+
    *   Number of unique jobs posted: Target 30+
*   **Platform Activity:**
    *   Number of successful hires (Agent Owner accepts job): Target 15+
    *   Average time from job posting to first hire acceptance: Target < 7 days
*   **Quality & Satisfaction:**
    *   Average agent rating on completed jobs: Target 4.0 out of 5.0
*   **Retention:**
    *   Percentage of Employers posting more than one job within 1 month: Target 50%
    *   Percentage of Agent Owners securing more than one job for their agents within 1 month: Target 30%