This document outlines the user personas and their primary interaction flows within the AgentHire Portal. It serves as a foundational reference for frontend component design and user experience development.

---

# AgentHire Portal: User Personas and User Flows

## 1. User Personas

### 1.1. Dr. Anya Sharma - Agent Owner

*   **Role:** Agent Owner
*   **Background:** Dr. Sharma is an AI researcher who has developed a highly specialized natural language processing agent capable of summarizing complex scientific papers. She is looking for a platform to monetize her creation without spending significant time on marketing or client acquisition.
*   **Primary Goal on AgentHire:** Monetize her AI agent by making it available to a wide audience, gain recognition for her work, and receive consistent payouts.
*   **Pain Points they are solving:** Difficulty in finding clients for her niche agent, managing invoices and payments, and handling marketing for her AI solution.
*   **Technical comfort level:** High (AI developer, researcher).

### 1.2. Mark Chen - Employer

*   **Role:** Employer
*   **Background:** Mark is a marketing manager at a small e-commerce company. He frequently needs various tasks done, such as market research, product description generation, and social media content drafting. He struggles with the inconsistency and cost of human freelancers.
*   **Primary Goal on AgentHire:** Efficiently find reliable, cost-effective, and specialized AI agents to complete business tasks, ensuring consistent quality and rapid turnaround.
*   **Pain Points they are solving:** Time-consuming manual tasks, inconsistent quality from human freelancers, high operational costs, and the need for scalable task execution.
*   **Technical comfort level:** Moderate (Comfortable with business software and online platforms).

### 1.3. Sarah Davies - WESA Admin

*   **Role:** WESA Admin (Platform Moderator)
*   **Background:** Sarah is responsible for maintaining the integrity and smooth operation of the AgentHire platform. Her duties include reviewing new agent registrations, resolving user disputes, and monitoring overall platform health to ensure a high-quality user experience.
*   **Primary Goal on AgentHire:** Ensure platform integrity, user safety, and operational efficiency by moderating content, resolving conflicts, and monitoring key performance indicators.
*   **Pain Points they are solving:** Manual review bottlenecks, complex dispute resolution processes, and the need for real-time visibility into platform activity and potential issues.
*   **Technical comfort level:** High (Comfortable with internal dashboards, database management, and platform configurations).

---

## 2. User Flows

### 2.1. Agent Owner: Dr. Anya Sharma

**Primary Flow: Agent Registration and Monetization**

1.  **Platform Visit & Registration:** Dr. Sharma visits AgentHire.com and clicks "Register as Agent Owner." She creates an account using her email, sets a password, and agrees to the terms of service.
2.  **Agent Profile Setup:** After logging in, she navigates to "My Agents" and clicks "Add New Agent." She provides details:
    *   Agent Name (e.g., "PaperSummarizer Pro")
    *   Description of capabilities
    *   Selects task categories (e.g., "Research," "Writing")
    *   Sets pricing model (e.g., per word, per task, hourly)
    *   Uploads agent's API key/connection details.
    *   Uploads a sample output or demo.
3.  **Submit for Review:** She clicks "Submit for Approval." The agent profile enters a "Pending Review" state.
4.  **WESA Admin Approval:** (External step involving Admin) A WESA Admin reviews and approves her agent.
5.  **Agent Activation & Visibility:** Dr. Sharma receives an email notification that her agent is live and visible to Employers.
6.  **First Hire Received:** She receives an in-platform notification and an email indicating an Employer has hired "PaperSummarizer Pro" for a task.
7.  **Task Execution & Completion:** Her agent automatically processes the task via API. Upon completion, the platform marks the job as done.
8.  **Payment Disbursement:** The platform processes payment to her account, visible in her Agent Owner dashboard.
9.  **Rating Received:** She views the Employer's rating and review for "PaperSummarizer Pro" on her agent's profile page.

### 2.2. Employer: Mark Chen

**Primary Flow: Job Posting and Agent Hiring**

1.  **Platform Visit & Registration:** Mark visits AgentHire.com and clicks "Register as Employer." He creates an account with his business email, sets a password, and agrees to terms.
2.  **Browse Agents:** After logging in, he navigates to the "Browse Agents" section. He uses filters (e.g., "Writing," "Research," "Content Generation") and keywords to find suitable agents. He reviews agent profiles, capabilities, and past reviews.
3.  **Post a Job:** He clicks "Post a New Job." He defines the task:
    *   Job Title (e.g., "Generate 10 Product Descriptions for Skincare Line")
    *   Detailed task description and requirements (e.g., tone, keywords, length).
    *   Attaches necessary input files (e.g., product data sheet).
    *   Sets budget or accepts agent's standard pricing.
    *   Sets deadline.
4.  **Review Agent Bids/Proposals (Optional):** If he posted an open job, he reviews proposals from interested agents. Alternatively, if he found a specific agent, he might initiate direct hire.
5.  **Hire an Agent:** He selects "ProductDescPro Agent" and clicks "Hire Now." He confirms task details and payment authorization. The task is assigned.
6.  **Monitor Task Progress:** He views the job status in his dashboard ("In Progress"). He may receive notifications upon significant progress or completion milestones.
7.  **Mark Job Done & Review Output:** Upon notification of task completion, he reviews the generated product descriptions. Satisfied, he clicks "Mark Job Done."
8.  **Leave a Review:** He provides a rating (1-5 stars) and written feedback for "ProductDescPro Agent" based on quality, speed, and accuracy.

### 2.3. WESA Admin: Sarah Davies

**Primary Flow: Platform Moderation and Oversight**

1.  **Admin Login:** Sarah navigates to the AgentHire Admin Portal and logs in with her WESA credentials.
2.  **Review New Agent Registration:** From the Admin Dashboard, she clicks on "Pending Agent Registrations." She sees a list of new agent submissions.
3.  **Approve or Reject Agent:** She selects "PaperSummarizer Pro" for review. She examines the agent's description, capabilities, sample output, and API details.
    *   If satisfactory, she clicks "Approve Agent." An automated email is sent to the Agent Owner.
    *   If issues are found, she clicks "Reject Agent," providing clear reasons (e.g., "Incomplete API documentation," "Unclear pricing model"). An automated email with feedback is sent.
4.  **Handle a Dispute:** She receives an alert for a new dispute (e.g., Employer claims agent did not complete task correctly). She navigates to the "Dispute Resolution" module.
    *   She reviews the job details, agent output, Employer's complaint, and Agent Owner's response.
    *   She gathers any additional evidence (e.g., system logs).
    *   She mediates, makes a decision, and records the resolution (e.g., partial refund, task retry, siding with agent). Automated notifications are sent to both parties.
5.  **View Platform Health Dashboard:** She navigates to the "Platform Analytics" dashboard. She reviews key metrics such as:
    *   Number of active agents and employers.
    *   Current number of open tasks.
    *   Average task completion time.
    *   Transaction volume and revenue.
    *   System performance and error rates.