<div align="center">

# NexoBot

**Your Tier 1 forensic investigator specializing in code/log security vulnerability reviews and remediation.**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18+-61DAFB?logo=react&logoColor=black)](https://reactjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-Python-000000?logo=python&logoColor=white)](https://github.com/abetlen/llama-cpp-python)
[![Docker](https://img.shields.io/badge/Docker-24+-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

</div>

---

## 🧭 Overview

**NexoBot** is a modern, privacy-first conversational AI platform transformed into a specialized Tier 1 forensic investigator. By leveraging powerful local large language models executed entirely on your own hardware, it guarantees that your chats, prompts, and sensitive security logs never leave your machine.

NexoBot is uniquely designed for cybersecurity analysis, developer crash/error diagnosis, and cyber-defense. Beyond just chatting, it includes **agentic tool calling** (such as automated forensic report writing) and a robust observability infrastructure utilizing **Docker, Prometheus, and Grafana**. The architecture supports full session management, real-time message streaming via Server-Sent Events (SSE), and secure JWT authentication. 

---

## 📁 Project Structure

```text
NexoBot/
│
├── backend/                           # FastAPI application and Python backend
│   ├── alembic/                       # Alembic database migration framework
│   │   ├── versions/                  # Auto-generated migration revision files
│   │   │   ├── 001_initial_schema.py
│   │   │   ├── 002_add_user_password_hash.py
│   │   │   └── 003_add_tool_role_to_messages.py
│   │   │
│   │   ├── env.py                     # Alembic runtime environment config
│   │   └── script.py.mako             # Template for generating new migration files
│   │
│   ├── core/                          # Application core layer
│   │   ├── config.py                  # Pydantic Settings — loads all .env variables & system prompt
│   │   ├── database.py                # SQLAlchemy engine, session creation, and async config
│   │   ├── dependencies.py            # FastAPI Depends guards: get_current_user, get_db
│   │   ├── metrics.py                 # Prometheus metrics instrumentation
│   │   ├── security.py                # bcrypt password hashing and JWT token creation/verification
│   │   └── utils.py                   # Shared utility functions and helpers
│   │
│   ├── llm/                           # Artificial Intelligence integration layer
│   │   ├── client.py                  # Direct interface with the local GGUF model via llama-cpp
│   │   ├── context.py                 # Chat history context window management and truncation
│   │   └── prompt.py                  # Prompt engineering and ChatML template formatting
│   │
│   ├── models/                        # SQLAlchemy ORM — defines the actual database tables
│   │   ├── feedback.py                # User feedback (thumbs up/down) for AI messages
│   │   ├── file.py                    # Metadata for uploaded files
│   │   ├── message.py                 # Individual chat messages (user, assistant, and tool roles)
│   │   ├── session.py                 # Chat sessions (groups messages together)
│   │   └── user.py                    # Registered users and authentication credentials
│   │
│   ├── routers/                       # FastAPI route handler files
│   │   ├── auth.py                    # POST /auth/register, /auth/login
│   │   ├── files.py                   # File upload and retrieval endpoints
│   │   ├── internal.py                # Internal admin and health check routes
│   │   ├── messages.py                # POST /messages — handles streaming & tool-call responses
│   │   └── sessions.py                # CRUD for chat sessions (create, list, delete)
│   │
│   ├── schemas/                       # Pydantic v2 — request/response validation shapes
│   │   ├── auth.py                    # User validation and JWT token schemas
│   │   ├── enums.py                   # Shared enumerations (MessageRole)
│   │   ├── feedback.py                # Feedback submission validation
│   │   ├── message.py                 # Message creation and response formatting
│   │   └── session.py                 # Session metadata schemas
│   │
│   ├── storage/                       # Persistent file storage
│   │   └── reports/                   # Output directory for generated forensic reports
│   │
│   ├── tools/                         # Agentic tool calling implementations
│   │   ├── registry.py                # Tool registry — maps tool names to handler functions
│   │   ├── report.py                  # Forensic report generation tool (write_report)
│   │   └── schemas.py                 # Pydantic schemas for tool call/result payloads
│   │
│   ├── Dockerfile                     # Container image definition for the backend service
│   ├── alembic.ini                    # Alembic configuration file
│   ├── main.py                        # FastAPI app entrypoint — registers routers, CORS, metrics
│   └── requirements.txt               # Python dependency list
│
├── frontend/                          # React 18 + Vite web application
│   ├── src/
│   │   ├── components/                # Page-level and reusable React UI components
│   │   │   ├── ChatWindow.jsx         # Main chat interface, displays message history
│   │   │   ├── Login.jsx              # User login form with error handling
│   │   │   ├── MessageBubble.jsx      # Individual chat bubble UI
│   │   │   ├── MessageInput.jsx       # Textarea for typing messages with auto-resize
│   │   │   ├── Register.jsx           # New account registration form
│   │   │   ├── Sidebar.jsx            # Left navigation: past sessions, new chat, logout
│   │   │   ├── ToolStatusBar.jsx      # Displays active tool call status during response
│   │   │   └── TypingIndicator.jsx    # Animated loading dots for AI response generation
│   │   │
│   │   ├── contexts/                  # Global React Context providers
│   │   │   └── AuthContext.jsx        # Manages global user authentication state and tokens
│   │   │
│   │   ├── hooks/                     # Custom React hooks
│   │   │   └── useAuth.js             # Exposes auth state and functions to components
│   │   │
│   │   ├── services/                  # Raw API call wrappers (fetch)
│   │   │   ├── api.js                 # Base API client with JWT interceptors
│   │   │   ├── authService.js         # login(), register(), refresh(), logout()
│   │   │   ├── fileService.js         # File upload service
│   │   │   ├── messageService.js      # streamMessage() via SSE, submitFeedback()
│   │   │   └── sessionService.js      # createSession(), listSessions(), getSession()
│   │   │
│   │   ├── App.css                    # Main stylesheet for layout and UI components
│   │   ├── App.jsx                    # Top-level router — coordinates screens based on auth state
│   │   ├── index.css                  # Global CSS variables, resets, and typography
│   │   └── main.jsx                   # React DOM root mount
│   │
│   ├── Dockerfile                     # Container image definition for the frontend service
│   ├── nginx.conf                     # Nginx config for serving the built frontend in Docker
│   ├── index.html                     # Vite HTML entry point
│   ├── package.json                   # npm dependencies & scripts
│   └── vite.config.js                 # Vite config (proxy routing to backend)
│
├── grafana/                           # Grafana observability configuration
│   ├── dashboards/
│   │   └── chatbot-dashboard.json     # Pre-built NexoBot metrics dashboard definition
│   └── provisioning/
│       ├── dashboards/
│       │   └── dashboard.yml          # Grafana dashboard provisioning config
│       └── datasources/
│           └── datasource.yml         # Prometheus datasource configuration for Grafana
│
├── notebook/                          # Model fine-tuning resources
│   └── Qwen3_(4B)_Instruct_cypersecurity.ipynb  # Fine-tuning notebook for the cybersecurity model
│
├── prometheus/                        # Prometheus monitoring configuration
│   └── prometheus.yml                 # Scrape configs and alerting rules
│
├── docker-compose.yml                 # Base Docker Compose — full stack orchestration
├── docker-compose.gpu.yml             # GPU override — enables CUDA for llama-cpp service
├── .env.example                       # Template for all required environment variables
├── alembic.ini                        # Root-level Alembic configuration alias
└── README.md                          # Project documentation
```

---

## ⚙️ Installation & Setup

Follow these steps to get NexoBot up and running locally with full GPU acceleration and observability.

### 1. Prerequisites
Ensure you have the following installed on your system before proceeding:
- **Python 3.13+**
- **Node.js 25.2.1+**
- **Docker & Docker Compose**
- **NVIDIA GPU & CUDA Toolkit:** Highly recommended for optimal performance.
  - Download the CUDA driver here: [CUDA Toolkit 12.4.0 Download](https://developer.nvidia.com/cuda-12-4-0-download-archive?target_os=Windows&target_arch=x86_64&target_version=11&target_type=exe_local)

### 2. Clone the Repository
Clone the project repository to your local machine and navigate into it:

```bash
git clone https://github.com/malekahmed99/NexoBot.git
cd NexoBot
```

### 3. Local Model Download
This application requires a local AI model fine-tuned for cybersecurity forensics.
1. Download the **Qwen cybersecurity `.gguf` model file** and its associated **JSON tokenizer files** from this [Hugging Face repository](https://huggingface.co/basilmh25/qwen_Cybersecucity).
2. Save these files in a designated folder on your machine (e.g., inside a `models/` directory).
3. **Important:** Make note of the *absolute paths* to both the `.gguf` file and the folder containing the tokenizer files, as you will need them for the environment configuration.

### 4. Environment Configuration (.env)
Create your environment variables file in the `backend/` directory by copying the provided example:

```bash
cd backend
cp .env.example .env
```

> [!CAUTION]
> You **must** open the new `.env` file and replace the placeholder values.

Specifically, ensure these variables are updated:
- `DB_URL`: The PostgreSQL connection string. If using Docker, point it to the postgres container.
- `MODEL_PATH`: The absolute file path to the `.gguf` model downloaded in Step 3.
- `TOKENIZER_PATH`: The absolute directory path to the folder containing the tokenizer JSON files from Step 3.
- `SECRET_KEY`: Replace the default string with a secure, random cryptographic string.
- `N_GPU_LAYERS`: Set this to **`-1`** to offload all layers to your GPU. This requires the CUDA driver installed from Step 1.

### 5. Running the Application via Docker

NexoBot is fully containerized, including the database, monitoring stack (Prometheus & Grafana), and backend/frontend services. 

To run the application with GPU acceleration, use both the base compose file and the GPU override:

```bash
# Return to the root directory
cd ..

# Start the full stack with GPU support
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

*(Note: If you do not have a GPU, you can run `docker-compose up --build` without the GPU override file, but performance will be severely degraded).*

Once the containers are up:
- **Frontend UI:** `http://localhost` (port 80)
- **Prometheus:** `http://localhost:9090`
- **Grafana:** `http://localhost:3001` — login: `admin` / `admin`

> **Note:** The backend API (`port 8000`) is internal to the Docker network and is not exposed to the host directly — the frontend communicates with it through Nginx inside the container.

---

## 👥 Team & Contributions

NexoBot was built as a team project for the **Digital Egypt Pioneers Initiative (DEPI) — AI & Data Science Track, Round 4** (Generative AI Projects booklet).

| Team Member | Role | Key Contributions |
|-------------|------|-------------------|
| **Malek Ahmed** | Frontend Engineer | Built the entire React frontend from scratch — authentication UI (Login/Register), JWT session management via React Context, real-time SSE message streaming, chat session management, file upload interface, Tool Status Bar, responsive sidebar, and the full Nginx + Docker containerization of the frontend service |
| **Basil Mohamed** | AI / ML Engineer | Fine-tuned the Qwen 3 (4B) model on a custom cybersecurity dataset, published the GGUF model on Hugging Face |
| **Ali Islam** | Backend Engineer | FastAPI application, PostgreSQL schema & Alembic migrations, agentic tool calling, Prometheus metrics, JWT auth backend |
